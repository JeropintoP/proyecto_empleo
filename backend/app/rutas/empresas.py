"""Empresas monitoreadas: listar, agregar, pausar, borrar y probar."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.db import obtener_sesion
from worker.empresas import EmpresaInvalida, probar, validar

router = APIRouter(tags=["empresas"])

Tipo = Literal["smartrecruiters", "successfactors", "pagina"]


class DatosEmpresa(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    tipo: Tipo
    identificador: str = Field(min_length=2, max_length=500)
    pais: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    solo_para_mi: bool = False


class CambioEmpresa(BaseModel):
    activa: bool


_COLUMNAS = """id, nombre, tipo::text AS tipo, identificador, pais, activa, usuario_id IS NOT NULL AS solo_para_mi,
               ultima_revision, ultimo_total, ultimo_error"""


def _visible(sesion: Session, empresa_id: int, usuario: dict) -> None:
    existe = sesion.execute(text("SELECT 1 FROM empresas WHERE id = :id AND (usuario_id IS NULL OR usuario_id = :u)"),
                            {"id": empresa_id, "u": usuario["id"]}).first()
    if not existe:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no encontrada")


def _validar(datos: DatosEmpresa) -> str:
    try:
        return validar(datos.tipo, datos.identificador)
    except EmpresaInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("/empresas")
def listar(usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion)) -> list[dict]:
    filas = sesion.execute(text(f"""SELECT {_COLUMNAS} FROM empresas
                                     WHERE usuario_id IS NULL OR usuario_id = :u ORDER BY lower(nombre)"""),
                           {"u": usuario["id"]}).mappings().all()
    return [dict(f) for f in filas]


@router.post("/empresas", status_code=status.HTTP_201_CREATED)
def agregar(datos: DatosEmpresa, usuario: dict = Depends(usuario_actual),
            sesion: Session = Depends(obtener_sesion)) -> dict:
    identificador = _validar(datos)
    try:
        fila = sesion.execute(text(f"""
            INSERT INTO empresas (nombre, tipo, identificador, pais, usuario_id)
            VALUES (:n, CAST(:t AS tipo_empresa), :i, lower(:p), :u)
            RETURNING {_COLUMNAS}"""),
            {"n": datos.nombre.strip(), "t": datos.tipo, "i": identificador, "p": datos.pais,
             "u": usuario["id"] if datos.solo_para_mi else None}).mappings().one()
        sesion.commit()
    except IntegrityError:
        sesion.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa empresa ya esta en la lista")
    return dict(fila)


@router.patch("/empresas/{empresa_id}")
def cambiar(empresa_id: int, cambio: CambioEmpresa, usuario: dict = Depends(usuario_actual),
            sesion: Session = Depends(obtener_sesion)) -> dict:
    _visible(sesion, empresa_id, usuario)
    fila = sesion.execute(text(f"UPDATE empresas SET activa = :a WHERE id = :id RETURNING {_COLUMNAS}"),
                          {"a": cambio.activa, "id": empresa_id}).mappings().one()
    sesion.commit()
    return dict(fila)


@router.delete("/empresas/{empresa_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar(empresa_id: int, usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion)) -> None:
    _visible(sesion, empresa_id, usuario)
    sesion.execute(text("DELETE FROM empresas WHERE id = :id"), {"id": empresa_id})
    sesion.commit()


@router.post("/empresas/probar")
def probar_empresa(datos: DatosEmpresa, usuario: dict = Depends(usuario_actual)) -> dict:
    """Consulta la empresa una vez y muestra lo que encontraria, sin guardar nada."""
    identificador = _validar(datos)
    empresa = {"id": 0, "nombre": datos.nombre, "tipo": datos.tipo, "identificador": identificador, "pais": datos.pais}
    try:
        vacantes = probar(empresa)
    except Exception as exc:
        return {"total": 0, "vacantes": [], "error": str(exc)[:300]}
    return {"total": len(vacantes), "error": None,
            "vacantes": [{"titulo": v.titulo, "url": v.url, "ubicacion": v.ubicacion} for v in vacantes[:25]]}
