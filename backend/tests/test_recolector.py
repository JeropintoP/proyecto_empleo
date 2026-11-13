"""Prueba de integracion contra PostgreSQL (requiere DATABASE_URL con el esquema cargado)."""
import os

import pytest
from sqlalchemy import text

from tests.test_lectores import cargar

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")


@pytest.fixture
def entorno():
    from app.db import sesion_transaccion
    from app.seguridad import hash_clave

    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados CASCADE"))
        for nombre, alias in (("Jerónimo Pinto", "jeronimopinto"), ("Helen Visbal", "helenvisbal")):
            s.execute(
                text("INSERT INTO usuarios (nombre, email, password_hash, alias_correo) "
                     "VALUES (:n, :e, :h, :a)"),
                {"n": nombre, "e": f"{alias}@test.local", "h": hash_clave("x" * 10), "a": alias},
            )
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo='correo'")).scalar_one()
    return sesion_transaccion, fuente_id


def test_flujo_reproceso(entorno):
    from worker.correo.recolector import procesar_mensaje

    sesion, fuente_id = entorno
    msg = cargar("computrabajo_1.eml")

    with sesion() as s:  # 1. primera vez: todo nuevo
        r = procesar_mensaje(s, fuente_id, msg, alias="jeronimopinto")
    assert (r["nuevas"], r["reprocesadas"]) == (7, 0)

    with sesion() as s:  # 2. mismo correo al dia siguiente: < 3 dias, se omite
        r = procesar_mensaje(s, fuente_id, msg, alias="jeronimopinto")
    assert (r["nuevas"], r["reprocesadas"]) == (0, 0)

    with sesion() as s:  # 3. pasan 3 dias y siguen abiertas: se reprocesan
        s.execute(text("UPDATE vacantes SET procesada_en = now() - interval '3 days'"))
        r = procesar_mensaje(s, fuente_id, msg, alias="jeronimopinto")
    assert r["reprocesadas"] == 7

    with sesion() as s:  # 4. una cerrada no se reprocesa
        s.execute(text("UPDATE vacantes SET procesada_en = now() - interval '4 days'"))
        s.execute(text("UPDATE vacantes SET abierta = FALSE WHERE id = (SELECT id FROM vacantes LIMIT 1)"))
        r = procesar_mensaje(s, fuente_id, msg, alias="jeronimopinto")
    assert r["reprocesadas"] == 6

    with sesion() as s:  # 5. la misma vacante para Helen: coincidencia propia, sin duplicar la vacante
        r = procesar_mensaje(s, fuente_id, msg, alias="helenvisbal")
        assert r["nuevas"] == 7
        assert s.execute(text("SELECT count(*) FROM vacantes")).scalar() == 7
        assert s.execute(text("SELECT count(*) FROM coincidencias")).scalar() == 14
        assert s.execute(text("SELECT max(veces_mostrada) FROM coincidencias")).scalar() == 3
        assert s.execute(text("SELECT count(*) FROM correos_procesados")).scalar() == 5


def test_ventana_de_lectura(entorno):
    from worker.correo.recolector import dias_a_leer

    sesion, fuente_id = entorno
    with sesion() as s:
        s.execute(text("DELETE FROM ejecuciones"))
        assert dias_a_leer(s, fuente_id) == 14                     # nunca ha corrido
        s.execute(text("INSERT INTO ejecuciones (fuente_id, estado, fin) VALUES (:f, 'ok', now() - interval '1 hour')"),
                  {"f": fuente_id})
        assert dias_a_leer(s, fuente_id) == 3                      # corrio ayer: minimo 3
        s.execute(text("UPDATE ejecuciones SET fin = now() - interval '9 days 2 hours'"))
        assert dias_a_leer(s, fuente_id) == 11                     # PC apagado 9 dias: 10 + 1 de margen
        s.execute(text("UPDATE ejecuciones SET fin = now() - interval '90 days'"))
        assert dias_a_leer(s, fuente_id) == 30                     # tope
        s.execute(text("INSERT INTO ejecuciones (fuente_id, estado, fin) VALUES (:f, 'error', now())"),
                  {"f": fuente_id})
        assert dias_a_leer(s, fuente_id) == 30                     # las fallidas no cuentan
