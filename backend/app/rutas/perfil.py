import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.db import obtener_sesion

router = APIRouter(tags=["perfil"])

_CAMPOS = """resumen, cargos_objetivo, habilidades, anios_experiencia, nivel_educativo, idiomas,
             ciudades, modalidad::text AS modalidad, salario_minimo, palabras_excluir,
             cv_archivo, modelo_ia, actualizado_en"""


def _leer(sesion: Session, usuario_id) -> dict:
    perfil = dict(sesion.execute(
        text(f"SELECT {_CAMPOS} FROM perfiles WHERE usuario_id = :u"), {"u": usuario_id}
    ).mappings().one())
    if perfil["anios_experiencia"] is not None:
        perfil["anios_experiencia"] = float(perfil["anios_experiencia"])
    perfil["busquedas"] = [dict(b) for b in sesion.execute(
        text("""SELECT DISTINCT b.palabras_clave, b.ubicacion FROM busquedas b
                  JOIN perfiles p ON p.id = b.perfil_id
                 WHERE p.usuario_id = :u AND b.activa ORDER BY 1"""),
        {"u": usuario_id},
    ).mappings().all()]
    return perfil


@router.get("/perfil")
def ver(usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion)) -> dict:
    return _leer(sesion, usuario["id"])


class Preferencias(BaseModel):
    cargos_objetivo: list[str] | None = Field(None, max_length=15)
    ciudades: list[str] | None = Field(None, max_length=10)
    modalidad: Literal["presencial", "remoto", "hibrido", "indiferente"] | None = None
    salario_minimo: int | None = Field(None, ge=0)
    palabras_excluir: list[str] | None = Field(None, max_length=30)


@router.put("/perfil")
def actualizar(
    datos: Preferencias, usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion),
) -> dict:
    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise HTTPException(400, "Nada que actualizar")
    sets = [("modalidad = CAST(:modalidad AS modalidad_trabajo)" if k == "modalidad" else f"{k} = :{k}")
            for k in cambios]
    for k, v in cambios.items():
        if isinstance(v, list):
            cambios[k] = [x.strip() for x in v if x and x.strip()]
    sesion.execute(
        text(f"UPDATE perfiles SET {', '.join(sets)}, actualizado_en = now() WHERE usuario_id = :u"),
        {**cambios, "u": usuario["id"]},
    )
    sesion.commit()
    return _leer(sesion, usuario["id"])


@router.post("/perfil/cv")
def subir_cv(
    archivo: UploadFile = File(...),
    usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion),
) -> dict:
    from app.ia.cv import texto_de_cv
    from app.ia.proveedor import ErrorIA
    from app.ia.servicios import generar_busquedas, perfilar_cv

    ext = Path(archivo.filename or "").suffix.lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(400, "Use un archivo PDF o Word (.docx)")
    contenido = archivo.file.read(5 * 1024 * 1024 + 1)
    if len(contenido) > 5 * 1024 * 1024:
        raise HTTPException(400, "El archivo supera 5 MB")

    with tempfile.NamedTemporaryFile(suffix=ext) as tmp:  # el archivo no se conserva, solo su texto
        tmp.write(contenido)
        tmp.flush()
        try:
            texto = texto_de_cv(tmp.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    try:
        perfilar_cv(usuario["id"], texto, archivo.filename)
        generar_busquedas(usuario["id"])
    except ErrorIA as exc:
        raise HTTPException(503, f"La IA no esta disponible en este momento: {exc}")
    return _leer(sesion, usuario["id"])
