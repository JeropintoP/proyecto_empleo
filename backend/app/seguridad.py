from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import config


def hash_clave(clave: str) -> str:
    return bcrypt.hashpw(clave.encode(), bcrypt.gensalt()).decode()


def verificar_clave(clave: str, hash_guardado: str) -> bool:
    return bcrypt.checkpw(clave.encode(), hash_guardado.encode())


def crear_token(usuario_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=config().jwt_horas)
    return jwt.encode({"sub": usuario_id, "exp": exp}, config().jwt_secreto, algorithm="HS256")


def leer_token(token: str) -> str:
    """Devuelve el usuario_id o lanza jwt.PyJWTError."""
    return jwt.decode(token, config().jwt_secreto, algorithms=["HS256"])["sub"]
