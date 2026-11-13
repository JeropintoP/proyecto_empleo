"""Lectura del buzon de alertas por IMAP (solo lectura: no marca, no borra)."""
import email
import imaplib
import re
from collections.abc import Iterator
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage

from app.config import config


def mensajes_recientes(dias: int) -> Iterator[EmailMessage]:
    """Mensajes recibidos en los ultimos `dias` dias."""
    cfg = config()
    desde = (date.today() - timedelta(days=dias)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL(cfg.imap_servidor) as imap:
        imap.login(cfg.imap_usuario, cfg.imap_clave_aplicacion)
        imap.select("INBOX", readonly=True)
        _, datos = imap.search(None, f'(SINCE "{desde}")')
        for num in datos[0].split():
            _, partes = imap.fetch(num, "(BODY.PEEK[])")
            crudo = partes[0][1]
            yield email.message_from_bytes(crudo, policy=policy.default)


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
