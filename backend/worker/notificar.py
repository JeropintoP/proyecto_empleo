"""Resumen diario por correo.

A cada usuario le llegan sus vacantes 'nuevas' con puntaje >= umbral que aun no se le han
notificado (o que reaparecieron despues de la ultima notificacion). Se envia desde el buzon
del proyecto (dstogether01) al correo personal registrado en `usuarios.email`.
Si no hay nada que reportar, no se envia nada.
"""
import html
import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import text

from app.config import config
from app.db import sesion_transaccion

log = logging.getLogger("worker.notificar")


def _parametro(s, clave: str, defecto: int) -> int:
    v = s.execute(text("SELECT valor FROM parametros WHERE clave = :c"), {"c": clave}).scalar()
    return int(v) if v is not None else defecto


def vacantes_para(s, usuario_id, umbral: int, maximo: int) -> list[dict]:
    return [dict(f) for f in s.execute(
        text("""
            SELECT c.id, c.puntaje, c.explicacion, c.veces_mostrada,
                   v.titulo, v.empresa, v.ubicacion, v.salario_texto, v.url
              FROM coincidencias c JOIN vacantes v ON v.id = c.vacante_id
             WHERE c.usuario_id = :u AND v.abierta AND c.estado = 'nueva'
               AND c.puntaje >= :umbral
               AND (c.notificada_en IS NULL OR c.reaparecio_en > c.notificada_en)
             ORDER BY c.puntaje DESC, c.creada_en DESC
             LIMIT :max
        """),
        {"u": usuario_id, "umbral": umbral, "max": maximo},
    ).mappings().all()]


def armar_correo(nombre: str, destino: str, vacantes: list[dict]) -> EmailMessage:
    cfg = config()
    enlace_app = f"https://{cfg.dominio}" if cfg.dominio else None
    n = len(vacantes)
    primer_nombre = nombre.split()[0]

    msg = EmailMessage()
    msg["Subject"] = f"{n} vacante{'s' if n != 1 else ''} para revisar · Proyecto Empleo"
    msg["From"] = f"Proyecto Empleo <{cfg.imap_usuario}>"
    msg["To"] = destino

    lineas = [f"Hola {primer_nombre}, estas son sus mejores vacantes nuevas:", ""]
    for v in vacantes:
        extra = " (sigue abierta)" if v["veces_mostrada"] > 1 else ""
        lineas += [f"[{v['puntaje']}] {v['titulo']}{extra}",
                   f"    {v['empresa'] or 'Empresa no indicada'} · {v['ubicacion'] or ''}",
                   f"    {v['explicacion'] or ''}", f"    {v['url']}", ""]
    if enlace_app:
        lineas.append(f"Ver todas en {enlace_app}")
    msg.set_content("\n".join(lineas))

    e = html.escape
    filas = "".join(f"""
      <tr><td style="padding:14px 0 14px 16px;border-bottom:1px solid #e2e5eb;vertical-align:top;width:52px">
        <div style="width:42px;height:42px;border-radius:9px;text-align:center;line-height:42px;font-weight:700;
             background:{'#e3f4ea' if v['puntaje'] >= 80 else '#fdf1dc'};color:{'#1b7a46' if v['puntaje'] >= 80 else '#9a6200'}">
          {v['puntaje']}</div></td>
      <td style="padding:14px 16px 14px 12px;border-bottom:1px solid #e2e5eb">
        <a href="{e(v['url'])}" style="color:#1c2230;font-weight:600;font-size:15px;text-decoration:none">{e(v['titulo'])}</a>
        {'<span style="font-size:11px;color:#626b7a"> · sigue abierta</span>' if v['veces_mostrada'] > 1 else ''}
        <div style="color:#626b7a;font-size:13px">{e(v['empresa'] or 'Empresa no indicada')}
          {(' · ' + e(v['ubicacion'])) if v['ubicacion'] else ''}
          {(' · <b>' + e(v['salario_texto']) + '</b>') if v['salario_texto'] else ''}</div>
        <div style="font-size:13px;margin-top:4px">{e(v['explicacion'] or '')}</div>
      </td></tr>""" for v in vacantes)
    boton = (f'<p style="margin-top:20px"><a href="{e(enlace_app)}" style="background:#2459c9;color:#fff;'
             f'padding:10px 16px;border-radius:8px;text-decoration:none;font-size:14px">Abrir el tablero</a></p>'
             if enlace_app else "")
    msg.add_alternative(f"""<!DOCTYPE html><html><body style="margin:0;background:#f6f7f9;font-family:Arial,sans-serif;color:#1c2230">
      <div style="max-width:600px;margin:0 auto;padding:24px">
        <p style="font-size:15px">Hola {e(primer_nombre)}, estas son sus mejores vacantes nuevas:</p>
        <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:12px">{filas}</table>
        {boton}
        <p style="font-size:12px;color:#626b7a;margin-top:24px">Proyecto Empleo · resumen automático de las 6:00 a.m.</p>
      </div></body></html>""", subtype="html")
    return msg


def enviar_resumenes(smtp_factory=None) -> dict:
    cfg = config()
    resultado = {"enviados": 0, "sin_novedades": 0, "errores": 0}
    with sesion_transaccion() as s:
        umbral = _parametro(s, "umbral_notificacion", 60)
        maximo = _parametro(s, "maximo_notificacion", 10)
        usuarios = s.execute(text("SELECT id, nombre, email FROM usuarios WHERE activo AND notificar")).all()
        pendientes = {u.id: vacantes_para(s, u.id, umbral, maximo) for u in usuarios}

    if not any(pendientes.values()):
        resultado["sin_novedades"] = len(usuarios)
        return resultado
    if not cfg.imap_clave_aplicacion:
        log.warning("Sin IMAP_CLAVE_APLICACION: no se pueden enviar resumenes")
        return resultado

    fabrica = smtp_factory or (lambda: smtplib.SMTP(cfg.smtp_servidor, cfg.smtp_puerto, timeout=30))
    with fabrica() as smtp:
        smtp.starttls()
        smtp.login(cfg.imap_usuario, cfg.imap_clave_aplicacion)
        for u in usuarios:
            vacantes = pendientes[u.id]
            if not vacantes:
                resultado["sin_novedades"] += 1
                continue
            error = None
            try:
                smtp.send_message(armar_correo(u.nombre, u.email, vacantes))
                resultado["enviados"] += 1
            except smtplib.SMTPException as exc:
                error = str(exc)[:500]
                resultado["errores"] += 1
                log.error("No se pudo enviar el resumen a %s: %s", u.email, exc)
            with sesion_transaccion() as s:
                if error is None:
                    s.execute(text("UPDATE coincidencias SET notificada_en = now() WHERE id = ANY(:ids)"),
                              {"ids": [v["id"] for v in vacantes]})
                s.execute(text("INSERT INTO notificaciones (usuario_id, vacantes, exito, error) "
                               "VALUES (:u, :n, :ok, :err)"),
                          {"u": u.id, "n": len(vacantes), "ok": error is None, "err": error})
    log.info("Resumenes: %s", resultado)
    return resultado
