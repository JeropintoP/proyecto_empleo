import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.db import obtener_sesion
from app.seguridad import crear_token, verificar_clave

router = APIRouter(tags=["sesion"])


class Credenciales(BaseModel):
    email: str
    clave: str


@router.post("/auth/login")
def login(datos: Credenciales, sesion: Session = Depends(obtener_sesion)) -> dict:
    fila = sesion.execute(
        text("SELECT id, nombre, password_hash FROM usuarios WHERE email = lower(:e) AND activo"),
        {"e": datos.email.strip()},
    ).first()
    if fila is None or not verificar_clave(datos.clave, fila.password_hash):
        time.sleep(1)  # frena intentos repetidos
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo o clave incorrectos")
    return {"token": crear_token(str(fila.id)), "nombre": fila.nombre}


@router.get("/yo")
def yo(usuario: dict = Depends(usuario_actual)) -> dict:
    return {**usuario, "id": str(usuario["id"])}
