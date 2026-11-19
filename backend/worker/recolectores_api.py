"""Recolectores por API: Jooble, Careerjet, Get on Board, Himalayas y Jobicy.

Cada corrida toma, por fuente, las N busquedas activas que hace mas tiempo no se consultan
(N = parametro consultas_diarias_<fuente>) y guarda las vacantes con la misma logica del
recolector de correo (regla de reproceso incluida). Las vacantes nuevas se asignan al usuario
duenno de la busqueda; la IA las evalua despues.

Si la fuente responde 401/403 (key invalida o revocada) o 429 (cupo agotado), se detiene esa
fuente en la corrida y el error queda en `ejecuciones`.
"""
import hashlib
import math
import html
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import text

from app.config import config
from app.db import sesion_transaccion
from worker.horario import corridas_por_dia
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
# Factor para llevar el salario a valor mensual segun salary_type
_A_MENSUAL = {"M": 1, "Y": 1 / 12, "W": 52 / 12, "D": 22, "H": 8 * 22}


def _salario_mensual_cop(valor, moneda: str | None, tipo: str | None) -> int | None:
    """Solo se convierten a cifra los salarios en pesos; otras monedas quedan como texto."""
    if not isinstance(valor, (int, float)) or valor <= 0 or (moneda or "").upper() != "COP":
        return None
    factor = _A_MENSUAL.get((tipo or "M").upper())
    return round(valor * factor) if factor else None


def _pedir_careerjet(cliente: httpx.Client, palabras: str, ubicacion: str | None) -> dict:
    cfg = config()
    params = {
        "locale_code": cfg.careerjet_locale, "keywords": palabras, "sort": "date", "page_size": 50,
        "fragment_size": 500,  # extracto mas largo: ayuda a la IA a evaluar
        "user_ip": cfg.careerjet_user_ip or "127.0.0.1", "user_agent": AGENTE,
    }
    if ubicacion:
        params["location"] = ubicacion
    r = cliente.get("https://search.api.careerjet.net/v4/query", params=params,
                    auth=(cfg.careerjet_api_key, ""),
                    headers={"Referer": "https://jeropintop.github.io/proyecto_empleo/"})
    if r.status_code == 400:
        raise FuenteDetenida(f"Careerjet: {r.text[:150]} (revise CAREERJET_LOCALE)")
    _revisar_respuesta(r, "Careerjet")
    return r.json()


def consultar_careerjet(cliente: httpx.Client, palabras: str, ubicacion: str) -> list[VacanteExtraida]:
    lugar = None if not ubicacion or ubicacion.lower() == "colombia" else ubicacion  # sin lugar = todo el pais
    datos = _pedir_careerjet(cliente, palabras, lugar)

    # Modo ubicacion: varias coincidencias -> se toma la primera; ninguna -> todo el pais
    if datos.get("type") == "LOCATIONS" and lugar:
        opciones = datos.get("locations") or []
        log.info("Careerjet: ubicacion '%s' %s", lugar, f"ambigua, se usa '{opciones[0]}'" if opciones else "no encontrada")
        datos = _pedir_careerjet(cliente, palabras, opciones[0] if opciones else None)
    if datos.get("type") != "JOBS":
        return []

    vacantes = []
    for j in datos.get("jobs") or []:
        if not j.get("title") or not j.get("url"):
            continue
        titulo, empresa, lugar_j = _limpiar(j["title"]), _limpiar(j.get("company")), _limpiar(j.get("locations"))
        # Careerjet no entrega un id: se usa una huella estable del contenido
        id_portal = hashlib.md5(f"{titulo}|{empresa}|{lugar_j}|{j.get('date')}".lower().encode()).hexdigest()
        moneda, tipo = j.get("salary_currency_code"), j.get("salary_type")
        vacantes.append(VacanteExtraida(
            portal="careerjet", id_portal=id_portal, titulo=titulo, url=j["url"],
            empresa=empresa, ubicacion=lugar_j, modalidad=modalidad_desde(f"{titulo} {lugar_j}"),
            salario_texto=_limpiar(j.get("salary")),
            salario_min=_salario_mensual_cop(j.get("salary_min"), moneda, tipo),
            salario_max=_salario_mensual_cop(j.get("salary_max"), moneda, tipo),
            fecha_publicacion=_fecha(j.get("date")),
            descripcion=_limpiar(j.get("description")),
        ))
    return vacantes


# ---------------------------------------------------------------------
# Orquestacion comun
# ---------------------------------------------------------------------
def _recolectar(codigo: str, consultar: Callable, cliente: httpx.Client | None, en_ingles: bool = False) -> dict:
    total = {"consultas": 0, "leidas": 0, "nuevas": 0, "reprocesadas": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = :c"), {"c": codigo}).scalar_one()
        diarias = int(s.execute(text("SELECT valor FROM parametros WHERE clave = :c"),
                                {"c": f"consultas_diarias_{codigo}"}).scalar() or 4)
        # El cupo es diario: se reparte entre las corridas del dia (cada 3 h = 8 corridas)
        cupo = max(1, math.ceil(diarias / corridas_por_dia(s)))
        busquedas = s.execute(
            text("""SELECT b.id, b.palabras_clave, b.palabras_clave_en, b.ubicacion, p.usuario_id
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
            palabras = (b.palabras_clave_en or b.palabras_clave) if en_ingles else b.palabras_clave
            vacantes = consultar(cliente, palabras, b.ubicacion or "Colombia")
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


def _pedir_getonbrd(cliente: httpx.Client, extra: dict, palabras: str) -> list[dict]:
    r = cliente.get("https://www.getonbrd.com/api/v0/search/jobs", params={
        "query": palabras, "per_page": 40, "page": 1, "lang": "es", "expand": '["company"]', **extra,
    })
    _revisar_respuesta(r, "Get on Board")
    return r.json().get("data") or []


def _admite_colombia(a: dict) -> bool:
    paises = [p.lower() for p in a.get("countries") or []]
    return not paises or any("colombia" in p for p in paises)


def consultar_getonbrd(cliente: httpx.Client, palabras: str, ubicacion: str) -> list[VacanteExtraida]:
    # 1) Vacantes publicadas en Colombia (country_code ISO de 2 letras: "CO", no "COL")
    # 2) Vacantes remotas que admiten Colombia o no restringen pais (la mayoria en Get on Board)
    resultados = _pedir_getonbrd(cliente, {"country_code": "CO"}, palabras)
    resultados += [j for j in _pedir_getonbrd(cliente, {"remote": "true"}, palabras)
                   if _admite_colombia(j.get("attributes") or {})]

    vacantes, vistos = [], set()
    for j in resultados:
        a, url = j.get("attributes") or {}, (j.get("links") or {}).get("public_url")
        if not a.get("title") or not url or j.get("id") in vistos:
            continue
        vistos.add(j.get("id"))
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


# ---------------------------------------------------------------------
# Salarios internacionales: texto legible; cifra solo si viene en pesos
# ---------------------------------------------------------------------
_PERIODO = {"annual": "Y", "yearly": "Y", "monthly": "M", "weekly": "W", "fortnightly": "W",
            "daily": "D", "hourly": "H"}
_PERIODO_TEXTO = {"Y": "anual", "M": "mensual", "W": "semanal", "D": "diario", "H": "por hora"}


def _salario_texto(minimo, maximo, moneda: str | None, tipo: str) -> str | None:
    minimo, maximo = _entero(minimo), _entero(maximo)
    if not (minimo or maximo):
        return None
    rango = f"{minimo:,}–{maximo:,}" if minimo and maximo else f"{(minimo or maximo):,}"
    return f"{(moneda or 'USD').upper()} {rango.replace(',', '.')} ({_PERIODO_TEXTO.get(tipo, 'anual')})"


# ---------------------------------------------------------------------
# Himalayas: GET https://himalayas.app/jobs/api/search?q=...&country=CO  (publica, sin key)
# Terminos: postular siempre por applicationLink y dar credito visible a Himalayas.
# ---------------------------------------------------------------------
def consultar_himalayas(cliente: httpx.Client, palabras: str, ubicacion: str) -> list[VacanteExtraida]:
    r = cliente.get("https://himalayas.app/jobs/api/search",
                    params={"q": palabras, "country": "CO", "sort": "recent", "page": 1})
    _revisar_respuesta(r, "Himalayas")
    vacantes = []
    for j in r.json().get("jobs") or []:
        url = j.get("applicationLink")
        if not j.get("title") or not url:
            continue
        paises = [p.get("name") if isinstance(p, dict) else str(p) for p in j.get("locationRestrictions") or []]
        tipo = _PERIODO.get((j.get("salaryPeriod") or "annual").lower(), "Y")
        publicado = j.get("pubDate")
        vacantes.append(VacanteExtraida(
            portal="himalayas", id_portal=str(j.get("guid") or hashlib.md5(url.encode()).hexdigest()),
            titulo=_limpiar(j["title"]), url=url, empresa=_limpiar(j.get("companyName")),
            ubicacion=("Remoto: " + ", ".join(filter(None, paises))) if paises else "Remoto (mundial)",
            modalidad="remoto",
            salario_texto=_salario_texto(j.get("minSalary"), j.get("maxSalary"), j.get("currency"), tipo),
            salario_min=_salario_mensual_cop(j.get("minSalary"), j.get("currency"), tipo),
            salario_max=_salario_mensual_cop(j.get("maxSalary"), j.get("currency"), tipo),
            fecha_publicacion=datetime.fromtimestamp(publicado, tz=timezone.utc) if isinstance(publicado, (int, float)) else None,
            descripcion=(_limpiar(j.get("excerpt")) or _limpiar(j.get("description")) or "")[:3000] or None,
        ))
    return vacantes


def recolectar_himalayas(cliente: httpx.Client | None = None) -> dict:
    from app.ia.servicios import completar_ingles
    completar_ingles()
    return _recolectar("himalayas", consultar_himalayas, cliente, en_ingles=True)


# ---------------------------------------------------------------------
# Jobicy: GET https://jobicy.com/api/v2/remote-jobs?geo=latam  (publica, sin key)
# Terminos: no consultar mas de una vez por hora. Por eso se hace UNA consulta por corrida
# (remotos para Latinoamerica) y se filtra localmente con las busquedas en ingles de cada usuario.
# ---------------------------------------------------------------------
_PALABRAS_VACIAS = {"de", "del", "la", "las", "el", "los", "en", "y", "a", "con", "para", "por", "un", "una",
                    "of", "the", "and", "in", "for", "jr", "sr"}


def _terminos(texto: str) -> list[str]:
    """Palabras sin tildes ni palabras vacias, recortadas a su raiz (6 letras) para que
    'mecanico' coincida con 'mecánica' e 'ingeniero' con 'ingeniería'."""
    if not texto:
        return []
    plano = unicodedata.normalize("NFKD", (_limpiar(texto) or "").lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return [t[:6] for t in re.findall(r"[a-z0-9+#]+", plano) if t not in _PALABRAS_VACIAS and len(t) > 1]


def coincide(busqueda: str, titulo: str, extracto: str | None) -> bool:
    """Todos los terminos de la busqueda aparecen en el titulo, o en el titulo + extracto."""
    terminos = _terminos(busqueda)
    if not terminos:
        return False
    en_titulo = set(_terminos(titulo))
    if all(t in en_titulo for t in terminos):
        return True
    en_todo = en_titulo | set(_terminos(extracto or ""))
    return len(terminos) > 1 and all(t in en_todo for t in terminos) and terminos[-1] in en_titulo


def recolectar_jobicy(cliente: httpx.Client | None = None) -> dict:
    from app.ia.servicios import completar_ingles

    total = {"consultas": 0, "leidas": 0, "nuevas": 0, "reprocesadas": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = 'jobicy'")).scalar_one()
        reciente = s.execute(text("""SELECT EXISTS (SELECT 1 FROM ejecuciones WHERE fuente_id = :f
                                       AND estado = 'ok' AND inicio > now() - interval '60 minutes')"""),
                             {"f": fuente_id}).scalar()
    if reciente:  # respeta el limite de Jobicy aunque se presione "Actualizar ahora"
        log.info("Jobicy consultado hace menos de una hora; se omite")
        return {**total, "omitida": "consultada hace menos de una hora"}

    completar_ingles()
    with sesion_transaccion() as s:
        busquedas = s.execute(text("""
            SELECT DISTINCT p.usuario_id, COALESCE(b.palabras_clave_en, b.palabras_clave) AS termino
              FROM busquedas b JOIN perfiles p ON p.id = b.perfil_id
              JOIN usuarios u ON u.id = p.usuario_id AND u.activo
             WHERE b.fuente_id = :f AND b.activa"""), {"f": fuente_id}).all()
    if not busquedas:
        return total

    propio = cliente is None
    cliente = cliente or httpx.Client(timeout=30, headers={"User-Agent": AGENTE})
    try:
        r = cliente.get("https://jobicy.com/api/v2/remote-jobs", params={"geo": "latam", "count": 100})
        _revisar_respuesta(r, "Jobicy")
        trabajos = r.json().get("jobs") or []
    finally:
        if propio:
            cliente.close()
    total["consultas"], total["leidas"] = 1, len(trabajos)

    with sesion_transaccion() as s:
        for j in trabajos:
            if not j.get("jobTitle") or not j.get("url"):
                continue
            usuarios = {b.usuario_id for b in busquedas if coincide(b.termino, j["jobTitle"], j.get("jobExcerpt"))}
            if not usuarios:
                continue
            tipo = _PERIODO.get((j.get("salaryPeriod") or "yearly").lower(), "Y")
            v = VacanteExtraida(
                portal="jobicy", id_portal=str(j.get("id") or j.get("jobSlug")), titulo=_limpiar(j["jobTitle"]),
                url=j["url"], empresa=_limpiar(j.get("companyName")),
                ubicacion=f"Remoto: {j.get('jobGeo') or 'LATAM'}", modalidad="remoto",
                salario_texto=_salario_texto(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency"), tipo),
                salario_min=_salario_mensual_cop(j.get("salaryMin"), j.get("salaryCurrency"), tipo),
                salario_max=_salario_mensual_cop(j.get("salaryMax"), j.get("salaryCurrency"), tipo),
                fecha_publicacion=_fecha(j.get("pubDate")),
                descripcion=(_limpiar(j.get("jobExcerpt")) or _limpiar(j.get("jobDescription")) or "")[:3000] or None,
            )
            for usuario_id in usuarios:
                resultado = guardar_vacante(s, fuente_id, usuario_id, v)
                if resultado == "nueva":
                    total["nuevas"] += 1
                elif resultado == "reprocesada":
                    total["reprocesadas"] += 1
    return total


def recolectar_jooble(cliente: httpx.Client | None = None) -> dict:
    return _recolectar("jooble", consultar_jooble, cliente)


def recolectar_careerjet(cliente: httpx.Client | None = None) -> dict:
    return _recolectar("careerjet", consultar_careerjet, cliente)
