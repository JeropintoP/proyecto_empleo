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
def _numero(valor) -> float | None:
    try:
        return round(float(valor), 1)
    except (TypeError, ValueError):
        return None


def _experiencias(lista) -> list[dict]:
    """Solo los campos esperados y con tipos seguros (la IA a veces agrega o cambia cosas)."""
    resultado = []
    for e in lista or []:
        if not isinstance(e, dict) or not (e.get("cargo") or e.get("empresa")):
            continue
        resultado.append({
            "cargo": str(e.get("cargo") or "")[:150], "empresa": str(e.get("empresa") or "")[:150],
            "desde": str(e.get("desde") or "")[:20], "hasta": str(e.get("hasta") or "")[:20],
            "anios": _numero(e.get("anios")), "tipo": "practica" if e.get("tipo") == "practica" else "empleo",
            "relacionada": bool(e.get("relacionada")),
        })
    return resultado[:20]


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
                       carreras = :carreras,
                       experiencias = CAST(:experiencias AS jsonb),
                       anios_experiencia_area = :anios_area,
                       -- La ciudad del CV y las exclusiones sugeridas solo se usan si la persona
                       -- no ha configurado las suyas (luego las edita en su perfil)
                       ciudades = CASE WHEN cardinality(ciudades) = 0 AND CAST(:ciudad AS text) IS NOT NULL
                                       THEN ARRAY[CAST(:ciudad AS text)] ELSE ciudades END,
                       palabras_excluir = CASE WHEN cardinality(palabras_excluir) = 0
                                               THEN CAST(:excluir AS text[]) ELSE palabras_excluir END,
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
             "carreras": [str(c)[:120] for c in perfil.get("carreras") or []][:5],
             "experiencias": json.dumps(_experiencias(perfil.get("experiencias")), ensure_ascii=False),
             "anios_area": _numero(perfil.get("anios_experiencia_area")),
             "ciudad": (str(perfil["ciudad"]).split(",")[0].strip()[:100] or None) if perfil.get("ciudad") else None,
             "excluir": [str(p).strip().lower()[:100] for p in perfil.get("palabras_excluir_sugeridas") or []
                         if str(p).strip()][:6],
             "completo": json.dumps(perfil, ensure_ascii=False), "modelo": modelo},
        )
    return perfil


def _perfil_para_ia(s, usuario_id: str) -> dict:
    fila = s.execute(
        text("""SELECT resumen, carreras, cargos_objetivo, habilidades, experiencias, anios_experiencia,
                       anios_experiencia_area, nivel_educativo, idiomas, ciudades, modalidad::text AS modalidad,
                       salario_minimo, palabras_excluir
                  FROM perfiles WHERE usuario_id = :u"""),
        {"u": usuario_id},
    ).mappings().one()
    return {k: (float(v) if k.startswith("anios_experiencia") and v is not None else v) for k, v in fila.items()}


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
        fuentes = s.execute(text("SELECT id FROM fuentes WHERE tipo = 'api' AND codigo <> 'empresas'")).scalars().all()
        # Las generadas antes por IA se reemplazan; las creadas a mano se conservan
        s.execute(text("DELETE FROM busquedas WHERE perfil_id = :p AND generada_por_ia"), {"p": perfil_id})
        for fuente_id in fuentes:
            for b in busquedas:
                s.execute(
                    text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, palabras_clave_en, ubicacion)
                            VALUES (:p, :f, :k, :en, :u)"""),
                    {"p": perfil_id, "f": fuente_id, "k": b["palabras_clave"][:200],
                     "en": (b.get("palabras_clave_en") or "")[:200] or None,
                     "u": (b.get("ubicacion") or "Colombia")[:120]},
                )
    return busquedas


def completar_ingles(lista: list[Proveedor] | None = None) -> int:
    """Agrega la version en ingles a las busquedas que no la tienen (una sola consulta a la IA).
    Si la IA no esta disponible, usa el texto en espanol para no bloquear la recolección."""
    with sesion_transaccion() as s:
        faltantes = s.execute(text(
            "SELECT DISTINCT palabras_clave FROM busquedas WHERE palabras_clave_en IS NULL AND activa LIMIT 30"
        )).scalars().all()
    if not faltantes:
        return 0
    try:
        respuesta, _ = consultar("traducir_busquedas", prompts.TRADUCIR_BUSQUEDAS,
                                 json.dumps(faltantes, ensure_ascii=False), lista)
        traducciones = {t.get("es"): t.get("en") for t in respuesta.get("traducciones", []) if t.get("en")}
    except ErrorIA as exc:
        log.warning("No se pudieron traducir las busquedas: %s", exc)
        traducciones = {}
    with sesion_transaccion() as s:
        for es in faltantes:
            s.execute(text("UPDATE busquedas SET palabras_clave_en = :en WHERE palabras_clave = :es "
                           "AND palabras_clave_en IS NULL"),
                      {"en": (traducciones.get(es) or es)[:200], "es": es})
    return len(faltantes)


# ---------------------------------------------------------------------
# 3. Evaluar coincidencias pendientes
# ---------------------------------------------------------------------
def cupo_evaluaciones(s) -> int:
    """Evaluaciones que quedan en las ultimas 24 h (parametro evaluaciones_diarias, 60 por defecto).
    Con corridas cada 3 horas, el tope es por dia y no por corrida: protege la cuota gratuita de la IA."""
    diarias = int(s.execute(text("SELECT valor FROM parametros WHERE clave = 'evaluaciones_diarias'")).scalar() or 60)
    hechas = s.execute(text("""SELECT count(*) FROM uso_ia
                                WHERE tarea = 'evaluar' AND exito AND creado_en > now() - interval '24 hours'""")).scalar()
    return max(0, diarias - hechas)


def evaluar_pendientes(limite: int | None = None, lista: list[Proveedor] | None = None) -> dict:
    """Evalua coincidencias sin puntaje, sin pasar del cupo diario de evaluaciones."""
    with sesion_transaccion() as s:
        if limite is None:
            limite = cupo_evaluaciones(s)
        pendientes = s.execute(
            text("""
                SELECT c.id, c.usuario_id, v.titulo, v.empresa, v.ubicacion, v.modalidad::text AS modalidad,
                       v.salario_texto, v.descripcion
                  FROM coincidencias c JOIN vacantes v ON v.id = c.vacante_id
                 WHERE c.puntaje IS NULL AND v.abierta
                 ORDER BY c.creada_en DESC   -- lo mas reciente primero: si hay atraso, lo viejo espera
                 LIMIT :n
            """),
            {"n": limite},
        ).mappings().all()
        perfiles = {u: _perfil_para_ia(s, u) for u in {p["usuario_id"] for p in pendientes}}

    resultado = {"evaluadas": 0, "excluidas": 0, "errores": 0}
    seguidos = 0
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
                seguidos += 1
                if seguidos >= 3:  # probablemente cuota agotada: se retoma manana
                    log.warning("Evaluacion detenida tras 3 errores seguidos")
                    break
                continue
            seguidos = 0
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
