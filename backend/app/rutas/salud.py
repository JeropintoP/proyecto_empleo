from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import obtener_sesion

router = APIRouter(tags=["salud"])


@router.get("/salud")
def salud(sesion: Session = Depends(obtener_sesion)) -> dict:
    """Comprueba que la API responde y que la base de datos esta disponible.
    Es publica: no expone fuentes ni errores (eso esta en /api/actualizar, con sesion)."""
    sesion.execute(text("SELECT 1"))
    return {"estado": "ok", "base_datos": "ok"}
