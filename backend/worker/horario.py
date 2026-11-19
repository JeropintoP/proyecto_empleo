"""Horarios de la actualizacion, leidos de la tabla `parametros`.

- horario_busqueda       (cron): cuando se buscan vacantes. Por defecto cada 3 horas: '0 */3 * * *'.
- horario_actualizacion  (cron): la corrida "completa" del dia (6 a.m.): ademas de buscar, verifica
                                 las vacantes cerradas y envia el resumen por correo.
                                 Debe coincidir con una de las horas de horario_busqueda.
"""
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text


def parametros(s) -> dict[str, str]:
    p = dict(s.execute(text("SELECT clave, valor FROM parametros")).all())
    return {
        "busqueda": p.get("horario_busqueda") or p.get("horario_actualizacion", "0 6 * * *"),
        "completa": p.get("horario_actualizacion", "0 6 * * *"),
        "zona": p.get("zona_horaria", "America/Bogota"),
    }


def _disparos(cron: str, zona: str, desde: datetime, hasta: datetime) -> list[datetime]:
    trigger, resultado = CronTrigger.from_crontab(cron, timezone=zona), []
    momento = trigger.get_next_fire_time(None, desde)
    while momento and momento < hasta and len(resultado) < 1440:
        resultado.append(momento)
        momento = trigger.get_next_fire_time(momento, momento + timedelta(seconds=1))
    return resultado


def corridas_por_dia(s) -> int:
    """Cuantas veces corre la busqueda en 24 h (para repartir los cupos diarios de las APIs)."""
    p = parametros(s)
    from zoneinfo import ZoneInfo
    ahora = datetime.now(ZoneInfo(p["zona"]))
    return max(1, len(_disparos(p["busqueda"], p["zona"], ahora, ahora + timedelta(days=1))))


def es_corrida_completa(s, momento: datetime) -> bool:
    """True si `momento` (hora de disparo de la busqueda) es tambien la hora de la corrida completa."""
    p = parametros(s)
    inicio = momento.replace(second=0, microsecond=0)
    return bool(_disparos(p["completa"], p["zona"], inicio - timedelta(minutes=1), inicio + timedelta(minutes=1)))
