import threading
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.db import obtener_sesion
from app.seguridad import crear_token, verificar_clave

router = APIRouter(tags=["sesion"])

# Bloqueo por intentos fallidos, por correo: 5 errores en 15 minutos bloquean ese correo 15 minutos.
# En memoria: basta para una sola instancia de la API.
MAX_FALLOS, VENTANA_SEG = 5, 15 * 60
_fallos: dict[str, list[float]] = {}
_candado = threading.Lock()


def _recientes(email: str, ahora: float) -> list[float]:
    return [t for t in _fallos.get(email, []) if ahora - t < VENTANA_SEG]


class Credenciales(BaseModel):
    email: str = Field(max_length=160)
    clave: str = Field(max_length=128)


@router.post("/auth/login")
def login(datos: Credenciales, sesion: Session = Depends(obtener_sesion)) -> dict:
    email, ahora = datos.email.strip().lower(), time.monotonic()
    with _candado:
        if len(_recientes(email, ahora)) >= MAX_FALLOS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Demasiados intentos fallidos. Espere 15 minutos e intente de nuevo.")
    fila = sesion.execute(
        text("SELECT id, nombre, password_hash FROM usuarios WHERE email = :e AND activo"), {"e": email},
    ).first()
    if fila is None or not verificar_clave(datos.clave, fila.password_hash):
        with _candado:
            _fallos[email] = _recientes(email, ahora) + [ahora]
        time.sleep(1)  # frena intentos repetidos
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo o clave incorrectos")
    with _candado:
        _fallos.pop(email, None)
    return {"token": crear_token(str(fila.id)), "nombre": fila.nombre}


@router.get("/yo")
def yo(usuario: dict = Depends(usuario_actual)) -> dict:
    return {**usuario, "id": str(usuario["id"])}
