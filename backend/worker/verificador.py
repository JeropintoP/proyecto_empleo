"""Verificacion diaria de vacantes abiertas.

Una vacante se marca como cerrada cuando:
  - su enlace responde 404 o 410, o
  - la pagina muestra un aviso de oferta cerrada, o
  - lleva DIAS_SIN_VER dias sin aparecer en ninguna fuente (regla de antiguedad).

Solo se revisan vacantes que no han aparecido en los ultimos 3 dias: si siguen llegando
en alertas, estan abiertas. Si el portal bloquea la consulta (403, 429, 999 de LinkedIn),
no se concluye nada y se reintenta otro dia.
"""
import logging
import re
import time

import httpx
from sqlalchemy import text

from app.db import sesion_transaccion

log = logging.getLogger("worker.verificador")

DIAS_SIN_APARECER = 3     # antes de esto no se revisa el enlace
DIAS_SIN_VER = 45         # cierre por antiguedad
LIMITE_DIARIO = 80        # consultas por corrida (cortesia con los portales)
PAUSA_SEGUNDOS = 1.5

AVISOS_CERRADA = re.compile(
    r"ya no (est[aá] disponible|acepta (postulaciones|solicitudes))"
    r"|oferta (ha sido )?(cerrada|finalizada|vencida|expirada|caducada)"
    r"|vacante (cerrada|finalizada|no disponible)"
    r"|no longer accepting applications"
    r"|this job is no longer available",
    re.I,
)

CABECERAS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "es-CO,es;q=0.9",
}


def estado_enlace(cliente: httpx.Client, url: str) -> str:
    """Devuelve 'abierta', 'cerrada' o 'desconocido'."""
    try:
        r = cliente.get(url)
    except httpx.HTTPError:
        return "desconocido"
    if r.status_code in (404, 410):
        return "cerrada"
    if r.status_code != 200:
        return "desconocido"
    return "cerrada" if AVISOS_CERRADA.search(r.text[:300_000]) else "abierta"


def verificar(cliente: httpx.Client | None = None, pausa: float = PAUSA_SEGUNDOS) -> dict:
    resultado = {"revisadas": 0, "cerradas": 0, "cerradas_antiguedad": 0, "sin_respuesta": 0}

    with sesion_transaccion() as s:
        resultado["cerradas_antiguedad"] = s.execute(
            text("""UPDATE vacantes SET abierta = FALSE, cerrada_en = now()
                     WHERE abierta AND ultima_vez_vista < now() - make_interval(days => :d)"""),
            {"d": DIAS_SIN_VER},
        ).rowcount
        pendientes = s.execute(
            text("""SELECT id, url FROM vacantes
                     WHERE abierta
                       AND ultima_vez_vista < now() - make_interval(days => :d)
                       AND (verificada_en IS NULL OR verificada_en < now() - interval '20 hours')
                     ORDER BY verificada_en NULLS FIRST
                     LIMIT :n"""),
            {"d": DIAS_SIN_APARECER, "n": LIMITE_DIARIO},
        ).all()

    propio = cliente is None
    cliente = cliente or httpx.Client(headers=CABECERAS, timeout=20, follow_redirects=True)
    try:
        for i, (vacante_id, url) in enumerate(pendientes):
            if i and pausa:
                time.sleep(pausa)
            estado = estado_enlace(cliente, url)
            resultado["revisadas"] += 1
            if estado == "desconocido":
                resultado["sin_respuesta"] += 1
                continue
            with sesion_transaccion() as s:
                s.execute(
                    text("""UPDATE vacantes
                               SET verificada_en = now(),
                                   abierta = :abierta,
                                   cerrada_en = CASE WHEN :abierta THEN NULL ELSE now() END
                             WHERE id = :id"""),
                    {"id": vacante_id, "abierta": estado == "abierta"},
                )
            if estado == "cerrada":
                resultado["cerradas"] += 1
    finally:
        if propio:
            cliente.close()

    log.info("Verificacion: %s", resultado)
    return resultado
