from fastapi import FastAPI

from app.rutas import salud

app = FastAPI(title="Proyecto Empleo", version="0.1.0")
app.include_router(salud.router, prefix="/api")
