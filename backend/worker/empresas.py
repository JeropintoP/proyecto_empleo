"""Monitor de empresas: revisa la pagina de empleo de empresas puntuales.

Tres adaptadores, segun la tabla `empresas`:
  - smartrecruiters: API publica  GET api.smartrecruiters.com/v1/companies/{id}/postings
  - successfactors:  feed XML del sitio de empleo de SAP SuccessFactors
                     (/career?company=X&career_ns=job_listing_summary&resultType=XML)
  - pagina:          cualquier pagina publica; se toman los enlaces del mismo sitio que parecen
                     vacantes (texto o direccion con "vacante", "empleo", "convocatoria", PDF...).
                     Solo funciona con paginas que traen las vacantes en el HTML (no armadas con JavaScript).

Cada empresa se consulta como maximo una vez cada HORAS_ENTRE_REVISIONES: una peticion por
empresa en cada corrida programada (cada 3 horas). Las vacantes se asignan al usuario de la empresa o, si no tiene,
a todos los usuarios activos; la IA decide despues si encajan con cada perfil.
"""
import hashlib
import ipaddress
import logging
import re
import socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

import httpx
from sqlalchemy import text

from app.db import sesion_transaccion
from worker.correo.modelos import VacanteExtraida
from worker.correo.recolector import guardar_vacante
from worker.recolectores_api import _fecha, _limpiar, _revisar_respuesta, nuevo_cliente

log = logging.getLogger("worker.empresas")

HORAS_ENTRE_REVISIONES = 2.5   # una consulta por corrida (cada 3 h); evita repetir en actualizaciones manuales
MAX_VACANTES_POR_EMPRESA = 60
TIPOS = ("smartrecruiters", "successfactors", "pagina")


class EmpresaInvalida(ValueError):
    """Datos de la empresa que no se pueden consultar (URL mal escrita, red interna...)."""


# ---------------------------------------------------------------------
# Seguridad: el monitor solo consulta sitios publicos de internet
# ---------------------------------------------------------------------
def validar_url_publica(url: str) -> str:
    partes = urlparse(url.strip())
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise EmpresaInvalida("La direccion debe empezar por https:// (o http://)")
    try:
        direcciones = {info[4][0] for info in socket.getaddrinfo(partes.hostname, None)}
    except socket.gaierror:
        raise EmpresaInvalida(f"No se encontro el sitio {partes.hostname}")
    for d in direcciones:
        ip = ipaddress.ip_address(d.split("%")[0])
        if not ip.is_global:
            raise EmpresaInvalida("Solo se permiten sitios publicos de internet")
    return url.strip()


def _vigilar_peticion(req: httpx.Request) -> None:
    """Tambien en cada redireccion: evita que un sitio redirija hacia la red interna."""
    validar_url_publica(str(req.url))


def cliente_publico() -> httpx.Client:
    return nuevo_cliente(follow_redirects=True,
                        event_hooks={"request": [_vigilar_peticion]})


def validar(tipo: str, identificador: str) -> str:
    """Normaliza y valida el identificador segun el tipo. Devuelve el identificador limpio."""
    identificador = (identificador or "").strip()
    if tipo == "smartrecruiters":
        identificador = identificador.rstrip("/").split("/")[-1]  # acepta tambien la URL completa
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", identificador):
            raise EmpresaInvalida("Escriba el identificador de SmartRecruiters, p. ej. konecranes")
        return identificador
    if tipo == "successfactors":
        validar_url_publica(identificador)
        if not parse_qs(urlparse(identificador).query).get("company"):
            raise EmpresaInvalida("La URL de SuccessFactors debe incluir ?company=...")
        return identificador
    if tipo == "pagina":
        return validar_url_publica(identificador)
    raise EmpresaInvalida(f"Tipo desconocido: {tipo}")


# ---------------------------------------------------------------------
# SmartRecruiters
# ---------------------------------------------------------------------
def consultar_smartrecruiters(cliente: httpx.Client, empresa: dict) -> list[VacanteExtraida]:
    ident = empresa["identificador"]
    vacantes: list[VacanteExtraida] = []
    offset = 0
    while len(vacantes) < MAX_VACANTES_POR_EMPRESA:
        params = {"limit": 100, "offset": offset}
        if empresa.get("pais"):
            params["country"] = empresa["pais"].lower()
        r = cliente.get(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings", params=params)
        if r.status_code == 404:
            raise EmpresaInvalida(f"SmartRecruiters no tiene publicaciones abiertas para '{ident}'")
        _revisar_respuesta(r, "SmartRecruiters")
        datos = r.json()
        contenido = datos.get("content") or []
        for p in contenido:
            if not p.get("id") or not p.get("name"):
                continue
            lugar = p.get("location") or {}
            remoto = bool(lugar.get("remote"))
            ubicacion = ", ".join(x for x in (lugar.get("city"), lugar.get("region")) if x) or lugar.get("country")
            detalles = [(p.get(k) or {}).get("label") for k in ("department", "function", "typeOfEmployment", "experienceLevel")]
            compania = (p.get("company") or {})
            vacantes.append(VacanteExtraida(
                portal="smartrecruiters", id_portal=str(p["id"]), titulo=_limpiar(p["name"]),
                url=f"https://jobs.smartrecruiters.com/{compania.get('identifier') or ident}/{p['id']}",
                empresa=compania.get("name") or empresa["nombre"],
                ubicacion=("Remoto" + (f" ({ubicacion})" if ubicacion else "")) if remoto else ubicacion,
                modalidad="remoto" if remoto else None,
                fecha_publicacion=_fecha(p.get("releasedDate")),
                descripcion=" · ".join(d for d in detalles if d) or None,
            ))
        offset += len(contenido)
        if not contenido or offset >= int(datos.get("totalFound") or 0):
            break
    return vacantes[:MAX_VACANTES_POR_EMPRESA]


# ---------------------------------------------------------------------
# SAP SuccessFactors (feed XML del sitio de empleo clasico)
# ---------------------------------------------------------------------
def _campos(elemento: ET.Element) -> dict[str, str]:
    """Hijos directos de un elemento como {etiqueta_en_minuscula: texto}."""
    return {re.sub(r"^\{.*\}", "", h.tag).lower(): (h.text or "").strip() for h in elemento if (h.text or "").strip()}


def _primero(campos: dict[str, str], *patrones: str) -> str | None:
    for patron in patrones:
        for clave, valor in campos.items():
            if re.search(patron, clave):
                return valor
    return None


def consultar_successfactors(cliente: httpx.Client, empresa: dict) -> list[VacanteExtraida]:
    url = urlparse(empresa["identificador"])
    compania = parse_qs(url.query)["company"][0]
    base = f"{url.scheme}://{url.netloc}/career"
    r = cliente.get(base, params={"company": compania, "career_ns": "job_listing_summary", "resultType": "XML"})
    _revisar_respuesta(r, "SuccessFactors")
    if r.content.lstrip()[:5].lower() == b"<html" or "html" in r.headers.get("content-type", ""):
        raise EmpresaInvalida("Esta empresa tiene desactivado el listado XML de SuccessFactors; "
                              "busque si publica en otra pagina (p. ej. su sitio en Elempleo)")
    try:
        raiz = ET.fromstring(r.content)
    except ET.ParseError:
        raise EmpresaInvalida("SuccessFactors no devolvio el listado en XML; revise la URL o use el tipo 'pagina'")

    vacantes, vistos = [], set()
    for elemento in raiz.iter():
        campos = _campos(elemento)
        titulo = _primero(campos, r"title", r"titulo")
        id_req = _primero(campos, r"req.*id", r"^id$", r"job.?id")
        if not titulo or not id_req or id_req in vistos:
            continue
        vistos.add(id_req)
        enlace = _primero(campos, r"url", r"link")
        if not enlace or not enlace.startswith("http"):
            enlace = f"{base}?company={compania}&career_ns=job_listing&career_job_req_id={id_req}"
        vacantes.append(VacanteExtraida(
            portal="successfactors", id_portal=f"{compania}:{id_req}", titulo=_limpiar(titulo), url=enlace,
            empresa=empresa["nombre"], ubicacion=_primero(campos, r"location", r"city", r"ciudad"),
            fecha_publicacion=_fecha(_primero(campos, r"posted", r"date")),
            descripcion=(_limpiar(_primero(campos, r"description", r"descripcion")) or "")[:3000] or None,
        ))
    return vacantes[:MAX_VACANTES_POR_EMPRESA]


# ---------------------------------------------------------------------
# Pagina generica: enlaces del mismo sitio que parecen vacantes
# ---------------------------------------------------------------------
class _Enlaces(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.enlaces: list[tuple[str, str]] = []
        self._href: str | None = None
        self._texto: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._ignorar += 1
        elif tag == "a" and not self._ignorar:
            self._href = dict(attrs).get("href")
            self._texto = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag == "a" and self._href is not None:
            self.enlaces.append((self._href, " ".join(self._texto)))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._texto.append(data)


_PISTA = re.compile(r"vacante|empleo|oferta|convocatoria|cargo|trabaj|job|career|position|requisici|"
                    r"postula|aplicar|apply|\.pdf$", re.I)
_GENERICOS = {"trabaje con nosotros", "trabaja con nosotros", "ofertas de empleo", "oferta de empleo", "empleo",
              "empleos", "vacantes", "ofertas", "convocatorias", "work with us", "careers", "jobs", "bolsa de empleo",
              "ver mas", "ver más", "leer mas", "leer más", "aplicar", "postularme", "postulate", "postúlate",
              "apply", "apply now", "mas informacion", "más información", "descargar", "aqui", "aquí", "clic aqui"}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def extraer_enlaces(html_pagina: str, url_pagina: str) -> list[tuple[str, str]]:
    """Devuelve [(url, titulo)] de los enlaces que parecen vacantes, sin repetir."""
    lector = _Enlaces()
    lector.feed(html_pagina)
    propia = urldefrag(url_pagina)[0].rstrip("/")
    resultado: dict[str, str] = {}
    for href, texto in lector.enlaces:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urldefrag(urljoin(url_pagina, href.strip()))[0]
        if url.rstrip("/") == propia or _host(url) != _host(url_pagina):
            continue
        titulo = _limpiar(texto) or ""
        es_pdf = urlparse(url).path.lower().endswith(".pdf")
        if es_pdf and (len(titulo) < 6 or titulo.lower() in _GENERICOS):  # el nombre del archivo hace de titulo
            titulo = re.sub(r"[-_]+", " ", urlparse(url).path.rsplit("/", 1)[-1][:-4]).strip()
        if len(titulo) < 6 or titulo.lower() in _GENERICOS:
            continue
        if not (_PISTA.search(urlparse(url).path) or _PISTA.search(titulo)):
            continue
        resultado.setdefault(url, titulo[:300])
    return list(resultado.items())


def consultar_pagina(cliente: httpx.Client, empresa: dict) -> list[VacanteExtraida]:
    r = cliente.get(empresa["identificador"])
    _revisar_respuesta(r, empresa["nombre"])
    if "html" not in r.headers.get("content-type", "html"):
        raise EmpresaInvalida("La direccion no es una pagina web (HTML)")
    enlaces = extraer_enlaces(r.text, str(r.url))
    return [VacanteExtraida(portal="empresa", id_portal=f"{empresa['id']}:{hashlib.md5(url.encode()).hexdigest()[:16]}",
                            titulo=titulo, url=url, empresa=empresa["nombre"])
            for url, titulo in enlaces[:MAX_VACANTES_POR_EMPRESA]]


ADAPTADORES = {"smartrecruiters": consultar_smartrecruiters, "successfactors": consultar_successfactors,
               "pagina": consultar_pagina}


# ---------------------------------------------------------------------
# Recolector (lo llama la tarea diaria) y vista previa (boton "Probar")
# ---------------------------------------------------------------------
def probar(empresa: dict, cliente: httpx.Client | None = None) -> list[VacanteExtraida]:
    """Consulta una empresa sin guardar nada."""
    propio = cliente is None
    cliente = cliente or cliente_publico()
    try:
        return ADAPTADORES[empresa["tipo"]](cliente, empresa)
    finally:
        if propio:
            cliente.close()


def recolectar_empresas(cliente: httpx.Client | None = None) -> dict:
    total = {"consultas": 0, "leidas": 0, "nuevas": 0, "reprocesadas": 0, "errores": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = 'empresas'")).scalar_one()
        empresas = [dict(e) for e in s.execute(text("""
            SELECT id, nombre, tipo::text AS tipo, identificador, pais, usuario_id
              FROM empresas
             WHERE activa AND (ultima_revision IS NULL
                               OR ultima_revision < now() - make_interval(secs => :h * 3600))
             ORDER BY ultima_revision NULLS FIRST"""), {"h": HORAS_ENTRE_REVISIONES}).mappings()]
        todos = [u for (u,) in s.execute(text("SELECT id FROM usuarios WHERE activo")).all()]

    propio = cliente is None
    cliente = cliente or cliente_publico()
    try:
        for e in empresas:
            total["consultas"] += 1
            try:
                vacantes = ADAPTADORES[e["tipo"]](cliente, e)
                error = None
            except Exception as exc:  # una empresa caida no detiene las demas
                log.warning("Empresa %s: %s", e["nombre"], exc)
                vacantes, error = [], str(exc)[:300]
                total["errores"] += 1
            with sesion_transaccion() as s:
                for v in vacantes:
                    for usuario_id in ([e["usuario_id"]] if e["usuario_id"] else todos):
                        r = guardar_vacante(s, fuente_id, usuario_id, v)
                        if r == "nueva":
                            total["nuevas"] += 1
                        elif r == "reprocesada":
                            total["reprocesadas"] += 1
                s.execute(text("""UPDATE empresas SET ultima_revision = now(), ultimo_total = :n, ultimo_error = :err
                                   WHERE id = :id"""),
                          {"n": None if error else len(vacantes), "err": error, "id": e["id"]})
            total["leidas"] += len(vacantes)
    finally:
        if propio:
            cliente.close()
    return total
