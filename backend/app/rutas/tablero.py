from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.db import obtener_sesion

router = APIRouter(tags=["tablero"])

Estado = Literal["nueva", "vista", "guardada", "postulada", "descartada"]


@router.get("/resumen")
def resumen(usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion)) -> dict:
    por_estado = dict(sesion.execute(
        text("SELECT estado::text, count(*) FROM coincidencias WHERE usuario_id = :u GROUP BY estado"),
        {"u": usuario["id"]},
    ).all())
    pendientes = sesion.execute(
        text("SELECT count(*) FROM coincidencias WHERE usuario_id = :u AND puntaje IS NULL"),
        {"u": usuario["id"]},
    ).scalar()
    ultima = sesion.execute(text("SELECT max(fin) FROM ejecuciones WHERE estado = 'ok'")).scalar()
    return {"por_estado": por_estado, "sin_evaluar": pendientes, "ultima_actualizacion": ultima}


@router.get("/coincidencias")
def listar(
    estado: Estado | None = None,
    min_puntaje: int = Query(0, ge=0, le=100),
    buscar: str | None = Query(None, max_length=80),
    orden: Literal["puntaje", "fecha"] = "puntaje",
    limite: int = Query(50, ge=1, le=200),
    desplazamiento: int = Query(0, ge=0),
    usuario: dict = Depends(usuario_actual),
    sesion: Session = Depends(obtener_sesion),
) -> dict:
    filtros = ["c.usuario_id = :u", "COALESCE(c.puntaje, 0) >= :min"]
    params: dict = {"u": usuario["id"], "min": min_puntaje, "lim": limite, "off": desplazamiento}
    if estado:
        filtros.append("c.estado = CAST(:estado AS estado_coincidencia)")
        params["estado"] = estado
    else:
        filtros.append("c.estado <> 'descartada'")
    if buscar:
        filtros.append("(v.titulo ILIKE :q OR v.empresa ILIKE :q OR v.ubicacion ILIKE :q)")
        params["q"] = f"%{buscar}%"
    orden_sql = ("c.puntaje DESC NULLS LAST, COALESCE(c.reaparecio_en, c.creada_en) DESC"
                 if orden == "puntaje" else "COALESCE(c.reaparecio_en, c.creada_en) DESC")
    where = " AND ".join(filtros)

    filas = sesion.execute(
        text(f"""
            SELECT c.id, c.puntaje, c.explicacion, c.fortalezas, c.brechas, c.estado::text AS estado,
                   c.notas, c.veces_mostrada, c.creada_en, c.reaparecio_en, c.postulada_en,
                   v.titulo, v.empresa, v.ubicacion, v.modalidad::text AS modalidad, v.salario_texto,
                   v.url, v.fecha_publicacion, v.abierta, v.datos_originales->>'portal' AS portal
              FROM coincidencias c JOIN vacantes v ON v.id = c.vacante_id
             WHERE {where}
             ORDER BY {orden_sql}
             LIMIT :lim OFFSET :off
        """),
        params,
    ).mappings().all()
    total = sesion.execute(
        text(f"SELECT count(*) FROM coincidencias c JOIN vacantes v ON v.id = c.vacante_id WHERE {where}"),
        params,
    ).scalar()
    return {"total": total, "items": [{**f, "id": str(f["id"])} for f in filas]}


class Cambio(BaseModel):
    estado: Estado | None = None
    notas: str | None = None


@router.patch("/coincidencias/{coincidencia_id}")
def actualizar(
    coincidencia_id: str, cambio: Cambio,
    usuario: dict = Depends(usuario_actual), sesion: Session = Depends(obtener_sesion),
) -> dict:
    sets, params = [], {"id": coincidencia_id, "u": usuario["id"]}
    if cambio.estado:
        sets.append("estado = CAST(:estado AS estado_coincidencia)")
        sets.append("postulada_en = CASE WHEN :estado = 'postulada' THEN now() ELSE postulada_en END")
        params["estado"] = cambio.estado
    if cambio.notas is not None:
        sets.append("notas = :notas")
        params["notas"] = cambio.notas[:2000]
    if not sets:
        raise HTTPException(400, "Nada que actualizar")
    fila = sesion.execute(
        text(f"UPDATE coincidencias SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) AND usuario_id = :u "
             "RETURNING estado::text, notas"),
        params,
    ).first()
    if fila is None:
        raise HTTPException(404, "No encontrada")
    sesion.commit()
    return {"estado": fila.estado, "notas": fila.notas}
