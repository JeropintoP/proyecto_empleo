"""Tarea diaria de actualizacion de vacantes.

Por ahora solo deja la estructura y la bitacora en `ejecuciones`.
Cada recolector (Jooble, Careerjet, correo) se conecta en el siguiente paso.
"""
import logging
from collections.abc import Callable

from sqlalchemy import text

from app.db import sesion_transaccion

log = logging.getLogger("worker")

# codigo de fuente -> funcion recolectora. Se llena en el paso 3.
RECOLECTORES: dict[str, Callable[[], dict]] = {}


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
