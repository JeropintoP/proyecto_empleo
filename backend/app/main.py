from fastapi import FastAPI

from app.rutas import perfil, salud, sesion, tablero

app = FastAPI(title="Proyecto Empleo", version="0.2.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

for modulo in (salud, sesion, tablero, perfil):
    app.include_router(modulo.router, prefix="/api")
