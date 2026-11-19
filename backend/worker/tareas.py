"""Tarea diaria de actualizacion de vacantes.

Recorre las fuentes activas, ejecuta su recolector y deja la bitacora en `ejecuciones`.
"""
import logging
from collections.abc import Callable

from sqlalchemy import text

from app.config import config
from app.db import motor, sesion_transaccion
from app.ia.proveedor import ErrorIA
from app.ia.servicios import evaluar_pendientes
from worker.correo.recolector import recolectar as recolectar_correo
from worker.notificar import enviar_resumenes
from worker.recolectores_api import recolectar_careerjet, recolectar_jooble
from worker.verificador import verificar

log = logging.getLogger("worker")

# codigo de fuente -> funcion recolectora
RECOLECTORES: dict[str, Callable[[], dict]] = {"correo": recolectar_correo}
if config().jooble_api_key:
    RECOLECTORES["jooble"] = recolectar_jooble
if config().careerjet_api_key:
    RECOLECTORES["careerjet"] = recolectar_careerjet


# Bloqueo en PostgreSQL: garantiza una sola actualizacion a la vez, aunque la pidan
# el worker (6 a.m.) y la API (boton "Actualizar ahora") desde contenedores distintos.
LLAVE_BLOQUEO = 7_315_001


def actualizacion_diaria(enviar_resumen: bool = True) -> bool:
    """Ejecuta la actualizacion completa. Devuelve False si ya habia otra en curso."""
    with motor.connect() as conexion:
        if not conexion.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LLAVE_BLOQUEO}).scalar():
            log.info("Ya hay una actualizacion en curso; se omite esta")
            return False
        try:
            _ejecutar(enviar_resumen)
        finally:
            conexion.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LLAVE_BLOQUEO})
            conexion.commit()
    return True


def actualizacion_en_curso() -> bool:
    with sesion_transaccion() as s:
        return s.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND objid = :k)"),
            {"k": LLAVE_BLOQUEO},
        ).scalar()


def _ejecutar(enviar_resumen: bool) -> None:
    with sesion_transaccion() as s:
        fuentes = s.execute(
            text("SELECT id, codigo FROM fuentes WHERE activa ORDER BY id")
        ).all()

    for fuente_id, codigo in fuentes:
        recolector = RECOLECTORES.get(codigo)
        if recolector is None:  # p. ej. Jooble/Careerjet mientras no hay API key
            log.info("Fuente %s sin recolector configurado; se omite", codigo)
            continue

        with sesion_transaccion() as s:
            ejecucion_id = s.execute(
                text("INSERT INTO ejecuciones (fuente_id) VALUES (:f) RETURNING id"),
                {"f": fuente_id},
            ).scalar_one()

        try:
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

    _paso("Verificacion de vacantes cerradas", verificar)
    _paso("Evaluacion IA", evaluar_pendientes)   # puntaje para nuevas o reprocesadas sin evaluar
    if enviar_resumen:  # en una actualizacion manual el usuario ya esta mirando el tablero
        _paso("Resumen por correo", enviar_resumenes)


def _paso(nombre: str, funcion: Callable[[], dict]) -> None:
    """Cada paso posterior es independiente: si uno falla, los demas siguen."""
    try:
        log.info("%s: %s", nombre, funcion())
    except ErrorIA as exc:
        log.error("%s: IA no disponible: %s", nombre, exc)
    except Exception:
        log.exception("%s: fallo", nombre)
