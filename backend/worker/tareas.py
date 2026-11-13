"""Tarea diaria de actualizacion de vacantes.

Recorre las fuentes activas, ejecuta su recolector y deja la bitacora en `ejecuciones`.
"""
import logging
from collections.abc import Callable

from sqlalchemy import text

from app.db import sesion_transaccion
from app.ia.proveedor import ErrorIA
from app.ia.servicios import evaluar_pendientes
from worker.correo.recolector import recolectar as recolectar_correo

log = logging.getLogger("worker")

# codigo de fuente -> funcion recolectora
RECOLECTORES: dict[str, Callable[[], dict]] = {
    "correo": recolectar_correo,
    # "jooble": ...,     pendiente de API key
    # "careerjet": ...,  pendiente de API key
}


def actualizacion_diaria() -> None:
    with sesion_transaccion() as s:
        fuentes = s.execute(
            text("SELECT id, codigo FROM fuentes WHERE activa ORDER BY id")
        ).all()

    for fuente_id, codigo in fuentes:
        with sesion_transaccion() as s:
            ejecucion_id = s.execute(
                text("INSERT INTO ejecuciones (fuente_id) VALUES (:f) RETURNING id"),
                {"f": fuente_id},
            ).scalar_one()

        recolector = RECOLECTORES.get(codigo)
        try:
            if recolector is None:
                raise NotImplementedError(f"Recolector '{codigo}' aun no implementado")
            resultado = recolector()
            estado, error = "ok", None
        except Exception as exc:  # una fuente caida no detiene las demas
            log.exception("Fallo la fuente %s", codigo)
            resultado, estado, error = {}, "error", str(exc)

        with sesion_transaccion() as s:
            s.execute(
                text("""
                    UPDATE ejecuciones
                       SET fin = now(), estado = :estado, error = :error,
                           vacantes_leidas = :leidas, vacantes_nuevas = :nuevas,
                           vacantes_reprocesadas = :repro, vacantes_cerradas = :cerradas
                     WHERE id = :id
                """),
                {
                    "id": ejecucion_id, "estado": estado, "error": error,
                    "leidas": resultado.get("leidas", 0),
                    "nuevas": resultado.get("nuevas", 0),
                    "repro": resultado.get("reprocesadas", 0),
                    "cerradas": resultado.get("cerradas", 0),
                },
            )
            s.execute(text("UPDATE fuentes SET ultima_ejecucion = now() WHERE id = :f"), {"f": fuente_id})

        log.info("Fuente %s: %s %s", codigo, estado, resultado)

    # Despues de recolectar: puntaje con IA para las coincidencias nuevas o reprocesadas sin evaluar
    try:
        log.info("Evaluacion IA: %s", evaluar_pendientes())
    except ErrorIA as exc:
        log.error("Evaluacion IA no disponible: %s", exc)
