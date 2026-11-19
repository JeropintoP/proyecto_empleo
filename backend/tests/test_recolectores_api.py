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

# Estructura segun la documentacion oficial de Careerjet v4
CAREERJET = {"type": "JOBS", "hits": 2, "message": "2 matching jobs found", "pages": 1, "jobs": [
    {"title": "Ingeniero de integración", "company": "Tecnología Costera", "date": "Fri, 13 Nov 2026 19:13:43 GMT",
     "description": "APIs REST y <b>Python</b>", "locations": "Cartagena", "salary": "$5.000.000 - 6.000.000",
     "salary_currency_code": "COP", "salary_min": 5000000.0, "salary_max": 6000000.0, "salary_type": "M",
     "url": "https://jobviewtrack.com/v2/abc"},
    {"title": "Data Engineer", "company": "Global Co", "date": "Thu, 12 Nov 2026 10:00:00 GMT",
     "description": "Remote", "locations": "Colombia", "salary": "$30000 - 36000",
     "salary_currency_code": "USD", "salary_min": 30000.0, "salary_max": 36000.0, "salary_type": "Y",
     "url": "https://jobviewtrack.com/v2/def"},
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
    assert r["consultas"] == 3 and r["nuevas"] == 2
    p = peticiones[0]
    assert p.url.path == "/v4/query"
    assert p.url.params["locale_code"] == "es_CO" and p.url.params["keywords"] == "DBA"
    assert p.url.params["location"] == "Cartagena" and "user_ip" in p.url.params
    assert p.url.params["user_agent"] and p.url.params["fragment_size"] == "500"
    assert p.headers["Authorization"] == "Basic " + base64.b64encode(b"KEY-C:").decode()
    assert "location" not in peticiones[1].url.params                        # "Colombia" = todo el pais
    with sesion_transaccion() as s:
        v = s.execute(text("SELECT * FROM vacantes WHERE titulo = 'Ingeniero de integración'")).mappings().one()
        assert (v["salario_min"], v["salario_max"]) == (5_000_000, 6_000_000)     # COP mensual
        assert v["descripcion"] == "APIs REST y Python" and v["fecha_publicacion"].day == 13
        usd = s.execute(text("SELECT salario_min, salario_texto FROM vacantes WHERE titulo = 'Data Engineer'")).one()
        assert usd == (None, "$30000 - 36000")                                      # USD anual: solo texto


def test_careerjet_ubicacion_ambigua(entorno):
    from worker.recolectores_api import consultar_careerjet

    respuestas = iter([
        (200, {"type": "LOCATIONS", "locations": ["Cartagena, Bolívar", "Cartagena, Chile"],
               "message": "multiple locations found"}),
        (200, CAREERJET),
        (200, {"type": "LOCATIONS", "locations": [], "message": "no matching location found"}),
        (200, CAREERJET),
    ])
    peticiones = []

    def manejar(req):
        peticiones.append(req)
        codigo, cuerpo = next(respuestas)
        return httpx.Response(codigo, json=cuerpo)

    cliente = httpx.Client(transport=httpx.MockTransport(manejar))
    assert len(consultar_careerjet(cliente, "DBA", "Cartagena")) == 2
    assert peticiones[1].url.params["location"] == "Cartagena, Bolívar"          # primera opcion
    assert len(consultar_careerjet(cliente, "DBA", "Cartagenita")) == 2
    assert "location" not in peticiones[3].url.params                            # todo el pais


def test_careerjet_locale_invalido(entorno):
    from worker.recolectores_api import FuenteDetenida, consultar_careerjet

    cliente = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(400, text="Unsupported locale code")))
    with pytest.raises(FuenteDetenida, match="CAREERJET_LOCALE"):
        consultar_careerjet(cliente, "DBA", "Cartagena")


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
    assert set(tareas.RECOLECTORES) == {"correo", "getonbrd", "himalayas", "jobicy", "empresas", "careerjet"}


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


def test_getonbrd_remotas_que_admiten_colombia():
    from worker.recolectores_api import consultar_getonbrd

    remotas = {"data": [
        {"id": "solo-chile", "attributes": {"title": "Dev", "countries": ["Chile"], "remote": True},
         "links": {"public_url": "https://www.getonbrd.com/jobs/solo-chile"}},
        {"id": "latam", "attributes": {"title": "Data Analyst", "countries": ["Chile", "Colombia", "Perú"],
                                       "remote": True, "remote_modality": "fully_remote"},
         "links": {"public_url": "https://www.getonbrd.com/jobs/latam"}},
        {"id": "mundial", "attributes": {"title": "SRE", "countries": [], "remote": True},
         "links": {"public_url": "https://www.getonbrd.com/jobs/mundial"}},
    ]}

    def manejar(req):
        return httpx.Response(200, json=remotas if req.url.params.get("remote") else {"data": []})

    ids = {v.id_portal for v in consultar_getonbrd(httpx.Client(transport=httpx.MockTransport(manejar)), "x", "")}
    assert ids == {"latam", "mundial"}                                       # descarta la solo-Chile


def test_getonbrd_sin_key(entorno):
    from app.db import sesion_transaccion
    from worker.recolectores_api import recolectar_getonbrd

    with sesion_transaccion() as s:   # la migracion 004 copia las busquedas de Jooble a Get on Board
        s.execute(text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion)
                          SELECT perfil_id, (SELECT id FROM fuentes WHERE codigo='getonbrd'), palabras_clave, ubicacion
                            FROM busquedas WHERE fuente_id = (SELECT id FROM fuentes WHERE codigo='jooble')"""))
    peticiones = []
    r = recolectar_getonbrd(transporte(peticiones, {"www.getonbrd.com": (200, GETONBRD)}))
    assert r["consultas"] == 3 and r["nuevas"] == 2                          # mismos ids: sin duplicados
    p, remota = peticiones[0], peticiones[1]
    assert p.url.path == "/api/v0/search/jobs" and "Authorization" not in p.headers
    assert p.url.params["country_code"] == "CO" and p.url.params["query"] == "DBA"   # 2 letras (COL -> 422)
    assert remota.url.params["remote"] == "true" and "country_code" not in remota.url.params
    with sesion_transaccion() as s:
        v = s.execute(text("SELECT * FROM vacantes WHERE id_externo = 'getonbrd:analista-de-datos-acme-bogota'")
                      ).mappings().one()
        assert (v["empresa"], v["modalidad"], v["ubicacion"]) == ("ACME LatAm", "remoto", "Colombia, Perú")
        assert v["salario_texto"] == "USD 1.500–2.200 (mensual)" and v["salario_min"] is None   # no se mezcla con COP
        assert v["descripcion"] == "SQL y Power BI Modelar datos"
        h = s.execute(text("SELECT modalidad::text, salario_texto FROM vacantes WHERE id_externo = 'getonbrd:dba-hibrido'")).one()
        assert h == ("hibrido", None)


# ----------------------------- Himalayas / Jobicy -----------------------------
HIMALAYAS = {"totalCount": 1, "jobs": [
    {"title": "Data Analyst", "companyName": "Remote Co", "excerpt": "SQL and dashboards",
     "description": "<p>Full</p>", "locationRestrictions": [{"alpha2": "CO", "name": "Colombia", "slug": "colombia"}],
     "minSalary": 30000, "maxSalary": 42000, "currency": "USD", "salaryPeriod": "annual",
     "pubDate": 1795000000, "applicationLink": "https://himalayas.app/companies/remote-co/jobs/data-analyst",
     "guid": "himalayas-123"},
]}

JOBICY = {"jobs": [
    {"id": 501, "url": "https://jobicy.com/jobs/501-senior-data-analyst", "jobTitle": "Senior Data Analyst",
     "companyName": "LatAm Data", "jobGeo": "LATAM", "jobExcerpt": "Power BI, SQL", "pubDate": "2026-11-18T10:00:00+00:00",
     "salaryMin": 2000, "salaryMax": 3000, "salaryCurrency": "USD", "salaryPeriod": "monthly"},
    {"id": 502, "url": "https://jobicy.com/jobs/502-graphic-designer", "jobTitle": "Graphic Designer",
     "companyName": "Studio", "jobGeo": "LATAM", "jobExcerpt": "Figma"},
    {"id": 503, "url": "https://jobicy.com/jobs/503-integration-engineer", "jobTitle": "Integration Engineer (APIs)",
     "companyName": "Conectar", "jobGeo": "Anywhere", "jobExcerpt": "REST, Python"},
]}


def _con_ingles():
    from app.db import sesion_transaccion
    with sesion_transaccion() as s:
        for codigo in ("himalayas", "jobicy"):
            s.execute(text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion)
                              SELECT perfil_id, (SELECT id FROM fuentes WHERE codigo = :c), palabras_clave, ubicacion
                                FROM busquedas WHERE fuente_id = (SELECT id FROM fuentes WHERE codigo='jooble')"""),
                      {"c": codigo})
        traduccion = {"DBA": "database administrator", "analista de datos": "data analyst",
                      "ingeniero de integracion": "integration engineer"}
        for es, en in traduccion.items():
            s.execute(text("UPDATE busquedas SET palabras_clave_en = :en WHERE palabras_clave = :es"), {"en": en, "es": es})


def test_himalayas_en_ingles_y_atribucion(entorno):
    from app.db import sesion_transaccion
    from worker.recolectores_api import recolectar_himalayas

    _con_ingles()
    peticiones = []
    r = recolectar_himalayas(transporte(peticiones, {"himalayas.app": (200, HIMALAYAS)}))
    assert r["consultas"] == 3 and r["nuevas"] == 1
    p = peticiones[0]
    assert p.url.path == "/jobs/api/search" and p.url.params["country"] == "CO"
    assert {q.url.params["q"] for q in peticiones} == {"database administrator", "data analyst", "integration engineer"}
    with sesion_transaccion() as s:
        v = s.execute(text("SELECT * FROM vacantes WHERE id_externo = 'himalayas:himalayas-123'")).mappings().one()
        assert v["url"] == HIMALAYAS["jobs"][0]["applicationLink"]          # terminos: postular por applicationLink
        assert (v["ubicacion"], v["modalidad"]) == ("Remoto: Colombia", "remoto")
        assert v["salario_texto"] == "USD 30.000–42.000 (anual)" and v["salario_min"] is None


def test_jobicy_una_consulta_y_filtro_local(entorno):
    from app.db import sesion_transaccion
    from worker.recolectores_api import recolectar_jobicy

    _con_ingles()
    peticiones = []
    r = recolectar_jobicy(transporte(peticiones, {"jobicy.com": (200, JOBICY)}))
    assert len(peticiones) == 1                                           # una sola consulta por corrida
    assert peticiones[0].url.params["geo"] == "latam"
    assert (r["leidas"], r["nuevas"]) == (3, 2)                           # el disenador no coincide
    with sesion_transaccion() as s:
        titulos = set(s.execute(text("SELECT titulo FROM vacantes")).scalars())
        assert titulos == {"Senior Data Analyst", "Integration Engineer (APIs)"}
        sal = s.execute(text("SELECT salario_texto FROM vacantes WHERE titulo = 'Senior Data Analyst'")).scalar()
        assert sal == "USD 2.000–3.000 (mensual)"
        s.execute(text("""INSERT INTO ejecuciones (fuente_id, estado, inicio, fin)
                          SELECT id, 'ok', now() - interval '10 minutes', now() FROM fuentes WHERE codigo='jobicy'"""))
    # Menos de una hora despues: no vuelve a consultar
    peticiones.clear()
    assert "omitida" in recolectar_jobicy(transporte(peticiones, {"jobicy.com": (200, JOBICY)}))
    assert peticiones == []


def test_coincidencia_de_terminos():
    from worker.recolectores_api import coincide
    assert coincide("data analyst", "Senior Data Analyst", None)
    assert coincide("data analyst", "Analyst II", "work with data pipelines")       # termino final en el titulo
    assert not coincide("data analyst", "Data Engineer", "analyst support")        # el cargo es otro
    assert not coincide("database administrator", "Graphic Designer", "admin of databases")
    assert coincide("IT support", "IT Support Specialist", None)


def test_completar_ingles(entorno):
    from app.db import sesion_transaccion
    from app.ia.servicios import completar_ingles

    class Traductor:
        nombre, modelo = "falso", "m"

        def completar_json(self, sistema, usuario):
            return {"traducciones": [{"es": "DBA", "en": "database administrator"},
                                     {"es": "analista de datos", "en": "data analyst"}]}, {}

    assert completar_ingles(lista=[Traductor()]) == 3
    with sesion_transaccion() as s:
        en = dict(s.execute(text("SELECT DISTINCT palabras_clave, palabras_clave_en FROM busquedas")).all())
    assert en["DBA"] == "database administrator" and en["analista de datos"] == "data analyst"
    assert en["ingeniero de integracion"] == "ingeniero de integracion"   # sin traduccion: queda el original
    assert completar_ingles(lista=[Traductor()]) == 0                     # ya no hay faltantes
