"""Cuenta del usuario: cambio de clave y actualizacion manual de vacantes."""
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.db import obtener_sesion
from app.seguridad import hash_clave, verificar_clave

log = logging.getLogger("api")
router = APIRouter(tags=["cuenta"])

MINUTOS_ENTRE_ACTUALIZACIONES = 10


class CambioClave(BaseModel):
    actual: str
    nueva: str = Field(min_length=10, max_length=128)


@router.post("/yo/clave", status_code=status.HTTP_204_NO_CONTENT)
def cambiar_clave(datos: CambioClave, usuario: dict = Depends(usuario_actual),
                  sesion: Session = Depends(obtener_sesion)) -> None:
    hash_actual = sesion.execute(text("SELECT password_hash FROM usuarios WHERE id = :u"),
                                 {"u": usuario["id"]}).scalar_one()
    if not verificar_clave(datos.actual, hash_actual):
        time.sleep(1)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La clave actual no es correcta")
    if datos.nueva == datos.actual:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La clave nueva debe ser distinta de la actual")
    sesion.execute(text("UPDATE usuarios SET password_hash = :h WHERE id = :u"),
                   {"h": hash_clave(datos.nueva), "u": usuario["id"]})
    sesion.commit()


def _estado(sesion: Session) -> dict:
    from worker.tareas import RECOLECTORES, actualizacion_en_curso

    fuentes = sesion.execute(text("""
        SELECT f.codigo, f.nombre, e.estado::text AS estado, e.fin, e.vacantes_nuevas, e.error
          FROM fuentes f
          LEFT JOIN LATERAL (SELECT * FROM ejecuciones x WHERE x.fuente_id = f.id
                              ORDER BY x.inicio DESC LIMIT 1) e ON TRUE
         WHERE f.activa ORDER BY f.id
    """)).mappings().all()
    return {
        "en_curso": actualizacion_en_curso(),
        "fuentes": [{**f, "configurada": f["codigo"] in RECOLECTORES,
                     "error": (f["error"] or "")[:200] or None} for f in fuentes],
    }


@router.get("/actualizar")
def estado_actualizacion(usuario: dict = Depends(usuario_actual),
                         sesion: Session = Depends(obtener_sesion)) -> dict:
    return _estado(sesion)


def _correr() -> None:
    from worker.tareas import actualizacion_diaria
    try:
        actualizacion_diaria(enviar_resumen=False, completa=False)
    except Exception:
        log.exception("Fallo la actualizacion manual")


@router.post("/actualizar", status_code=status.HTTP_202_ACCEPTED)
def actualizar_ahora(tareas: BackgroundTasks, usuario: dict = Depends(usuario_actual),
                     sesion: Session = Depends(obtener_sesion)) -> dict:
    estado = _estado(sesion)
    if estado["en_curso"]:
        return estado
    reciente = sesion.execute(text(
        "SELECT max(inicio) > now() - make_interval(mins => :m) FROM ejecuciones"),
        {"m": MINUTOS_ENTRE_ACTUALIZACIONES}).scalar()
    if reciente:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Ya se actualizo hace poco. Intente de nuevo en unos {MINUTOS_ENTRE_ACTUALIZACIONES} minutos.")
    tareas.add_task(_correr)
    return {**estado, "en_curso": True}
