import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import obtener_sesion
from app.seguridad import leer_token

_bearer = HTTPBearer(auto_error=False)


def usuario_actual(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    sesion: Session = Depends(obtener_sesion),
) -> dict:
    no_autorizado = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesion invalida o vencida")
    if cred is None:
        raise no_autorizado
    try:
        usuario_id = leer_token(cred.credentials)
    except jwt.PyJWTError:
        raise no_autorizado
    usuario = sesion.execute(
        text("SELECT id, nombre, email, alias_correo FROM usuarios WHERE id = :id AND activo"),
        {"id": usuario_id},
    ).mappings().first()
    if usuario is None:
        raise no_autorizado
    return dict(usuario)
