"""Lectura del buzon de alertas por IMAP (solo lectura: no marca, no borra).

Solo se leen las subcarpetas de la carpeta raiz (IMAP_CARPETA, por defecto "Empleos"): una por
usuario, p. ej. "Empleos/Jeronimo Pinto" y "Empleos/Helen Visbal". En Gmail son etiquetas, que un
filtro aplica a las alertas (deliveredto:dstogether01+alias@gmail.com). El resto del buzon -correo
personal o de otros usos de la cuenta- nunca se abre.
"""
import base64
import email
import imaplib
import logging
import re
import unicodedata
from collections.abc import Iterator
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage

from app.config import config

log = logging.getLogger("worker.correo")


def _utf7_imap(nombre: str) -> str:
    """Decodifica nombres de carpeta IMAP (UTF-7 modificado): 'Jer&APM-nimo' -> 'Jerónimo'."""
    def parte(m: re.Match) -> str:
        if m.group(1) == "":
            return "&"
        b64 = m.group(1).replace(",", "/")
        return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")
    return re.sub(r"&([A-Za-z0-9+,]*)-", parte, nombre)


def normalizar(texto: str) -> str:
    """'Jerónimo Pinto' -> 'jeronimopinto' (para comparar carpeta con alias o nombre del usuario)."""
    plano = unicodedata.normalize("NFKD", texto.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in plano if not unicodedata.combining(c)))


_LISTA = re.compile(rb'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<nombre>.+)$')


def subcarpetas(imap: imaplib.IMAP4, raiz: str) -> list[tuple[str, str]]:
    """[(nombre_imap, nombre_legible_sin_raiz)] de las subcarpetas directas de `raiz`.
    Sin distinguir mayusculas: 'Empleos', 'EMPLEOS' o 'empleos' sirven igual."""
    _, datos = imap.list()
    resultado = []
    for linea in datos or []:
        if not isinstance(linea, bytes):
            continue
        m = _LISTA.match(linea)
        if not m or b"\\Noselect" in m["flags"]:
            continue
        nombre = m["nombre"].decode().strip('"')
        separador = m["sep"].decode() or "/"
        prefijo = raiz + separador
        if nombre.lower().startswith(prefijo.lower()) and separador not in nombre[len(prefijo):]:
            resultado.append((nombre, _utf7_imap(nombre[len(prefijo):])))
    return resultado


def mensajes_por_carpeta(dias: int) -> Iterator[tuple[str, EmailMessage]]:
    """(nombre legible de la subcarpeta, mensaje) de los ultimos `dias` dias, solo dentro de la raiz."""
    cfg = config()
    desde = (date.today() - timedelta(days=dias)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL(cfg.imap_servidor) as imap:
        imap.login(cfg.imap_usuario, cfg.imap_clave_aplicacion)
        carpetas = subcarpetas(imap, cfg.imap_carpeta)
        if not carpetas:
            log.warning("No hay subcarpetas en '%s': cree una etiqueta por usuario (p. ej. '%s/Jeronimo Pinto')",
                        cfg.imap_carpeta, cfg.imap_carpeta)
        for nombre_imap, legible in carpetas:
            estado, _ = imap.select(f'"{nombre_imap}"', readonly=True)
            if estado != "OK":
                log.warning("No se pudo abrir la carpeta %s", legible)
                continue
            _, datos = imap.search(None, f'(SINCE "{desde}")')
            for num in datos[0].split():
                _, partes = imap.fetch(num, "(BODY.PEEK[])")
                yield legible, email.message_from_bytes(partes[0][1], policy=policy.default)


_ALIAS = re.compile(r"\+([a-z0-9._-]+)@", re.I)


def alias_destino(msg: EmailMessage, buzon: str) -> str | None:
    """Alias (+xxx) al que llego el correo, buscando en las cabeceras de entrega."""
    usuario_buzon = buzon.split("@")[0].lower()
    for cabecera in ("Delivered-To", "X-Forwarded-To", "X-Original-To", "To", "Cc"):
        for valor in msg.get_all(cabecera, []) or []:
            for m in _ALIAS.finditer(str(valor)):
                inicio = str(valor).lower().rfind(usuario_buzon, 0, m.start() + 1)
                if inicio != -1:
                    return m.group(1).lower()
    return None
