"""Programador de tareas. Lee los horarios de la tabla `parametros` (ver worker/horario.py).

Ejecucion manual completa (sin esperar):
    docker compose exec worker python -m worker.main --ahora
"""
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db import sesion_transaccion
from worker import horario
from worker.tareas import actualizacion_diaria, actualizacion_programada

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")
logging.getLogger("httpx").setLevel(logging.WARNING)  # no registrar URLs (la de Jooble lleva la key)


def main() -> None:
    if "--ahora" in sys.argv:
        actualizacion_diaria()
        return

    with sesion_transaccion() as s:
        p = horario.parametros(s)
        corridas = horario.corridas_por_dia(s)
    programador = BlockingScheduler(timezone=p["zona"])
    programador.add_job(
        actualizacion_programada,
        CronTrigger.from_crontab(p["busqueda"], timezone=p["zona"]),
        id="busqueda",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,  # si el servidor estaba caido a la hora, corre al volver (hasta 30 min)
    )
    log.info("Worker iniciado. Busqueda: '%s' (%d corridas al dia); corrida completa: '%s' (%s)",
             p["busqueda"], corridas, p["completa"], p["zona"])
    programador.start()


if __name__ == "__main__":
    main()
