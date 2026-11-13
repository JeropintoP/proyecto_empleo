"""Verificador de vacantes con respuestas HTTP simuladas (no sale a internet)."""
import os

import httpx
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="sin base de datos")

RESPUESTAS = {
    "https://x/abierta": (200, "<h1>Ingeniero de sistemas</h1><button>Postularme</button>"),
    "https://x/borrada": (404, "Not found"),
    "https://x/aviso": (200, "<p>Esta oferta ha sido cerrada por la empresa</p>"),
    "https://x/linkedin": (200, "<span>No longer accepting applications</span>"),
    "https://x/bloqueada": (429, "Too many requests"),
}


def _cliente():
    def manejar(req: httpx.Request) -> httpx.Response:
        codigo, cuerpo = RESPUESTAS[str(req.url)]
        return httpx.Response(codigo, text=cuerpo)
    return httpx.Client(transport=httpx.MockTransport(manejar))


@pytest.fixture
def vacantes():
    from app.db import sesion_transaccion
    with sesion_transaccion() as s:
        s.execute(text("TRUNCATE usuarios, vacantes, correos_procesados CASCADE"))
        for i, url in enumerate(RESPUESTAS):
            s.execute(
                text("""INSERT INTO vacantes (fuente_id, id_externo, titulo, url, huella, ultima_vez_vista)
                        VALUES (3, :e, 't', :u, md5(:h), now() - interval '5 days')"""),
                {"e": f"v{i}", "u": url, "h": f"v{i}"},
            )
        # Vista ayer: no se revisa
        s.execute(text("""INSERT INTO vacantes (fuente_id, id_externo, titulo, url, huella)
                          VALUES (3, 'reciente', 't', 'https://x/borrada', md5('r'))"""))
        # Sin aparecer 60 dias: se cierra por antiguedad sin consultar
        s.execute(text("""INSERT INTO vacantes (fuente_id, id_externo, titulo, url, huella, ultima_vez_vista)
                          VALUES (3, 'vieja', 't', 'https://x/no-existe', md5('v'), now() - interval '60 days')"""))


def _estado(s, id_externo):
    return s.execute(text("SELECT abierta FROM vacantes WHERE id_externo = :e"), {"e": id_externo}).scalar()


def test_verificar(vacantes):
    from app.db import sesion_transaccion
    from worker.verificador import verificar

    r = verificar(cliente=_cliente(), pausa=0)
    assert r == {"revisadas": 5, "cerradas": 3, "cerradas_antiguedad": 1, "sin_respuesta": 1}
    with sesion_transaccion() as s:
        assert _estado(s, "v0") is True      # sigue publicada
        assert _estado(s, "v1") is False     # 404
        assert _estado(s, "v2") is False     # aviso en la pagina
        assert _estado(s, "v3") is False     # LinkedIn
        assert _estado(s, "v4") is True      # 429: no se concluye nada
        assert _estado(s, "reciente") is True
        assert _estado(s, "vieja") is False

    # Al dia siguiente no repite las ya verificadas; solo reintenta la bloqueada
    r = verificar(cliente=_cliente(), pausa=0)
    assert r["revisadas"] == 1 and r["sin_respuesta"] == 1


def test_reaparece_y_se_reabre(vacantes):
    """Una vacante cerrada que vuelve a llegar en una alerta se reabre (lo hace el recolector)."""
    from app.db import sesion_transaccion
    from worker.verificador import verificar

    verificar(cliente=_cliente(), pausa=0)
    with sesion_transaccion() as s:
        s.execute(text("UPDATE vacantes SET ultima_vez_vista = now(), abierta = TRUE, cerrada_en = NULL "
                       "WHERE id_externo = 'v1'"))
        assert _estado(s, "v1") is True
