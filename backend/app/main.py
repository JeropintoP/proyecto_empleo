import logging

from fastapi import FastAPI

from app.config import config
from app.rutas import cuenta, empresas, perfil, salud, sesion, tablero

# httpx registra cada URL en nivel INFO; la de Jooble lleva la API key
logging.getLogger("httpx").setLevel(logging.WARNING)

_produccion = bool(config().dominio)
app = FastAPI(title="Proyecto Empleo", version="0.3.0",
              docs_url=None if _produccion else "/api/docs",
              openapi_url=None if _produccion else "/api/openapi.json", redoc_url=None)

for modulo in (salud, sesion, tablero, perfil, cuenta, empresas):
    app.include_router(modulo.router, prefix="/api")
