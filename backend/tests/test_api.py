"""Pruebas de la API (requiere DATABASE_URL)."""
import os
from unittest import mock

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")


@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient

    from app.db import sesion_transaccion
    from app.main import app
    from app.seguridad import hash_clave

    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados, uso_ia CASCADE"))
        ids = {}
        for alias in ("jeronimopinto", "helenvisbal"):
            ids[alias] = s.execute(
                text("INSERT INTO usuarios (nombre, email, password_hash, alias_correo) "
                     "VALUES (:n, :e, :h, :a) RETURNING id"),
                {"n": alias, "e": f"{alias}@test.local", "h": hash_clave("clave-segura-1"), "a": alias},
            ).scalar()
            s.execute(text("INSERT INTO perfiles (usuario_id) VALUES (:u)"), {"u": ids[alias]})
        fuente = s.execute(text("SELECT id FROM fuentes WHERE codigo='correo'")).scalar()
        for i, (titulo, puntaje) in enumerate([("DBA PostgreSQL", 90), ("Soporte TI", 60), ("Vendedor", 5)]):
            vid = s.execute(
                text("""INSERT INTO vacantes (fuente_id, id_externo, titulo, empresa, url, huella, datos_originales)
                        VALUES (:f, :e, :t, 'ACME', 'https://x', md5(:h), '{"portal":"linkedin"}') RETURNING id"""),
                {"f": fuente, "e": f"t:{i}", "t": titulo, "h": titulo},
            ).scalar()
            s.execute(text("INSERT INTO coincidencias (usuario_id, vacante_id, puntaje) VALUES (:u, :v, :p)"),
                      {"u": ids["jeronimopinto"], "v": vid, "p": puntaje})
    return TestClient(app)


def _token(cliente, alias="jeronimopinto"):
    r = cliente.post("/api/auth/login", json={"email": f"{alias}@test.local", "clave": "clave-segura-1"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_y_proteccion(cliente):
    assert cliente.get("/api/coincidencias").status_code == 401
    with mock.patch("app.rutas.sesion.time.sleep"):
        assert cliente.post("/api/auth/login", json={"email": "jeronimopinto@test.local", "clave": "mal"}).status_code == 401
    assert cliente.get("/api/yo", headers=_token(cliente)).json()["alias_correo"] == "jeronimopinto"


def test_tablero_filtros_y_estados(cliente):
    h = _token(cliente)
    r = cliente.get("/api/coincidencias", headers=h).json()
    assert [i["titulo"] for i in r["items"]] == ["DBA PostgreSQL", "Soporte TI", "Vendedor"]
    assert r["items"][0]["portal"] == "linkedin"
    assert cliente.get("/api/coincidencias?min_puntaje=50", headers=h).json()["total"] == 2
    assert cliente.get("/api/coincidencias?buscar=dba", headers=h).json()["total"] == 1

    cid = r["items"][2]["id"]
    assert cliente.patch(f"/api/coincidencias/{cid}", headers=h, json={"estado": "descartada"}).status_code == 200
    assert cliente.get("/api/coincidencias", headers=h).json()["total"] == 2  # descartadas ocultas por defecto
    assert cliente.get("/api/coincidencias?estado=descartada", headers=h).json()["total"] == 1

    cid = r["items"][0]["id"]
    cliente.patch(f"/api/coincidencias/{cid}", headers=h, json={"estado": "postulada", "notas": "Enviada HV"})
    item = cliente.get("/api/coincidencias?estado=postulada", headers=h).json()["items"][0]
    assert item["notas"] == "Enviada HV" and item["postulada_en"] is not None
    assert cliente.get("/api/resumen", headers=h).json()["por_estado"] == {"nueva": 1, "postulada": 1, "descartada": 1}


def test_aislamiento_entre_usuarios(cliente):
    h_jero, h_helen = _token(cliente), _token(cliente, "helenvisbal")
    assert cliente.get("/api/coincidencias", headers=h_helen).json()["total"] == 0
    cid = cliente.get("/api/coincidencias", headers=h_jero).json()["items"][0]["id"]
    assert cliente.patch(f"/api/coincidencias/{cid}", headers=h_helen, json={"estado": "vista"}).status_code == 404


def test_perfil_preferencias(cliente):
    h = _token(cliente)
    r = cliente.put("/api/perfil", headers=h, json={
        "ciudades": ["Cartagena", " "], "modalidad": "hibrido", "salario_minimo": 3000000,
        "palabras_excluir": ["vendedor"]})
    assert r.status_code == 200
    p = r.json()
    assert (p["ciudades"], p["modalidad"], p["salario_minimo"]) == (["Cartagena"], "hibrido", 3000000)
    assert cliente.put("/api/perfil", headers=h, json={"modalidad": "otra"}).status_code == 422

    from app.db import sesion_transaccion
    with sesion_transaccion() as s:
        s.execute(text("""INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion)
                          SELECT p.id, 1, 'DBA', 'Cartagena' FROM perfiles p
                            JOIN usuarios u ON u.id = p.usuario_id WHERE u.alias_correo = 'jeronimopinto'"""))
    r = cliente.get("/api/perfil", headers=h)
    assert r.status_code == 200 and r.json()["busquedas"] == [{"palabras_clave": "DBA", "ubicacion": "Cartagena"}]


def test_subir_cv_valida_formato(cliente):
    h = _token(cliente)
    r = cliente.post("/api/perfil/cv", headers=h, files={"archivo": ("cv.exe", b"x", "application/octet-stream")})
    assert r.status_code == 400
