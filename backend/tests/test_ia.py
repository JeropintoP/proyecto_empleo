"""Pruebas del modulo de IA con un proveedor simulado (no consume cuota)."""
import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")


class ProveedorFalso:
    nombre, modelo = "falso", "modelo-prueba"

    def __init__(self, fallar: bool = False):
        self.fallar, self.llamadas = fallar, []

    def completar_json(self, sistema, usuario):
        from app.ia.proveedor import ErrorIA
        self.llamadas.append(sistema[:30])
        if self.fallar:
            raise ErrorIA("429 cuota agotada")
        if "hoja de vida" in sistema:
            return {"resumen": "Ingeniero de sistemas con experiencia en bases de datos.",
                    "cargos_objetivo": ["Administrador de bases de datos", "Analista de datos"],
                    "habilidades": [{"nombre": "PostgreSQL", "nivel": "avanzado"}],
                    "anios_experiencia": 2, "nivel_educativo": "Profesional",
                    "idiomas": [{"idioma": "Ingles", "nivel": "B2"}]}, {"entrada": 100, "salida": 50}
        if "consultas" in sistema:
            return {"busquedas": [{"palabras_clave": "DBA", "ubicacion": "Cartagena"},
                                  {"palabras_clave": "analista de datos", "ubicacion": "Colombia"}]}, {}
        return {"puntaje": 82, "explicacion": "Encaja bien.", "fortalezas": ["SQL"],
                "brechas": ["Ingles C1", "x", "y", "z"]}, {}


@pytest.fixture
def usuario():
    from app.db import sesion_transaccion
    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados, uso_ia CASCADE"))
        uid = s.execute(text("""INSERT INTO usuarios (nombre, email, password_hash, alias_correo)
                                VALUES ('Jerónimo Pinto', 'j@test.local', 'x', 'jeronimopinto') RETURNING id""")).scalar()
        s.execute(text("INSERT INTO perfiles (usuario_id, palabras_excluir) VALUES (:u, ARRAY['vendedor'])"), {"u": uid})
        fuente = s.execute(text("SELECT id FROM fuentes WHERE codigo='correo'")).scalar()
        for i, titulo in enumerate(["DBA PostgreSQL", "Vendedor de mostrador", "Analista de datos"]):
            vid = s.execute(text("""INSERT INTO vacantes (fuente_id, id_externo, titulo, url, huella)
                                    VALUES (:f, :e, :t, 'u', md5(:h)) RETURNING id"""),
                            {"f": fuente, "e": f"t:{i}", "t": titulo, "h": titulo}).scalar()
            s.execute(text("INSERT INTO coincidencias (usuario_id, vacante_id) VALUES (:u, :v)"), {"u": uid, "v": vid})
    return uid


def test_perfil_y_busquedas(usuario):
    from app.db import sesion_transaccion
    from app.ia.servicios import generar_busquedas, perfilar_cv

    falso = ProveedorFalso()
    perfilar_cv(usuario, "texto del cv " * 30, "cv.pdf", lista=[falso])
    generar_busquedas(usuario, lista=[falso])
    with sesion_transaccion() as s:
        p = s.execute(text("SELECT cargos_objetivo, modelo_ia, habilidades FROM perfiles WHERE usuario_id=:u"),
                      {"u": usuario}).one()
        assert p.cargos_objetivo == ["Administrador de bases de datos", "Analista de datos"]
        assert p.modelo_ia == "falso/modelo-prueba"
        # 2 busquedas x 2 fuentes API (Jooble, Careerjet)
        assert s.execute(text("SELECT count(*) FROM busquedas")).scalar() == 4
        assert s.execute(text("SELECT count(*) FROM uso_ia WHERE exito")).scalar() == 2

    generar_busquedas(usuario, lista=[falso])  # regenerar no duplica
    with sesion_transaccion() as s:
        assert s.execute(text("SELECT count(*) FROM busquedas")).scalar() == 4


def test_evaluar_con_exclusion_y_respaldo(usuario):
    from app.db import sesion_transaccion
    from app.ia.servicios import evaluar_pendientes

    caido, respaldo = ProveedorFalso(fallar=True), ProveedorFalso()
    r = evaluar_pendientes(lista=[caido, respaldo])
    assert r == {"evaluadas": 2, "excluidas": 1, "errores": 0}
    assert len(respaldo.llamadas) == 2  # la vacante excluida no gasto cuota
    with sesion_transaccion() as s:
        filas = dict(s.execute(text("""SELECT v.titulo, c.puntaje FROM coincidencias c
                                         JOIN vacantes v ON v.id = c.vacante_id""")).all())
        assert filas == {"DBA PostgreSQL": 82, "Analista de datos": 82, "Vendedor de mostrador": 0}
        assert s.execute(text("SELECT max(cardinality(brechas)) FROM coincidencias")).scalar() == 3
        assert s.execute(text("SELECT count(*) FROM uso_ia WHERE NOT exito")).scalar() == 2


def test_cuota_agotada_se_detiene(usuario):
    from app.ia.servicios import evaluar_pendientes
    r = evaluar_pendientes(lista=[ProveedorFalso(fallar=True)])
    assert r["errores"] == 2 and r["evaluadas"] == 0  # quedan pendientes para manana
