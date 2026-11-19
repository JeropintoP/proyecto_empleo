"""Recolectores por API: Jooble, Careerjet y Get on Board.

Cada corrida toma, por fuente, las N busquedas activas que hace mas tiempo no se consultan
(N = parametro consultas_diarias_<fuente>) y guarda las vacantes con la misma logica del
recolector de correo (regla de reproceso incluida). Las vacantes nuevas se asignan al usuario
duenno de la busqueda; la IA las evalua despues.

Si la fuente responde 401/403 (key invalida o revocada) o 429 (cupo agotado), se detiene esa
fuente en la corrida y el error queda en `ejecuciones`.
"""
import hashlib
import html
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import text

from app.config import config
from app.db import sesion_transaccion
from worker.correo.lectores import modalidad_desde, salario_cop
from worker.correo.modelos import VacanteExtraida
from worker.correo.recolector import guardar_vacante

log = logging.getLogger("worker.api")

AGENTE = "ProyectoEmpleo/1.0 (+https://jeropintop.github.io/proyecto_empleo/)"


class FuenteDetenida(Exception):
    """La fuente rechazo la key o agoto su cupo: no tiene sentido seguir consultandola hoy."""


def _limpiar(texto: str | None) -> str | None:
    if not texto:
        return None
    texto = html.unescape(re.sub(r"<[^>]+>", " ", texto))
    return re.sub(r"\s+", " ", texto).strip() or None


def _fecha(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None


def _entero(valor) -> int | None:
    return int(valor) if isinstance(valor, (int, float)) and valor > 0 else None


def _revisar_respuesta(r: httpx.Response, fuente: str) -> None:
    if r.status_code in (401, 403):
        raise FuenteDetenida(f"{fuente}: acceso rechazado ({r.status_code}); revise la API key")
    if r.status_code == 429:
        raise FuenteDetenida(f"{fuente}: cupo de consultas agotado (429)")
    if r.status_code != 200:
        raise httpx.HTTPStatusError(f"{fuente} respondio {r.status_code}: {r.text[:200]}",
                                    request=r.request, response=r)


# ---------------------------------------------------------------------
# Jooble: POST https://<host>/api/<key>  {"keywords", "location", "page"}
# ---------------------------------------------------------------------
def consultar_jooble(cliente: httpx.Client, palabras: str, ubicacion: str) -> list[VacanteExtraida]:
    cfg = config()
    r = cliente.post(f"https://{cfg.jooble_host}/api/{cfg.jooble_api_key}",
                     json={"keywords": palabras, "location": ubicacion, "page": "1"})
    _revisar_respuesta(r, "Jooble")
    vacantes = []
    for j in r.json().get("jobs") or []:
        if not j.get("title") or not j.get("link"):
            continue
        id_portal = str(j.get("id") or hashlib.md5(j["link"].encode()).hexdigest())
        salario = _limpiar(j.get("salary"))
        vacantes.append(VacanteExtraida(
            portal="jooble", id_portal=id_portal,
            titulo=_limpiar(j["title"]), url=j["link"],
            empresa=_limpiar(j.get("company")), ubicacion=_limpiar(j.get("location")),
            modalidad=modalidad_desde(f'{j.get("title", "")} {j.get("type", "")} {j.get("location", "")}'),
            salario_texto=salario, salario_min=salario_cop(salario),
            fecha_publicacion=_fecha(j.get("updated")),
            descripcion=_limpiar(j.get("snippet")),
            extra={"origen": j.get("source")} if j.get("source") else {},
        ))
    return vacantes


# ---------------------------------------------------------------------
# Careerjet v4: GET https://search.api.careerjet.net/v4/query  (Basic auth "<key>:")
# ---------------------------------------------------------------------
def consultar_careerjet(cliente: httpx.Client, palabras: str, ubicacion: str) -> list[VacanteExtraida]:
    cfg = config()
    params = {
        "locale_code": cfg.careerjet_locale, "keywords": palabras, "sort": "date", "page_size": 50,
        "user_ip": cfg.careerjet_user_ip or "127.0.0.1", "user_agent": AGENTE,
    }
    if ubicacion and ubicacion.lower() != "colombia":  # sin ubicacion = todo el pais
        params["location"] = ubicacion
    r = cliente.get("https://search.api.careerjet.net/v4/query", params=params,
                    auth=(cfg.careerjet_api_key, ""),
                    headers={"Referer": "https://jeropintop.github.io/proyecto_empleo/"})
    _revisar_respuesta(r, "Careerjet")
    datos = r.json()
    if datos.get("type") != "JOBS":  # p. ej. LOCATIONS cuando la ubicacion es ambigua
        log.info("Careerjet '%s' en '%s': respuesta %s", palabras, ubicacion, datos.get("type"))
        return []
    vacantes = []
    for j in datos.get("jobs") or []:
        if not j.get("title") or not j.get("url"):
            continue
        titulo, empresa, lugar = _limpiar(j["title"]), _limpiar(j.get("company")), _limpiar(j.get("locations"))
        # Careerjet no entrega un id: se usa una huella estable del contenido
        id_portal = hashlib.md5(f"{titulo}|{empresa}|{lugar}|{j.get('date')}".lower().encode()).hexdigest()
        vacantes.append(VacanteExtraida(
            portal="careerjet", id_portal=id_portal, titulo=titulo, url=j["url"],
            empresa=empresa, ubicacion=lugar, modalidad=modalidad_desde(f"{titulo} {lugar}"),
            salario_texto=_limpiar(j.get("salary")),
            salario_min=_entero(j.get("salary_min")) or salario_cop(j.get("salary")),
            salario_max=_entero(j.get("salary_max")),
            fecha_publicacion=_fecha(j.get("date")),
            descripcion=_limpiar(j.get("description")),
            extra={"origen": j.get("site")} if j.get("site") else {},
        ))
    return vacantes


# ---------------------------------------------------------------------
# Orquestacion comun
# ---------------------------------------------------------------------
def _recolectar(codigo: str, consultar: Callable, cliente: httpx.Client | None) -> dict:
    total = {"consultas": 0, "leidas": 0, "nuevas": 0, "reprocesadas": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = :c"), {"c": codigo}).scalar_one()
        cupo = int(s.execute(text("SELECT valor FROM parametros WHERE clave = :c"),
                             {"c": f"consultas_diarias_{codigo}"}).scalar() or 4)
        busquedas = s.execute(
            text("""SELECT b.id, b.palabras_clave, b.ubicacion, p.usuario_id
                      FROM busquedas b JOIN perfiles p ON p.id = b.perfil_id
                      JOIN usuarios u ON u.id = p.usuario_id AND u.activo
                     WHERE b.fuente_id = :f AND b.activa
                     ORDER BY b.ultima_consulta NULLS FIRST, b.creada_en
                     LIMIT :n"""),
            {"f": fuente_id, "n": cupo},
        ).all()

    propio = cliente is None
    cliente = cliente or httpx.Client(timeout=30, headers={"User-Agent": AGENTE})
    try:
        for b in busquedas:
            vacantes = consultar(cliente, b.palabras_clave, b.ubicacion or "Colombia")
            total["consultas"] += 1
            with sesion_transaccion() as s:
                for v in vacantes:
                    r = guardar_vacante(s, fuente_id, b.usuario_id, v)
                    if r == "nueva":
                        total["nuevas"] += 1
                    elif r == "reprocesada":
                        total["reprocesadas"] += 1
                s.execute(text("UPDATE busquedas SET ultima_consulta = now(), ultimo_resultado = :n WHERE id = :id"),
                          {"n": len(vacantes), "id": b.id})
            total["leidas"] += len(vacantes)
    finally:
        if propio:
            cliente.close()
    return total


# ---------------------------------------------------------------------
# Get on Board: GET https://www.getonbrd.com/api/v0/search/jobs  (publica, sin key)
# Empleo tecnologico en Latinoamerica. Salarios en USD: se guardan solo como texto.
# ---------------------------------------------------------------------
_MODALIDAD_GOB = {"fully_remote": "remoto", "remote_local": "remoto", "temporarily_remote": "remoto",
                  "hybrid": "hibrido", "no_remote": "presencial"}


def consultar_getonbrd(cliente: httpx.Client, palabras: str, ubicacion: str) -> list[VacanteExtraida]:
    r = cliente.get("https://www.getonbrd.com/api/v0/search/jobs", params={
        "query": palabras, "country_code": "COL", "per_page": 40, "page": 1,
        "lang": "es", "expand": '["company"]',
    })
    _revisar_respuesta(r, "Get on Board")
    vacantes = []
    for j in r.json().get("data") or []:
        a, url = j.get("attributes") or {}, (j.get("links") or {}).get("public_url")
        if not a.get("title") or not url:
            continue
        empresa = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name")
        minimo, maximo = _entero(a.get("min_salary")), _entero(a.get("max_salary"))
        salario = (f"USD {minimo:,}–{maximo:,} (mensual)".replace(",", ".") if minimo and maximo
                   else f"USD {(minimo or maximo):,} (mensual)".replace(",", ".") if (minimo or maximo) else None)
        modalidad = _MODALIDAD_GOB.get(a.get("remote_modality") or "") or ("remoto" if a.get("remote") else None)
        paises = ", ".join(a.get("countries") or []) or None
        publicado = a.get("published_at")
        descripcion = " ".join(filter(None, (_limpiar(a.get("description")), _limpiar(a.get("functions")))))
        vacantes.append(VacanteExtraida(
            portal="getonbrd", id_portal=str(j.get("id")), titulo=_limpiar(a["title"]), url=url,
            empresa=_limpiar(empresa), ubicacion=paises, modalidad=modalidad, salario_texto=salario,
            fecha_publicacion=datetime.fromtimestamp(publicado, tz=timezone.utc) if isinstance(publicado, int) else None,
            descripcion=descripcion[:3000] or None,
            extra={k: v for k, v in (("categoria", a.get("category_name")),) if v},
        ))
    return vacantes


def recolectar_getonbrd(cliente: httpx.Client | None = None) -> dict:
    return _recolectar("getonbrd", consultar_getonbrd, cliente)


def recolectar_jooble(cliente: httpx.Client | None = None) -> dict:
    return _recolectar("jooble", consultar_jooble, cliente)


def recolectar_careerjet(cliente: httpx.Client | None = None) -> dict:
    return _recolectar("careerjet", consultar_careerjet, cliente)
