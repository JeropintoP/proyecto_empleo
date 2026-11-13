from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import obtener_sesion

router = APIRouter(tags=["salud"])


@router.get("/salud")
def salud(sesion: Session = Depends(obtener_sesion)) -> dict:
    """Comprueba que la API responde y que la base de datos esta disponible."""
    fuentes = sesion.execute(
        text("""
            SELECT f.codigo, f.activa, f.horario,
                   (SELECT e.estado FROM ejecuciones e
                     WHERE e.fuente_id = f.id ORDER BY e.inicio DESC LIMIT 1) AS ultima
              FROM fuentes f ORDER BY f.id
        """)
    ).mappings().all()
    return {"estado": "ok", "base_datos": "ok", "fuentes": [dict(f) for f in fuentes]}
