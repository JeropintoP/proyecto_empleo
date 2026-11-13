import email
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from worker.correo.imap import alias_destino
from worker.correo.lectores import fecha_relativa, lector_para, salario_cop

FIX = Path(__file__).parent / "fixtures"


def cargar(nombre: str) -> EmailMessage:
    ruta = FIX / nombre
    if not ruta.exists():
        pytest.skip(f"Falta el ejemplo {nombre} (los .eml no se suben al repositorio)")
    return email.message_from_binary_file(open(ruta, "rb"), policy=policy.default)


def test_computrabajo():
    msg = cargar("computrabajo_1.eml")
    vacantes = lector_para(msg["From"])(msg)
    assert len(vacantes) == 7
    v = vacantes[0]
    assert v.titulo == "Ingeniero de Sistemas / Medio Tiempo"
    assert v.empresa == "GRUPO EXPRO"
    assert v.ubicacion == "Bogotá, D.C., Bogotá, D.C."
    assert v.salario_min == 2_500_000
    assert v.url.startswith("https://co.computrabajo.com/ofertas-de-trabajo/") and "?" not in v.url
    # La calificacion (4.4) no debe mezclarse con la empresa
    assert vacantes[2].empresa == "Global SAS"


def test_linkedin_sin_tokens():
    msg = cargar("linkedin_1.eml")
    vacantes = lector_para(msg["From"])(msg)
    assert len(vacantes) == 4
    wood = next(v for v in vacantes if v.id_portal == "4487015251")
    assert (wood.titulo, wood.empresa, wood.ubicacion, wood.modalidad) == (
        "Project Engineering IV", "Wood", "Cartagena de Indias", "presencial")
    assert all("otpToken" not in v.url and "?" not in v.url for v in vacantes)


def test_utilidades():
    from datetime import datetime, timezone
    base = datetime(2026, 11, 12, tzinfo=timezone.utc)
    assert fecha_relativa("Hace 3 días", base).day == 9
    assert fecha_relativa("Ayer", base).day == 11
    assert salario_cop("$ 4.500.000,00 (Mensual)") == 4_500_000


def test_alias_reenvio():
    msg = EmailMessage()
    msg["Delivered-To"] = "dstogether01+helenvisbal@gmail.com"
    msg["To"] = "helen@correo.com"
    assert alias_destino(msg, "dstogether01@gmail.com") == "helenvisbal"
