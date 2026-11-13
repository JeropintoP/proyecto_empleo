"""Programador de tareas. Lee el horario de la tabla `parametros`.

Ejecucion manual (sin esperar a las 6 a.m.):
    docker compose exec worker python -m worker.main --ahora
"""
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.db import sesion_transaccion
from worker.tareas import actualizacion_diaria

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")


def leer_parametros() -> tuple[str, str]:
    with sesion_transaccion() as s:
        p = dict(s.execute(text("SELECT clave, valor FROM parametros")).all())
    return p["horario_actualizacion"], p["zona_horaria"]


def main() -> None:
    if "--ahora" in sys.argv:
        actualizacion_diaria()
        return

    horario, zona = leer_parametros()
    programador = BlockingScheduler(timezone=zona)
    programador.add_job(
        actualizacion_diaria,
        CronTrigger.from_crontab(horario, timezone=zona),
        id="actualizacion_diaria",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,  # si el servidor estaba caido a las 6, corre al volver (hasta 1 h)
    )
    log.info("Worker iniciado. Actualizacion diaria: '%s' (%s)", horario, zona)
    programador.start()


if __name__ == "__main__":
    main()
