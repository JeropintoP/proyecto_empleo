"""Servicio Publico de Empleo (buscadordeempleo.gov.co).

El buscador oficial del SPE reune las vacantes de cajas de compensacion (Comfenalco, Comfamiliar),
SENA, alcaldias, bolsas de universidades y agencias privadas. Su servicio publico (sin clave) es:

    GET https://www.buscadordeempleo.gov.co/backbue/v1/vacantes/resultados?page=N&departamento=BOLÍVAR
    -> {"resultados": [...50 vacantes...], "totalPages", "currentPage", "total_registros", ...}

No tiene busqueda por texto, asi que se leen las vacantes de los departamentos configurados
(parametro `spe_departamentos`) y se filtran aqui con las busquedas de cada perfil, como Jobicy.
Para no cargar el servicio: como maximo `spe_max_paginas` paginas por corrida, una pausa entre
paginas y se deja de paginar al llegar a vacantes publicadas antes de la ultima revision.
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from app.db import sesion_transaccion
from worker.correo.modelos import VacanteExtraida
from worker.correo.recolector import guardar_vacante
from worker.recolectores_api import AGENTE, _fecha, _limpiar, _revisar_respuesta, coincide

log = logging.getLogger("worker.spe")

URL = "https://www.buscadordeempleo.gov.co/backbue/v1/vacantes/resultados"
BUSCADOR = "https://www.buscadordeempleo.gov.co/"
PAUSA_SEGUNDOS = 1.0


def _parametro(s, clave: str, defecto: str) -> str:
    return s.execute(text("SELECT valor FROM parametros WHERE clave = :c"), {"c": clave}).scalar() or defecto


def _reparar(texto: str | None) -> str | None:
    """Algunos campos llegan con tildes mal codificadas ('ingenierÃ­a')."""
    if texto and "Ã" in texto:
        try:
            return texto.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return texto


def _pesos(rango: str | None) -> tuple[int | None, int | None]:
    """'$1.500.001 - $2.000.000' -> (1500001, 2000000); 'Mayor de $15.000.001' -> (15000001, None)."""
    numeros = [int(n.replace(".", "")) for n in re.findall(r"\$\s*([\d.]+)", rango or "")]
    if not numeros:
        return None, None
    if len(numeros) == 1:
        return (numeros[0], None) if "mayor" in rango.lower() else (None, numeros[0])
    return numeros[0], numeros[1]


def convertir(v: dict) -> VacanteExtraida | None:
    codigo, titulo = v.get("CODIGO_VACANTE"), _limpiar(v.get("TITULO_VACANTE"))
    if not codigo or not titulo:
        return None
    prestadores = v.get("DETALLES_PRESTADOR") or []
    url = next((p.get("URL_DETALLE_VACANTE") for p in prestadores if p.get("URL_DETALLE_VACANTE")), None) or BUSCADOR
    prestador = next((p.get("NOMBRE_PRESTADOR") for p in prestadores if p.get("NOMBRE_PRESTADOR")), None)
    municipio, departamento = _limpiar(v.get("MUNICIPIO")), _limpiar(v.get("DEPARTAMENTO"))
    remoto = bool(v.get("TELETRABAJO"))
    detalles = [
        f"Publicada por: {prestador}" if prestador else None,
        f"Cargo: {v['CARGO']}" if v.get("CARGO") else None,
        f"Estudios: {v['NIVEL_ESTUDIOS']}" if v.get("NIVEL_ESTUDIOS") else None,
        f"Contrato: {v['TIPO_CONTRATO']}" if v.get("TIPO_CONTRATO") else None,
        f"Sector: {_reparar(v['SECTOR_ECONOMICO'])}" if v.get("SECTOR_ECONOMICO") else None,
        f"Vence: {v['FECHA_VENCIMIENTO'][:10]}" if v.get("FECHA_VENCIMIENTO") else None,
    ]
    salario_min, salario_max = _pesos(v.get("RANGO_SALARIAL"))
    return VacanteExtraida(
        portal="spe", id_portal=str(codigo), titulo=titulo, url=url,
        empresa=None,  # el SPE no publica el nombre del empleador
        ubicacion=", ".join(x.title() for x in (municipio, departamento) if x) or None,
        modalidad="remoto" if remoto else None,
        salario_texto=v.get("RANGO_SALARIAL") or None, salario_min=salario_min, salario_max=salario_max,
        fecha_publicacion=_fecha(v.get("FECHA_PUBLICACION")),
        descripcion=("\n".join(d for d in detalles if d) + "\n\n" + (_limpiar(_reparar(v.get("DESCRIPCION_VACANTE"))) or ""))
        .strip()[:3000] or None,
    )


def _vencida(v: dict, hoy: datetime) -> bool:
    vence = _fecha(v.get("FECHA_VENCIMIENTO"))
    return bool(vence and vence.date() < hoy.date())


def recolectar_spe(cliente: httpx.Client | None = None, pausa: float = PAUSA_SEGUNDOS) -> dict:
    total = {"consultas": 0, "leidas": 0, "nuevas": 0, "reprocesadas": 0}
    with sesion_transaccion() as s:
        fuente_id = s.execute(text("SELECT id FROM fuentes WHERE codigo = 'spe'")).scalar_one()
        departamentos = [d.strip() for d in _parametro(s, "spe_departamentos", "BOLÍVAR").split(",") if d.strip()]
        max_paginas = int(_parametro(s, "spe_max_paginas", "20"))
        ultima = s.execute(text("""SELECT max(inicio) FROM ejecuciones
                                    WHERE fuente_id = :f AND estado = 'ok'"""), {"f": fuente_id}).scalar()
        busquedas = s.execute(text("""
            SELECT DISTINCT p.usuario_id, b.palabras_clave AS termino
              FROM busquedas b JOIN perfiles p ON p.id = b.perfil_id
              JOIN usuarios u ON u.id = p.usuario_id AND u.activo
             WHERE b.fuente_id = :f AND b.activa"""), {"f": fuente_id}).all()
    if not busquedas or not departamentos:
        return total

    ahora = datetime.now(timezone.utc)
    # Margen de un dia: el SPE publica con fecha sin hora
    desde = (ultima - timedelta(days=1)) if ultima else ahora - timedelta(days=14)

    propio = cliente is None
    cliente = cliente or httpx.Client(timeout=30, headers={"User-Agent": AGENTE})
    try:
        for departamento in departamentos:
            for pagina in range(1, max_paginas + 1):
                if total["consultas"]:
                    time.sleep(pausa)
                r = cliente.get(URL, params={"page": pagina, "departamento": departamento})
                total["consultas"] += 1
                _revisar_respuesta(r, "Servicio Público de Empleo")
                datos = r.json()
                resultados = datos.get("resultados") or []
                total["leidas"] += len(resultados)

                with sesion_transaccion() as s:
                    for v in resultados:
                        if _vencida(v, ahora):
                            continue
                        vacante = convertir(v)
                        if not vacante:
                            continue
                        # El cargo cuenta como parte del titulo: muchos titulos del SPE son genericos ("Profesional")
                        titulo = f"{vacante.titulo} {v.get('CARGO') or ''}"
                        usuarios = {b.usuario_id for b in busquedas
                                    if coincide(b.termino, titulo, v.get("DESCRIPCION_VACANTE"))}
                        for usuario_id in usuarios:
                            resultado = guardar_vacante(s, fuente_id, usuario_id, vacante)
                            if resultado == "nueva":
                                total["nuevas"] += 1
                            elif resultado == "reprocesada":
                                total["reprocesadas"] += 1

                fechas = [f for f in (_fecha(v.get("FECHA_PUBLICACION")) for v in resultados) if f]
                if (not resultados or pagina >= int(datos.get("totalPages") or 0)
                        or (fechas and max(fechas) < desde)):
                    break  # sin mas paginas, o todo lo de esta pagina ya se habia revisado
    finally:
        if propio:
            cliente.close()
    return total
