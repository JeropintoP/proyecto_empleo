"""Lectores de alertas por portal.

Cada lector recibe el mensaje ya parseado y devuelve una lista de VacanteExtraida.
Las URL se guardan LIMPIAS: los correos traen tokens de seguimiento y, en el caso
de LinkedIn, un token de inicio de sesion (otpToken) que nunca debe almacenarse.
"""
import re
import unicodedata
import urllib.parse
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from worker.correo.modelos import VacanteExtraida


# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------
def _fecha_correo(msg: EmailMessage) -> datetime | None:
    try:
        return parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        return None


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def fecha_relativa(texto: str, base: datetime | None) -> datetime | None:
    """'Hoy', 'Ayer', 'Hace 3 dias', 'Hace 5 horas' -> datetime aproximado."""
    if base is None or not texto:
        return None
    t = _sin_tildes(texto.lower().strip())
    if t.startswith("hoy"):
        return base
    if t.startswith("ayer"):
        return base - timedelta(days=1)
    m = re.search(r"hace\s+(\d+)\s+(minuto|hora|dia|semana|mes)", t)
    if m:
        n, unidad = int(m.group(1)), m.group(2)
        delta = {"minuto": timedelta(minutes=n), "hora": timedelta(hours=n),
                 "dia": timedelta(days=n), "semana": timedelta(weeks=n),
                 "mes": timedelta(days=30 * n)}[unidad]
        return base - delta
    return None


def salario_cop(texto: str | None) -> int | None:
    """'$ 2.500.000,00 (Mensual)' -> 2500000"""
    if not texto:
        return None
    m = re.search(r"\$\s*([\d.]+)", texto)
    return int(m.group(1).replace(".", "")) if m else None


def modalidad_desde(texto: str | None) -> str | None:
    if not texto:
        return None
    t = _sin_tildes(texto.lower())
    if "remot" in t:
        return "remoto"
    if "hibrid" in t:
        return "hibrido"
    if "presencial" in t:
        return "presencial"
    return None


def _texto_propio(raiz, etiqueta: str, estilo: str, contiene: str | None = None) -> str | None:
    """Texto directo (no de los hijos) del primer elemento cuyo estilo coincide.
    Evita mezclar, por ejemplo, la calificacion '4.4' con el nombre de la empresa."""
    for el in raiz.find_all(etiqueta, style=re.compile(estilo, re.I)):
        texto = " ".join(t.strip() for t in el.find_all(string=True, recursive=False) if t.strip())
        texto = re.sub(r"\[if mso\].*?\[endif\]", "", texto).strip()
        if texto and (contiene is None or contiene in texto):
            return texto
    return None


# ---------------------------------------------------------------------
# Computrabajo
# ---------------------------------------------------------------------
_CT_ID = re.compile(r"-([0-9A-F]{32})(?:$|\?)")


def leer_computrabajo(msg: EmailMessage) -> list[VacanteExtraida]:
    cuerpo = msg.get_body(("html",))
    if cuerpo is None:
        return []
    sopa = BeautifulSoup(cuerpo.get_content(), "lxml")
    base = _fecha_correo(msg)
    vacantes: dict[str, VacanteExtraida] = {}

    for a in sopa.find_all("a", href=True):
        if "o_detail" not in a["href"]:
            continue
        destino = urllib.parse.parse_qs(urllib.parse.urlparse(a["href"]).query).get("url", [""])[0]
        destino = destino.split("?")[0]
        m = _CT_ID.search(destino + "?")
        titulo_el = a.find("strong")
        if not m or titulo_el is None:
            continue  # enlaces del mismo bloque sin contenido (imagen, boton)
        id_portal = m.group(1)
        if id_portal in vacantes:
            continue

        empresa = _texto_propio(a, "span", r"line-height:\s*22px")
        ubicacion = _texto_propio(a, "td", r"font-size:\s*16px")
        salario_txt = _texto_propio(a, "td", r"font-size:\s*13px;\s*padding-bottom", contiene="$")
        fecha_txt = _texto_propio(a, "td", r"color:\s*#4B5968")
        etiquetas = [s.get_text(strip=True) for s in a.find_all("span", style=re.compile(r"background-color"))]

        vacantes[id_portal] = VacanteExtraida(
            portal="computrabajo",
            id_portal=id_portal,
            titulo=titulo_el.get_text(" ", strip=True),
            url=destino,
            empresa=empresa,
            ubicacion=ubicacion,
            salario_texto=salario_txt,
            salario_min=salario_cop(salario_txt),
            fecha_publicacion=fecha_relativa(fecha_txt or "", base),
            extra={"etiquetas": etiquetas} if etiquetas else {},
        )
    return list(vacantes.values())


# ---------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------
# Bloque en texto plano:
#   <titulo>
#   <empresa> · <ubicacion> (<modalidad>)
#   Ver
#   https://www.linkedin.com/comm/jobs/view/<id>/?...
_LI_BLOQUE = re.compile(
    r"^(?P<titulo>[^\n]+)\n(?P<detalle>[^\n]*·[^\n]*)\n(?:[^\n]*\n)??\s*"
    r"https?://www\.linkedin\.com/(?:comm/)?jobs/view/(?P<id>\d+)",
    re.M,
)


def leer_linkedin(msg: EmailMessage) -> list[VacanteExtraida]:
    cuerpo = msg.get_body(("plain",))
    if cuerpo is None:
        return []
    texto = cuerpo.get_content().replace("\r\n", "\n")
    vacantes: dict[str, VacanteExtraida] = {}

    for m in _LI_BLOQUE.finditer(texto):
        id_portal = m.group("id")
        if id_portal in vacantes:
            continue
        empresa, _, ubicacion = (p.strip() for p in m.group("detalle").partition("·"))
        vacantes[id_portal] = VacanteExtraida(
            portal="linkedin",
            id_portal=id_portal,
            titulo=m.group("titulo").strip(),
            url=f"https://www.linkedin.com/jobs/view/{id_portal}/",
            empresa=empresa or None,
            ubicacion=re.sub(r"\s*\([^)]*\)\s*$", "", ubicacion) or None,
            modalidad=modalidad_desde(ubicacion),
            fecha_publicacion=None,
        )
    return list(vacantes.values())


# ---------------------------------------------------------------------
# Elempleo (pendiente de un correo de ejemplo)
# ---------------------------------------------------------------------
def leer_elempleo(msg: EmailMessage) -> list[VacanteExtraida]:
    raise NotImplementedError("Lector de Elempleo pendiente: falta un correo .eml de ejemplo")


# Dominio del remitente -> lector
LECTORES = {
    "linkedin.com": leer_linkedin,
    "computrabajo.com": leer_computrabajo,
    "elempleo.com": leer_elempleo,
}


def lector_para(remitente: str):
    remitente = remitente.lower()
    for dominio, lector in LECTORES.items():
        if dominio in remitente:
            return lector
    return None
