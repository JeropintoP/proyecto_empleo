"""Recolectores de Jooble y Careerjet con respuestas simuladas (no sale a internet)."""
import base64
import json
import os

import httpx
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")

JOOBLE = {"totalCount": 2, "jobs": [
    {"id": 7712, "title": "Administrador de <b>Bases de Datos</b>", "company": "Energía Caribe",
     "location": "Cartagena, Bolívar", "snippet": "Gestión de PostgreSQL y respaldos&nbsp;diarios",
     "salary": "$ 4.000.000", "link": "https://co.jooble.org/desc/7712", "type": "Tiempo completo",
     "updated": "2026-11-14T08:00:00.0000000", "source": "elempleo.com"},
    {"id": 7713, "title": "Analista de datos remoto", "company": "", "location": "Colombia",
     "snippet": "SQL y Power BI", "salary": "", "link": "https://co.jooble.org/desc/7713",
     "updated": "2026-11-13T10:00:00"},
]}

CAREERJET = {"type": "JOBS", "hits": 1, "jobs": [
    {"title": "Ingeniero de integración", "company": "Tecnología Costera", "date": "Fri, 13 Nov 2026 19:13:43 GMT",
     "description": "APIs REST y <b>Python</b>", "locations": "Cartagena", "salary": "COP 5.000.000 mensual",
     "salary_min": 5000000, "salary_max": 6000000, "site": "computrabajo.com",
     "url": "https://jobviewtrack.com/v2/abc"},
]}


def transporte(registro: list, respuestas: dict):
    def manejar(req: httpx.Request) -> httpx.Response:
        registro.append(req)
        codigo, cuerpo = respuestas[req.url.host]
        return httpx.Response(codigo, json=cuerpo)
    return httpx.Client(transport=httpx.MockTransport(manejar))


@pytest.fixture
def entorno(monkeypatch):
    from app.config import config
    from app.db import sesion_transaccion

    monkeypatch.setattr(config(), "jooble_api_key", "KEY-J")
    monkeypatch.setattr(config(), "careerjet_api_key", "KEY-C")
    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados, ejecuciones CASCADE"))
        uid = s.execute(text("INSERT INTO usuarios (nombre, email, password_hash, alias_correo) "
                             "VALUES ('Jero', 'j@t.local', 'x', 'jeronimopinto') RETURNING id")).scalar()
        pid = s.execute(text("INSERT INTO perfiles (usuario_id) VALUES (:u) RETURNING id"), {"u": uid}).scalar()
        for codigo in ("jooble", "careerjet"):
            for i, (k, u) in enumerate([("DBA", "Cartagena"), ("analista de datos", "Colombia"),
                                        ("ingeniero de integracion", "Cartagena")]):
                s.execute(text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion, creada_en)
                                  SELECT :p, id, :k, :u, now() - make_interval(mins => :i)
                                    FROM fuentes WHERE codigo = :c"""),
                          {"p": pid, "k": k, "u": u, "c": codigo, "i": 10 - i})
        s.execute(text("UPDATE parametros SET valor = '2' WHERE clave = 'consultas_diarias_jooble'"))
    return uid


def test_jooble_guarda_y_rota(entorno):
    from app.db import sesion_transaccion
    from worker.recolectores_api import recolectar_jooble

    peticiones = []
    r = recolectar_jooble(transporte(peticiones, {"jooble.org": (200, JOOBLE)}))
    assert r == {"consultas": 2, "leidas": 4, "nuevas": 2, "reprocesadas": 0}  # misma oferta en 2 busquedas
    assert str(peticiones[0].url) == "https://jooble.org/api/KEY-J"
    assert json.loads(peticiones[0].content) == {"keywords": "DBA", "location": "Cartagena", "page": "1"}

    with sesion_transaccion() as s:
        v = s.execute(text("SELECT * FROM vacantes WHERE id_externo = 'jooble:7712'")).mappings().one()
        assert v["titulo"] == "Administrador de Bases de Datos"           # sin HTML
        assert v["descripcion"] == "Gestión de PostgreSQL y respaldos diarios"
        assert v["salario_min"] == 4_000_000 and v["empresa"] == "Energía Caribe"
        assert s.execute(text("SELECT modalidad::text FROM vacantes WHERE id_externo = 'jooble:7713'")).scalar() == "remoto"
        assert s.execute(text("SELECT count(*) FROM coincidencias WHERE usuario_id = :u"), {"u": entorno}).scalar() == 2

    # Siguiente corrida: rota a la busqueda que no se habia consultado
    peticiones.clear()
    recolectar_jooble(transporte(peticiones, {"jooble.org": (200, JOOBLE)}))
    assert json.loads(peticiones[0].content)["keywords"] == "ingeniero de integracion"


def test_careerjet_parametros_y_campos(entorno):
    from app.db import sesion_transaccion
    from worker.recolectores_api import recolectar_careerjet

    peticiones = []
    r = recolectar_careerjet(transporte(peticiones, {"search.api.careerjet.net": (200, CAREERJET)}))
    assert r["consultas"] == 3 and r["nuevas"] == 1
    p = peticiones[0]
    assert p.url.path == "/v4/query"
    assert p.url.params["locale_code"] == "es_CO" and p.url.params["keywords"] == "DBA"
    assert p.url.params["location"] == "Cartagena" and "user_ip" in p.url.params
    assert p.headers["Authorization"] == "Basic " + base64.b64encode(b"KEY-C:").decode()
    assert "location" not in peticiones[1].url.params                        # "Colombia" = todo el pais
    with sesion_transaccion() as s:
        v = s.execute(text("SELECT * FROM vacantes WHERE fuente_id = (SELECT id FROM fuentes WHERE codigo='careerjet')")
                      ).mappings().one()
        assert (v["salario_min"], v["salario_max"]) == (5_000_000, 6_000_000)
        assert v["descripcion"] == "APIs REST y Python" and v["fecha_publicacion"].day == 13


def test_key_invalida_detiene_la_fuente(entorno):
    from worker.recolectores_api import FuenteDetenida, recolectar_jooble

    peticiones = []
    with pytest.raises(FuenteDetenida, match="revise la API key"):
        recolectar_jooble(transporte(peticiones, {"jooble.org": (403, {})}))
    assert len(peticiones) == 1                                               # no sigue insistiendo


def test_registro_condicional(monkeypatch):
    """Sin key la fuente no se registra; asi no aparece como error en cada corrida."""
    import importlib

    from app.config import config
    from worker import tareas

    monkeypatch.setattr(config(), "jooble_api_key", "")
    monkeypatch.setattr(config(), "careerjet_api_key", "x")
    importlib.reload(tareas)
    assert set(tareas.RECOLECTORES) == {"correo", "getonbrd", "careerjet"}


GETONBRD = {"data": [
    {"id": "analista-de-datos-acme-bogota", "type": "job",
     "attributes": {"title": "Analista de Datos", "description": "<p>SQL y <b>Power BI</b></p>",
                    "functions": "<ul><li>Modelar datos</li></ul>", "benefits": "", "projects": "", "desirable": "",
                    "remote": True, "remote_modality": "fully_remote", "countries": ["Colombia", "Perú"],
                    "min_salary": 1500, "max_salary": 2200, "published_at": 1795000000,
                    "category_name": "Data Science / Analytics",
                    "company": {"data": {"id": "acme", "type": "company", "attributes": {"name": "ACME LatAm"}}}},
     "links": {"public_url": "https://www.getonbrd.com/jobs/analista-de-datos-acme-bogota"}},
    {"id": "dba-hibrido", "type": "job",
     "attributes": {"title": "DBA PostgreSQL", "description": "", "functions": "", "remote": False,
                    "remote_modality": "hybrid", "countries": ["Colombia"], "min_salary": 0, "max_salary": 0,
                    "published_at": 1795000000, "company": {"data": {"attributes": {"name": "Datos SAS"}}}},
     "links": {"public_url": "https://www.getonbrd.com/jobs/dba-hibrido"}},
], "meta": {"page": 1, "per_page": 40, "total_pages": 1}}


def test_getonbrd_sin_key(entorno):
    from app.db import sesion_transaccion
    from worker.recolectores_api import recolectar_getonbrd

    with sesion_transaccion() as s:   # la migracion 004 copia las busquedas de Jooble a Get on Board
        s.execute(text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion)
                          SELECT perfil_id, (SELECT id FROM fuentes WHERE codigo='getonbrd'), palabras_clave, ubicacion
                            FROM busquedas WHERE fuente_id = (SELECT id FROM fuentes WHERE codigo='jooble')"""))
    peticiones = []
    r = recolectar_getonbrd(transporte(peticiones, {"www.getonbrd.com": (200, GETONBRD)}))
    assert r["consultas"] == 3 and r["nuevas"] == 2
    p = peticiones[0]
    assert p.url.path == "/api/v0/search/jobs" and "Authorization" not in p.headers
    assert p.url.params["country_code"] == "COL" and p.url.params["query"] == "DBA"
    with sesion_transaccion() as s:
        v = s.execute(text("SELECT * FROM vacantes WHERE id_externo = 'getonbrd:analista-de-datos-acme-bogota'")
                      ).mappings().one()
        assert (v["empresa"], v["modalidad"], v["ubicacion"]) == ("ACME LatAm", "remoto", "Colombia, Perú")
        assert v["salario_texto"] == "USD 1.500–2.200 (mensual)" and v["salario_min"] is None   # no se mezcla con COP
        assert v["descripcion"] == "SQL y Power BI Modelar datos"
        h = s.execute(text("SELECT modalidad::text, salario_texto FROM vacantes WHERE id_externo = 'getonbrd:dba-hibrido'")).one()
        assert h == ("hibrido", None)
