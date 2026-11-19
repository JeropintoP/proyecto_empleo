"""Monitor de empresas con respuestas simuladas (no sale a internet)."""
import os
import socket

import httpx
import pytest
from sqlalchemy import text

from worker.empresas import EmpresaInvalida, extraer_enlaces, validar, validar_url_publica

con_bd = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")

PAGINA = """
<html><body>
<nav><a href="/trabaja-con-nosotros/jobs/1-menu">Ingeniero en el menu de navegacion</a></nav>
<h1>Trabaja con nosotros</h1>
<a href="/trabaja-con-nosotros">Trabaja con nosotros</a>
<a href="/trabaja-con-nosotros/jobs/101-ingeniero-mecanico">Ingeniero mecánico de proyectos</a>
<a href="/trabaja-con-nosotros/jobs/102-inspector-hse">Inspector HSE</a>
<a href="/trabaja-con-nosotros/jobs/102-inspector-hse#aplicar">Postúlate</a>
<a href="/sites/default/files/OFERTA-LABORAL-TECNICO-NAVAL.pdf">Descargar</a>
<a href="/nosotros">Quiénes somos</a>
<a href="https://www.linkedin.com/company/astivik/jobs">Empleos en LinkedIn</a>
<a href="mailto:reclutamiento@empresa.com">Escríbanos</a>
<script>var a = '<a href="/jobs/999">Falso</a>';</script>
</body></html>
"""


def test_extraer_enlaces_de_pagina():
    enlaces = dict(extraer_enlaces(PAGINA, "https://empresa.buk.co/trabaja-con-nosotros"))
    assert enlaces == {
        "https://empresa.buk.co/trabaja-con-nosotros/jobs/101-ingeniero-mecanico": "Ingeniero mecánico de proyectos",
        "https://empresa.buk.co/trabaja-con-nosotros/jobs/102-inspector-hse": "Inspector HSE",
        "https://empresa.buk.co/sites/default/files/OFERTA-LABORAL-TECNICO-NAVAL.pdf": "OFERTA LABORAL TECNICO NAVAL",
    }


def test_validar_identificadores(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a: [(0, 0, 0, "", ("8.8.8.8", 0))])
    assert validar("smartrecruiters", "https://jobs.smartrecruiters.com/Konecranes/") == "Konecranes"
    assert validar("pagina", " https://www.dimar.mil.co/ofertas-de-empleo ") == "https://www.dimar.mil.co/ofertas-de-empleo"
    with pytest.raises(EmpresaInvalida):
        validar("successfactors", "https://career17.sapsf.com/career")  # falta ?company=
    with pytest.raises(EmpresaInvalida):
        validar("pagina", "ftp://empresa.com")


def test_bloquea_red_interna(monkeypatch):
    for ip in ("127.0.0.1", "10.0.0.5", "172.18.0.2", "169.254.169.254"):
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, ip=ip: [(0, 0, 0, "", (ip, 0))])
        with pytest.raises(EmpresaInvalida):
            validar_url_publica("http://db:5432/")


SR_PAG1 = {"offset": 0, "limit": 100, "totalFound": 2, "content": [
    {"id": "744000101", "name": "Service Technician", "releasedDate": "2026-11-10T14:00:00.000Z",
     "company": {"identifier": "Konecranes", "name": "Konecranes"},
     "location": {"city": "Cartagena", "region": "Bolívar", "country": "co", "remote": False},
     "function": {"label": "Service"}, "typeOfEmployment": {"label": "Full-time"}},
    {"id": "744000102", "name": "Field Engineer", "company": {"identifier": "Konecranes", "name": "Konecranes"},
     "location": {"city": "Bogotá", "country": "co", "remote": True}},
]}

SF_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Job-Listing>
  <Job><Requisition-ID>1234</Requisition-ID><Job-Title>Ingeniero de Produccion</Job-Title>
       <Location>Cartagena</Location><Posted-Date>2026-11-12</Posted-Date></Job>
  <Job><Requisition-ID>1235</Requisition-ID><Job-Title>Soldador calificado</Job-Title></Job>
</Job-Listing>"""


def _cliente(registro: list) -> httpx.Client:
    def manejar(req: httpx.Request) -> httpx.Response:
        registro.append(req)
        if req.url.host == "api.smartrecruiters.com":
            if "noexiste" in req.url.path:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=SR_PAG1)
        if req.url.host == "career17.sapsf.com":
            return httpx.Response(200, content=SF_XML, headers={"content-type": "text/xml"})
        if req.url.host == "empresa.buk.co":
            return httpx.Response(200, text=PAGINA, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(500)
    return httpx.Client(transport=httpx.MockTransport(manejar))


def test_smartrecruiters_y_successfactors():
    from worker.empresas import consultar_smartrecruiters, consultar_successfactors

    registro = []
    sr = consultar_smartrecruiters(_cliente(registro), {"nombre": "Konecranes", "identificador": "konecranes", "pais": "CO"})
    assert registro[0].url.params["country"] == "co"
    assert [v.titulo for v in sr] == ["Service Technician", "Field Engineer"]
    assert sr[0].url == "https://jobs.smartrecruiters.com/Konecranes/744000101"
    assert sr[0].ubicacion == "Cartagena, Bolívar" and sr[1].modalidad == "remoto"
    assert sr[0].descripcion == "Service · Full-time"

    sf = consultar_successfactors(_cliente(registro), {
        "nombre": "Cotecmar", "identificador": "https://career17.sapsf.com/career?company=cotecmarcoP"})
    assert registro[-1].url.params["career_ns"] == "job_listing_summary"
    assert [(v.titulo, v.ubicacion) for v in sf] == [("Ingeniero de Produccion", "Cartagena"), ("Soldador calificado", None)]
    assert sf[0].url.endswith("company=cotecmarcoP&career_ns=job_listing&career_job_req_id=1234")
    assert sf[0].id_externo == "successfactors:cotecmarcoP:1234"


@con_bd
def test_recolectar_empresas():
    from app.db import sesion_transaccion
    from worker.empresas import recolectar_empresas

    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, ejecuciones, empresas CASCADE"))
        for alias in ("jeronimopinto", "helenvisbal"):
            s.execute(text("INSERT INTO usuarios (nombre, email, password_hash, alias_correo) VALUES (:a, :e, 'x', :a)"),
                      {"a": alias, "e": f"{alias}@t.local"})
        s.execute(text("""INSERT INTO empresas (nombre, tipo, identificador, pais) VALUES
            ('Konecranes', 'smartrecruiters', 'konecranes', 'co'),
            ('Astivik', 'pagina', 'https://empresa.buk.co/trabaja-con-nosotros', NULL),
            ('Sin API', 'smartrecruiters', 'noexiste', NULL)"""))

    registro = []
    total = recolectar_empresas(_cliente(registro))
    assert total["consultas"] == 3 and total["errores"] == 1
    assert total["leidas"] == 5 and total["nuevas"] == 10   # 5 vacantes x 2 usuarios

    with sesion_transaccion() as s:
        estado = dict(s.execute(text("SELECT nombre, coalesce(ultimo_error, ultimo_total::text) FROM empresas")).all())
        assert estado["Konecranes"] == "2" and estado["Astivik"] == "3"
        assert "no tiene publicaciones" in estado["Sin API"]

    # Ya revisadas hoy: no se vuelven a consultar
    assert recolectar_empresas(_cliente(registro))["consultas"] == 0


@con_bd
def test_api_empresas(monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import sesion_transaccion
    from app.main import app
    from app.seguridad import hash_clave

    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a: [(0, 0, 0, "", ("8.8.8.8", 0))])
    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, empresas CASCADE"))
        for alias in ("jeronimopinto", "helenvisbal"):
            s.execute(text("INSERT INTO usuarios (nombre, email, password_hash, alias_correo) VALUES (:a, :e, :h, :a)"),
                      {"a": alias, "e": f"{alias}@t.local", "h": hash_clave("clave-segura-1")})
    c = TestClient(app)

    def token(alias):
        r = c.post("/api/auth/login", json={"email": f"{alias}@t.local", "clave": "clave-segura-1"})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    jero, helen = token("jeronimopinto"), token("helenvisbal")
    assert c.get("/api/empresas").status_code == 401

    r = c.post("/api/empresas", headers=jero, json={"nombre": "Dimar", "tipo": "pagina",
                                                     "identificador": "https://www.dimar.mil.co/ofertas-de-empleo"})
    assert r.status_code == 201 and r.json()["activa"] and not r.json()["solo_para_mi"]
    comun = r.json()["id"]
    assert c.post("/api/empresas", headers=jero, json={"nombre": "Dimar", "tipo": "pagina",
                  "identificador": "https://www.dimar.mil.co/ofertas-de-empleo"}).status_code == 409
    privada = c.post("/api/empresas", headers=jero, json={"nombre": "Konecranes", "tipo": "smartrecruiters",
                     "identificador": "konecranes", "pais": "CO", "solo_para_mi": True}).json()["id"]
    assert c.post("/api/empresas", headers=jero, json={"nombre": "X", "tipo": "pagina",
                  "identificador": "nada"}).status_code == 422

    # Helen ve la comun pero no la privada de Jeronimo
    assert [e["nombre"] for e in c.get("/api/empresas", headers=helen).json()] == ["Dimar"]
    assert c.delete(f"/api/empresas/{privada}", headers=helen).status_code == 404

    assert c.patch(f"/api/empresas/{comun}", headers=helen, json={"activa": False}).json()["activa"] is False
    assert c.delete(f"/api/empresas/{privada}", headers=jero).status_code == 204

    # Probar: sin guardar nada
    import worker.empresas as modulo
    monkeypatch.setattr(modulo, "cliente_publico", lambda: _cliente([]))
    r = c.post("/api/empresas/probar", headers=jero, json={"nombre": "Astivik", "tipo": "pagina",
               "identificador": "https://empresa.buk.co/trabaja-con-nosotros"}).json()
    assert r["total"] == 3 and r["error"] is None and r["vacantes"][0]["titulo"] == "Ingeniero mecánico de proyectos"
