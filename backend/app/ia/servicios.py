"""Tareas de IA sobre la base de datos: perfilar CV, generar busquedas y evaluar vacantes."""
import json
import logging
import unicodedata

from sqlalchemy import text

from app.db import sesion_transaccion
from app.ia import prompts
from app.ia.proveedor import ErrorIA, Proveedor, consultar

log = logging.getLogger("ia")


def _normalizar(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------
# 1. Perfilar CV
# ---------------------------------------------------------------------
def perfilar_cv(usuario_id: str, cv_texto: str, cv_archivo: str | None = None,
                lista: list[Proveedor] | None = None) -> dict:
    perfil, modelo = consultar("perfilar", prompts.PERFILAR_CV, cv_texto[:20000], lista)
    with sesion_transaccion() as s:
        s.execute(
            text("""
                UPDATE perfiles
                   SET cv_texto = :cv, cv_archivo = COALESCE(:archivo, cv_archivo),
                       resumen = :resumen,
                       cargos_objetivo = :cargos,
                       habilidades = CAST(:habilidades AS jsonb),
                       anios_experiencia = :anios,
                       nivel_educativo = :educacion,
                       idiomas = CAST(:idiomas AS jsonb),
                       perfil_ia = CAST(:completo AS jsonb),
                       modelo_ia = :modelo,
                       actualizado_en = now()
                 WHERE usuario_id = :u
            """),
            {"u": usuario_id, "cv": cv_texto, "archivo": cv_archivo,
             "resumen": perfil.get("resumen"),
             "cargos": list(perfil.get("cargos_objetivo") or []),
             "habilidades": json.dumps(perfil.get("habilidades") or [], ensure_ascii=False),
             "anios": perfil.get("anios_experiencia"),
             "educacion": perfil.get("nivel_educativo"),
             "idiomas": json.dumps(perfil.get("idiomas") or [], ensure_ascii=False),
             "completo": json.dumps(perfil, ensure_ascii=False), "modelo": modelo},
        )
    return perfil


def _perfil_para_ia(s, usuario_id: str) -> dict:
    fila = s.execute(
        text("""SELECT resumen, cargos_objetivo, habilidades, anios_experiencia, nivel_educativo,
                       idiomas, ciudades, modalidad::text AS modalidad, salario_minimo, palabras_excluir
                  FROM perfiles WHERE usuario_id = :u"""),
        {"u": usuario_id},
    ).mappings().one()
    return {k: (float(v) if k == "anios_experiencia" and v is not None else v) for k, v in fila.items()}


# ---------------------------------------------------------------------
# 2. Generar busquedas (para fuentes por API: Jooble, Careerjet)
# ---------------------------------------------------------------------
def generar_busquedas(usuario_id: str, lista: list[Proveedor] | None = None) -> list[dict]:
    with sesion_transaccion() as s:
        perfil = _perfil_para_ia(s, usuario_id)
    respuesta, _ = consultar("generar_busquedas", prompts.GENERAR_BUSQUEDAS,
                             json.dumps(perfil, ensure_ascii=False), lista)
    busquedas = [b for b in respuesta.get("busquedas", []) if b.get("palabras_clave")][:8]

    with sesion_transaccion() as s:
        perfil_id = s.execute(text("SELECT id FROM perfiles WHERE usuario_id = :u"),
                              {"u": usuario_id}).scalar_one()
        fuentes = s.execute(text("SELECT id FROM fuentes WHERE tipo = 'api'")).scalars().all()
        # Las generadas antes por IA se reemplazan; las creadas a mano se conservan
        s.execute(text("DELETE FROM busquedas WHERE perfil_id = :p AND generada_por_ia"), {"p": perfil_id})
        for fuente_id in fuentes:
            for b in busquedas:
                s.execute(
                    text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion)
                            VALUES (:p, :f, :k, :u)"""),
                    {"p": perfil_id, "f": fuente_id, "k": b["palabras_clave"][:200],
                     "u": (b.get("ubicacion") or "Colombia")[:120]},
                )
    return busquedas


# ---------------------------------------------------------------------
# 3. Evaluar coincidencias pendientes
# ---------------------------------------------------------------------
def evaluar_pendientes(limite: int = 60, lista: list[Proveedor] | None = None) -> dict:
    """Evalua coincidencias sin puntaje. El limite protege la cuota gratuita diaria."""
    with sesion_transaccion() as s:
        pendientes = s.execute(
            text("""
                SELECT c.id, c.usuario_id, v.titulo, v.empresa, v.ubicacion, v.modalidad::text AS modalidad,
                       v.salario_texto, v.descripcion
                  FROM coincidencias c JOIN vacantes v ON v.id = c.vacante_id
                 WHERE c.puntaje IS NULL AND v.abierta
                 ORDER BY c.creada_en
                 LIMIT :n
            """),
            {"n": limite},
        ).mappings().all()
        perfiles = {u: _perfil_para_ia(s, u) for u in {p["usuario_id"] for p in pendientes}}

    resultado = {"evaluadas": 0, "excluidas": 0, "errores": 0}
    for c in pendientes:
        perfil = perfiles[c["usuario_id"]]
        titulo_norm = _normalizar(f'{c["titulo"]} {c["empresa"] or ""}')
        excluida = next((p for p in perfil.get("palabras_excluir") or []
                         if _normalizar(p) in titulo_norm), None)
        if excluida:  # filtro local: no gasta cuota de IA
            evaluacion = {"puntaje": 0, "explicacion": f"Contiene una palabra excluida: {excluida}",
                          "fortalezas": [], "brechas": []}
            modelo = "filtro_local"
            resultado["excluidas"] += 1
        else:
            vacante = {k: c[k] for k in ("titulo", "empresa", "ubicacion", "modalidad", "salario_texto")}
            vacante["descripcion"] = (c["descripcion"] or "")[:3000]
            try:
                evaluacion, modelo = consultar(
                    "evaluar", prompts.EVALUAR_VACANTE,
                    json.dumps({"candidato": perfil, "vacante": vacante}, ensure_ascii=False, default=str),
                    lista)
            except ErrorIA:
                resultado["errores"] += 1
                if resultado["errores"] >= 3:  # probablemente cuota agotada: se retoma manana
                    log.warning("Evaluacion detenida tras 3 errores seguidos")
                    break
                continue
            resultado["evaluadas"] += 1

        puntaje = max(0, min(100, int(evaluacion.get("puntaje") or 0)))
        with sesion_transaccion() as s:
            s.execute(
                text("""UPDATE coincidencias
                           SET puntaje = :p, explicacion = :e, fortalezas = :f, brechas = :b,
                               modelo_ia = :m, evaluada_en = now()
                         WHERE id = :id"""),
                {"id": c["id"], "p": puntaje, "e": evaluacion.get("explicacion"),
                 "f": list(evaluacion.get("fortalezas") or [])[:3],
                 "b": list(evaluacion.get("brechas") or [])[:3], "m": modelo},
            )
    return resultado
