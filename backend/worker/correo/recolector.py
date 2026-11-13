"""Recolector de alertas por correo.

Flujo por cada mensaje:
  1. Identifica al usuario por el alias (+jeronimopinto / +helenvisbal).
  2. Elige el lector segun el portal remitente.
  3. Guarda cada vacante aplicando la regla de reproceso (abierta + 3 dias).
  4. Crea o reactiva la coincidencia para ese usuario.
  5. Registra el correo en `correos_procesados` (se permite repetir).
"""
import hashlib
import json
import logging
from email.message import EmailMessage

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import config
from app.db import sesion_transaccion
from worker.correo.imap import alias_destino, mensajes_recientes
from worker.correo.lectores import lector_para
from worker.correo.modelos import VacanteExtraida

log = logging.getLogger("worker.correo")

DIAS_BUZON = 3  # ventana de lectura del buzon en cada corrida diaria


def _huella(v: VacanteExtraida) -> str:
    base = "|".join((v.titulo or "", v.empresa or "", v.ubicacion or "")).lower()
    return hashlib.md5(base.encode()).hexdigest()


def guardar_vacante(s: Session, fuente_id: int, usuario_id: str, v: VacanteExtraida) -> str:
    """Devuelve 'nueva', 'reprocesada' u 'omitida'."""
    existente = s.execute(
        text("""SELECT id, vacante_reprocesable(id) AS repro
                  FROM vacantes WHERE fuente_id = :f AND id_externo = :e"""),
        {"f": fuente_id, "e": v.id_externo},
    ).first()

    if existente is None:
        vacante_id = s.execute(
            text("""
                INSERT INTO vacantes (fuente_id, id_externo, titulo, empresa, ubicacion, modalidad,
                                      salario_texto, salario_min, salario_max, url,
                                      fecha_publicacion, huella, datos_originales)
                VALUES (:f, :e, :titulo, :empresa, :ubicacion, CAST(:modalidad AS modalidad_trabajo),
                        :sal_txt, :sal_min, :sal_max, :url, :fecha, :huella, CAST(:datos AS jsonb))
                RETURNING id
            """),
            {"f": fuente_id, "e": v.id_externo, "titulo": v.titulo, "empresa": v.empresa,
             "ubicacion": v.ubicacion, "modalidad": v.modalidad, "sal_txt": v.salario_texto,
             "sal_min": v.salario_min, "sal_max": v.salario_max, "url": v.url,
             "fecha": v.fecha_publicacion, "huella": _huella(v),
             "datos": json.dumps({"portal": v.portal, **v.extra}, ensure_ascii=False)},
        ).scalar_one()
        s.execute(
            text("INSERT INTO coincidencias (usuario_id, vacante_id) VALUES (:u, :v) ON CONFLICT DO NOTHING"),
            {"u": usuario_id, "v": vacante_id},
        )
        return "nueva"

    vacante_id, reprocesable = existente
    # Siempre: sigue publicada en el portal
    s.execute(
        text("UPDATE vacantes SET ultima_vez_vista = now(), abierta = TRUE, cerrada_en = NULL WHERE id = :v"),
        {"v": vacante_id},
    )

    # Si el otro usuario aun no la tenia, se le crea su coincidencia
    creada = s.execute(
        text("""INSERT INTO coincidencias (usuario_id, vacante_id) VALUES (:u, :v)
                ON CONFLICT DO NOTHING RETURNING id"""),
        {"u": usuario_id, "v": vacante_id},
    ).first()
    if creada:
        return "nueva"

    if not reprocesable:
        return "omitida"

    s.execute(text("UPDATE vacantes SET procesada_en = now() WHERE id = :v"), {"v": vacante_id})
    s.execute(
        text("""
            UPDATE coincidencias
               SET veces_mostrada = veces_mostrada + 1,
                   reaparecio_en  = now(),
                   estado = CASE WHEN estado IN ('postulada', 'descartada') THEN estado ELSE 'nueva' END
             WHERE usuario_id = :u AND vacante_id = :v
        """),
        {"u": usuario_id, "v": vacante_id},
    )
    return "reprocesada"


def procesar_mensaje(s: Session, fuente_id: int, msg: EmailMessage, alias: str | None = None) -> dict:
    remitente = str(msg.get("From", ""))
    lector = lector_para(remitente)
    if lector is None:
        return {"omitido": "remitente no reconocido"}

    alias = alias or alias_destino(msg, config().imap_usuario)
    usuario_id = s.execute(
        text("SELECT id FROM usuarios WHERE alias_correo = :a AND activo"), {"a": alias}
    ).scalar() if alias else None
    if usuario_id is None:
        return {"omitido": f"alias sin usuario: {alias}"}

    vacantes = lector(msg)
    conteo = {"nueva": 0, "reprocesada": 0, "omitida": 0}
    for v in vacantes:
        conteo[guardar_vacante(s, fuente_id, usuario_id, v)] += 1

    s.execute(
        text("""
            INSERT INTO correos_procesados (message_id, usuario_id, remitente, asunto,
                                            vacantes_extraidas, vacantes_nuevas, vacantes_reprocesadas)
            VALUES (:mid, :u, :rem, :asunto, :ext, :nuevas, :repro)
        """),
        {"mid": str(msg.get("Message-ID", ""))[:300], "u": usuario_id, "rem": remitente[:200],
         "asunto": str(msg.get("Subject", ""))[:300], "ext": len(vacantes),
         "nuevas": conteo["nueva"], "repro": conteo["reprocesada"]},
    )
    return {"leidas": len(vacantes), "nuevas": conteo["nueva"], "reprocesadas": conteo["reprocesada"]}


def recolectar() -> dict:
    """Punto de entrada para el worker (fuente 'correo')."""
    total = {"leidas": 0, "nuevas": 0, "reprocesadas": 0, "correos": 0, "errores": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = 'correo'")).scalar_one()

    for msg in mensajes_recientes(DIAS_BUZON):
        try:
            with sesion_transaccion() as s:  # un correo con error no afecta a los demas
                r = procesar_mensaje(s, fuente_id, msg)
        except Exception:
            log.exception("Error procesando %s", msg.get("Subject"))
            total["errores"] += 1
            continue
        if "omitido" in r:
            log.info("Omitido '%s': %s", msg.get("Subject"), r["omitido"])
            continue
        total["correos"] += 1
        for k in ("leidas", "nuevas", "reprocesadas"):
            total[k] += r[k]
    return total
