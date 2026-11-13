from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import config

motor = create_engine(config().database_url, pool_pre_ping=True)
SesionLocal = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


def obtener_sesion() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesion por peticion."""
    with SesionLocal() as sesion:
        yield sesion


@contextmanager
def sesion_transaccion() -> Iterator[Session]:
    """Para el worker y la CLI: commit al final o rollback si hay error."""
    with SesionLocal() as sesion:
        with sesion.begin():
            yield sesion
