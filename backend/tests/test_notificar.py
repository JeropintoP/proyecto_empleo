"""Resumen diario por correo y flujo completo de la tarea diaria (SMTP e IA simulados)."""
import os
from unittest import mock

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")


class SmtpFalso:
    enviados: list = []

    def __init__(self, fallar_con: str | None = None):
        self.fallar_con = fallar_con

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, usuario, clave):
        assert usuario and clave

    def send_message(self, msg):
        import smtplib
        if self.fallar_con and self.fallar_con in msg["To"]:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no existe")})
        SmtpFalso.enviados.append(msg)


@pytest.fixture
def datos(monkeypatch):
    from app.config import config
    from app.db import sesion_transaccion

    monkeypatch.setattr(config(), "imap_usuario", "dstogether01@gmail.com")
    monkeypatch.setattr(config(), "imap_clave_aplicacion", "clave-app")
    monkeypatch.setattr(config(), "dominio", "proyectoempleo.duckdns.org")
    SmtpFalso.enviados = []

    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados, notificaciones, ejecuciones CASCADE"))
        ids = {}
        for alias, email in (("jeronimopinto", "jero@test.local"), ("helenvisbal", "helen@test.local")):
            ids[alias] = s.execute(
                text("INSERT INTO usuarios (nombre, email, password_hash, alias_correo) "
                     "VALUES (:n, :e, 'x', :a) RETURNING id"),
                {"n": alias.capitalize(), "e": email, "a": alias}).scalar()
        filas = [("DBA <PostgreSQL>", 90, "nueva", True), ("Soporte TI", 65, "nueva", True),
                 ("Vendedor", 20, "nueva", True), ("Analista", 85, "vista", True), ("Cerrada", 95, "nueva", False)]
        for i, (titulo, puntaje, estado, abierta) in enumerate(filas):
            vid = s.execute(text("""INSERT INTO vacantes (fuente_id, id_externo, titulo, url, huella, abierta)
                                    VALUES (3, :e, :t, :url, md5(:h), :a) RETURNING id"""),
                            {"e": f"v{i}", "t": titulo, "h": titulo, "a": abierta, "url": f"https://x/v{i}"}).scalar()
            s.execute(text("""INSERT INTO coincidencias (usuario_id, vacante_id, puntaje, estado, explicacion)
                              VALUES (:u, :v, :p, CAST(:est AS estado_coincidencia), 'Encaja')"""),
                      {"u": ids["jeronimopinto"], "v": vid, "p": puntaje, "est": estado})
    return ids


def test_resumen_solo_lo_relevante(datos):
    from worker.notificar import enviar_resumenes

    r = enviar_resumenes(smtp_factory=SmtpFalso)
    assert r == {"enviados": 1, "sin_novedades": 1, "errores": 0}    # Helen no tiene vacantes
    msg = SmtpFalso.enviados[0]
    assert msg["To"] == "jero@test.local"
    assert msg["Subject"].startswith("2 vacantes")
    cuerpo = msg.get_body(("plain",)).get_content()
    assert "DBA <PostgreSQL>" in cuerpo and "Soporte TI" in cuerpo
    assert "Vendedor" not in cuerpo and "Analista" not in cuerpo and "Cerrada" not in cuerpo
    html = msg.get_body(("html",)).get_content()
    assert "DBA &lt;PostgreSQL&gt;" in html                         # escapado
    assert "https://proyectoempleo.duckdns.org" in html

    # Segunda corrida el mismo dia: nada nuevo, no se envia
    SmtpFalso.enviados = []
    assert enviar_resumenes(smtp_factory=SmtpFalso)["enviados"] == 0
    assert SmtpFalso.enviados == []


def test_reaparecida_se_vuelve_a_notificar(datos):
    from app.db import sesion_transaccion
    from worker.notificar import enviar_resumenes

    enviar_resumenes(smtp_factory=SmtpFalso)
    with sesion_transaccion() as s:
        s.execute(text("""UPDATE coincidencias c SET reaparecio_en = now() + interval '1 second', veces_mostrada = 2
                           FROM vacantes v WHERE v.id = c.vacante_id AND v.titulo = 'Soporte TI'"""))
    SmtpFalso.enviados = []
    enviar_resumenes(smtp_factory=SmtpFalso)
    cuerpo = SmtpFalso.enviados[0].get_body(("plain",)).get_content()
    assert "Soporte TI (sigue abierta)" in cuerpo and "DBA" not in cuerpo


def test_fallo_de_envio_no_marca_notificada(datos):
    from app.db import sesion_transaccion
    from worker.notificar import enviar_resumenes

    r = enviar_resumenes(smtp_factory=lambda: SmtpFalso(fallar_con="jero@"))
    assert r["errores"] == 1
    with sesion_transaccion() as s:
        assert s.execute(text("SELECT count(*) FROM coincidencias WHERE notificada_en IS NOT NULL")).scalar() == 0
        assert s.execute(text("SELECT exito FROM notificaciones")).scalar() is False


def test_tarea_diaria_completa(datos):
    """Recolector, verificador, IA y resumen encadenados; una fuente caida no detiene el resto."""
    from worker import tareas

    def recolector_roto():
        raise ConnectionError("IMAP caido")

    with mock.patch.dict(tareas.RECOLECTORES, {"correo": recolector_roto}, clear=True), \
         mock.patch.object(tareas, "verificar", return_value={"revisadas": 0}) as ver, \
         mock.patch.object(tareas, "evaluar_pendientes", return_value={"evaluadas": 0}) as ev, \
         mock.patch.object(tareas, "enviar_resumenes", side_effect=lambda: {"enviados": 1}) as env:
        tareas.actualizacion_diaria()
    ver.assert_called_once(); ev.assert_called_once(); env.assert_called_once()

    from app.db import sesion_transaccion
    with sesion_transaccion() as s:
        filas = s.execute(text("SELECT f.codigo, e.estado::text, e.error FROM ejecuciones e "
                               "JOIN fuentes f ON f.id = e.fuente_id")).all()
    assert [(c, e) for c, e, _ in filas] == [("correo", "error")]     # jooble/careerjet se omiten sin error
    assert "IMAP caido" in filas[0][2]
