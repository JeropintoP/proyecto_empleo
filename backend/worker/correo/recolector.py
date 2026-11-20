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
import re
import unicodedata
from email.message import EmailMessage
from email.utils import parseaddr

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import config
from app.db import sesion_transaccion
from worker.correo.imap import alias_destino, mensajes_recientes
from worker.correo.lectores import lector_para, remitente_original
from worker.correo.modelos import VacanteExtraida

log = logging.getLogger("worker.correo")

# Ventana de lectura del buzon: desde la ultima corrida exitosa (con 1 dia de margen),
# nunca menos de 3 dias (regla de reproceso) ni mas de 30 (si el equipo estuvo apagado mucho tiempo).
DIAS_MIN, DIAS_MAX, DIAS_SIN_HISTORIAL = 3, 30, 14


def dias_a_leer(s: Session, fuente_id: int) -> int:
    dias = s.execute(
        text("""SELECT ceil(EXTRACT(EPOCH FROM now() - max(fin)) / 86400) + 1
                  FROM ejecuciones WHERE fuente_id = :f AND estado = 'ok'"""),
        {"f": fuente_id},
    ).scalar()
    if dias is None:
        return DIAS_SIN_HISTORIAL
    return int(min(max(dias, DIAS_MIN), DIAS_MAX))


def _corte(texto: str | None, maximo: int) -> str | None:
    return texto if texto is None or len(texto) <= maximo else texto[: maximo - 1] + "…"


def _id_corto(id_externo: str) -> str:
    """id_externo admite 200 caracteres; los mas largos (URL) se resumen con un hash estable."""
    if len(id_externo) <= 200:
        return id_externo
    return id_externo[:160] + ":" + hashlib.md5(id_externo.encode()).hexdigest()


def _plano(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in t if not unicodedata.combining(c))))


_SOCIEDAD = {"sas", "sa", "ltda", "inc", "bic", "llc", "sl", "cia", "y"}


def _empresa(nombre: str | None) -> str:
    """'Astilleros S.A.S.' y 'ASTILLEROS SAS BIC' -> 'astilleros'."""
    return " ".join(t for t in _plano((nombre or "").replace(".", "")).split() if t not in _SOCIEDAD)


def _huella(v: VacanteExtraida) -> str:
    """Identifica la misma oferta publicada en varios portales: cargo + empresa (sin tildes ni signos).
    Sin empresa no se puede comparar entre portales, asi que se incluye el portal."""
    empresa = _empresa(v.empresa)
    base = f"{_plano(v.titulo)}|{empresa}" if empresa else f"{_plano(v.titulo)}||{v.portal}:{v.id_portal}"
    return hashlib.md5(base.encode()).hexdigest()


def _ya_la_tiene(s: Session, usuario_id: str, vacante_id, huella: str) -> bool:
    """El usuario ya tiene esta misma oferta (abierta) llegada por otro portal.
    Si es asi, la original cuenta como vista hoy (sigue publicada)."""
    originales = s.execute(text("""SELECT v.id FROM coincidencias c JOIN vacantes v ON v.id = c.vacante_id
                                     WHERE c.usuario_id = :u AND v.huella = :h AND v.abierta AND v.id <> :v"""),
                           {"u": usuario_id, "h": huella, "v": vacante_id}).scalars().all()
    if originales:
        s.execute(text("UPDATE vacantes SET ultima_vez_vista = now() WHERE id = ANY(:ids)"), {"ids": list(originales)})
    return bool(originales)


def guardar_vacante(s: Session, fuente_id: int, usuario_id: str, v: VacanteExtraida) -> str:
    """Devuelve 'nueva', 'reprocesada', 'omitida' o 'duplicada' (la misma oferta ya le llego por otro portal)."""
    existente = s.execute(
        text("""SELECT id, vacante_reprocesable(id) AS repro
                  FROM vacantes WHERE fuente_id = :f AND id_externo = :e"""),
        {"f": fuente_id, "e": _id_corto(v.id_externo)},
    ).first()

    if existente is None:
        vacante_id = s.execute(
            text("""
                INSERT INTO vacantes (fuente_id, id_externo, titulo, empresa, ubicacion, modalidad,
                                      salario_texto, salario_min, salario_max, url,
                                      fecha_publicacion, descripcion, huella, datos_originales)
                VALUES (:f, :e, :titulo, :empresa, :ubicacion, CAST(:modalidad AS modalidad_trabajo),
                        :sal_txt, :sal_min, :sal_max, :url, :fecha, :desc, :huella, CAST(:datos AS jsonb))
                RETURNING id
            """),
            {"f": fuente_id, "e": _id_corto(v.id_externo), "titulo": _corte(v.titulo, 300), "empresa": _corte(v.empresa, 200),
             "ubicacion": _corte(v.ubicacion, 200), "modalidad": v.modalidad, "sal_txt": _corte(v.salario_texto, 120),
             "sal_min": v.salario_min, "sal_max": v.salario_max, "url": v.url,
             "fecha": v.fecha_publicacion, "desc": v.descripcion, "huella": _huella(v),
             "datos": json.dumps({"portal": v.portal, **v.extra}, ensure_ascii=False)},
        ).scalar_one()
        if _ya_la_tiene(s, usuario_id, vacante_id, _huella(v)):
            return "duplicada"   # se guarda la vacante, pero no se le muestra dos veces ni se evalua de nuevo
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
    tiene = s.execute(text("SELECT EXISTS (SELECT 1 FROM coincidencias WHERE usuario_id = :u AND vacante_id = :v)"),
                      {"u": usuario_id, "v": vacante_id}).scalar()
    if not tiene and _ya_la_tiene(s, usuario_id, vacante_id, _huella(v)):
        return "duplicada"
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


def _suplantado(msg: EmailMessage) -> bool:
    """Gmail anota en Authentication-Results si el remitente es autentico. Un 'dmarc=fail'
    indica que alguien uso el nombre del portal sin serlo (posible enlace malicioso)."""
    return any(re.search(r"\bdmarc=fail\b", str(h), re.I) for h in msg.get_all("Authentication-Results", []) or [])


def procesar_mensaje(s: Session, fuente_id: int, msg: EmailMessage, alias: str | None = None) -> dict:
    remitente = str(msg.get("From", ""))
    if _suplantado(msg):
        return {"omitido": "remitente no autenticado (DMARC fallido)"}
    lector = lector_para(remitente)
    reenvio_manual = False
    if lector is None:
        original = remitente_original(msg)
        lector = lector_para(original) if original else None
        if lector is None:
            return {"omitido": "remitente no reconocido"}
        reenvio_manual = True

    alias = alias or alias_destino(msg, config().imap_usuario)
    usuario = s.execute(
        text("SELECT id, email FROM usuarios WHERE alias_correo = :a AND activo"), {"a": alias}
    ).first() if alias else None
    if usuario is None:
        return {"omitido": f"alias sin usuario: {alias}"}
    usuario_id = usuario.id

    # Un reenvio manual solo se acepta si lo envia el propio usuario desde su correo registrado
    if reenvio_manual and parseaddr(remitente)[1].lower() != usuario.email.lower():
        return {"omitido": "reenvio manual desde un correo que no es el del usuario"}

    vacantes = lector(msg)
    conteo = {"nueva": 0, "reprocesada": 0, "omitida": 0, "duplicada": 0}
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
    total = {"leidas": 0, "nuevas": 0, "reprocesadas": 0, "correos": 0, "omitidos": 0, "errores": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = 'correo'")).scalar_one()
        dias = dias_a_leer(s, fuente_id)
    log.info("Leyendo el buzon: ultimos %s dias", dias)
    total["dias_leidos"] = dias

    for msg in mensajes_recientes(dias):
        try:
            with sesion_transaccion() as s:  # un correo con error no afecta a los demas
                r = procesar_mensaje(s, fuente_id, msg)
        except Exception:
            log.exception("Error procesando una alerta de %s", str(msg.get("From", ""))[:60])
            total["errores"] += 1
            continue
        if "omitido" in r:
            # No se registra el asunto: el buzon puede recibir correos personales por error
            total["omitidos"] += 1
            if r["omitido"] != "remitente no reconocido":
                log.warning("Alerta omitida: %s", r["omitido"])
            continue
        total["correos"] += 1
        for k in ("leidas", "nuevas", "reprocesadas"):
            total[k] += r[k]
    return total
