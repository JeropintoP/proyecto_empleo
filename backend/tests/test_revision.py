"""Pruebas de las correcciones de la revision de codigo (seguridad y duplicados)."""
import os
from email.message import EmailMessage

import pytest
from sqlalchemy import text

con_bd = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")


def test_jwt_secreto_debil_rechazado(monkeypatch):
    from pydantic import ValidationError

    from app.config import Config

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@y/z")
    for debil in ("corto", "cambiar_por_texto_aleatorio_largo_xxxxxxxxxxxxxx"):
        monkeypatch.setenv("JWT_SECRETO", debil)
        with pytest.raises(ValidationError, match="openssl rand"):
            Config(_env_file=None)
    monkeypatch.setenv("JWT_SECRETO", "f" * 64)
    assert Config(_env_file=None).jwt_secreto == "f" * 64


def _usuarios(s):
    from app.seguridad import hash_clave
    s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados CASCADE"))
    return [s.execute(text("""INSERT INTO usuarios (nombre, email, password_hash, alias_correo)
                              VALUES (:a, :e, :h, :a) RETURNING id"""),
                      {"a": a, "e": f"{a}@t.local", "h": hash_clave("clave-segura-1")}).scalar()
            for a in ("jeronimopinto", "helenvisbal")]


@con_bd
def test_misma_oferta_en_dos_portales_no_se_duplica():
    from app.db import sesion_transaccion
    from worker.correo.modelos import VacanteExtraida
    from worker.correo.recolector import guardar_vacante

    with sesion_transaccion() as s:
        jero, helen = _usuarios(s)
        f = dict(s.execute(text("SELECT codigo, id FROM fuentes")).all())
        a = VacanteExtraida(portal="jooble", id_portal="1", titulo="Ingeniero Mecánico", url="https://a/1",
                            empresa="Astilleros S.A.S.", ubicacion="Cartagena")
        b = VacanteExtraida(portal="careerjet", id_portal="9", titulo="INGENIERO MECANICO", url="https://b/9",
                            empresa="Astilleros SAS", ubicacion="Cartagena de Indias, Bolívar")
        sin_empresa = VacanteExtraida(portal="spe", id_portal="7", titulo="Ingeniero mecánico", url="https://c/7")

        assert guardar_vacante(s, f["jooble"], jero, a) == "nueva"
        assert guardar_vacante(s, f["careerjet"], jero, b) == "duplicada"     # mismo cargo y empresa
        assert guardar_vacante(s, f["spe"], jero, sin_empresa) == "nueva"     # sin empresa no se compara
        assert guardar_vacante(s, f["careerjet"], helen, b) == "nueva"        # Helen no la tenia
        assert guardar_vacante(s, f["jooble"], helen, a) == "duplicada"       # ...y ahora le llega por Jooble
        assert s.execute(text("SELECT count(*) FROM coincidencias WHERE usuario_id = :u"), {"u": jero}).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM coincidencias WHERE usuario_id = :u"), {"u": helen}).scalar() == 1

        # Si la que tenia se cerro, la que llega por otro portal si se le muestra
        s.execute(text("UPDATE vacantes SET abierta = FALSE WHERE id_externo = 'jooble:1'"))
        c = VacanteExtraida(portal="getonbrd", id_portal="x", titulo="Ingeniero mecánico", url="https://d/x",
                            empresa="ASTILLEROS SAS BIC", ubicacion="Remoto")
        assert guardar_vacante(s, f["getonbrd"], jero, c) == "nueva"
        assert guardar_vacante(s, f["getonbrd"], helen, c) == "duplicada"   # Helen tiene abierta la de Careerjet

@con_bd
def test_correo_con_dmarc_fallido_se_descarta():
    from app.db import sesion_transaccion
    from worker.correo.recolector import procesar_mensaje

    msg = EmailMessage()
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["To"] = "dstogether01+jeronimopinto@gmail.com"
    msg["Authentication-Results"] = "mx.google.com; spf=fail smtp.mailfrom=linkedin.com; dmarc=fail (p=REJECT) header.from=linkedin.com"
    msg.set_content("Ingeniero https://www.linkedin.com/comm/jobs/view/123")
    with sesion_transaccion() as s:
        _usuarios(s)
        fuente = s.execute(text("SELECT id FROM fuentes WHERE codigo = 'correo'")).scalar()
        assert procesar_mensaje(s, fuente, msg, alias="jeronimopinto") == {
            "omitido": "remitente no autenticado (DMARC fallido)"}


@con_bd
def test_login_bloquea_tras_intentos_fallidos():
    from fastapi.testclient import TestClient

    from app.db import sesion_transaccion
    from app.main import app
    from app.rutas import sesion as modulo

    with sesion_transaccion() as s:
        _usuarios(s)
    modulo._fallos.clear()
    c = TestClient(app)
    malo = {"email": "JeronimoPinto@t.local ", "clave": "equivocada"}
    for _ in range(modulo.MAX_FALLOS):
        assert c.post("/api/auth/login", json=malo).status_code == 401
    bueno = {"email": "jeronimopinto@t.local", "clave": "clave-segura-1"}
    assert c.post("/api/auth/login", json=bueno).status_code == 429          # bloqueado aunque acierte
    assert c.post("/api/auth/login", json={**bueno, "email": "helenvisbal@t.local"}).status_code == 200
    modulo._fallos.clear()
    assert c.post("/api/auth/login", json=bueno).status_code == 200

    # Otros ajustes: salud sin detalles e id invalido -> 422 (antes 500)
    assert c.get("/api/salud").json() == {"estado": "ok", "base_datos": "ok"}
    token = c.post("/api/auth/login", json=bueno).json()["token"]
    r = c.patch("/api/coincidencias/no-es-uuid", headers={"Authorization": f"Bearer {token}"}, json={"estado": "vista"})
    assert r.status_code == 422
    r = c.get("/api/coincidencias", params={"buscar": "100%_"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_verificador_no_consulta_red_interna(monkeypatch):
    import socket

    import httpx

    from worker.verificador import _solo_publicas, estado_enlace

    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a: [(0, 0, 0, "", ("169.254.169.254", 0))])
    llamadas = []
    cliente = httpx.Client(transport=httpx.MockTransport(lambda r: llamadas.append(r) or httpx.Response(200)),
                           event_hooks={"request": [_solo_publicas]})
    assert estado_enlace(cliente, "http://169.254.169.254/latest/meta-data") == "desconocido"
    assert llamadas == []
