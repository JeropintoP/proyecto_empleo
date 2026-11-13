"""Proveedores de IA intercambiables (planes gratuitos).

Todos exponen `completar_json(sistema, usuario) -> dict`. El proveedor activo se elige
con IA_PROVEEDOR en el .env. Si el principal falla (cuota agotada, caida), se intenta
con el de respaldo cuando tiene llave configurada.
"""
import json
import logging
import re
import time
from typing import Protocol

import httpx
from sqlalchemy import text

from app.config import config
from app.db import sesion_transaccion

log = logging.getLogger("ia")


class ErrorIA(Exception):
    pass


class Proveedor(Protocol):
    nombre: str
    modelo: str

    def completar_json(self, sistema: str, usuario: str) -> tuple[dict, dict]:
        """Devuelve (respuesta_json, uso{entrada,salida})."""


def _a_json(texto: str) -> dict:
    """Acepta JSON puro o envuelto en ```json ... ```."""
    texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ErrorIA(f"La respuesta no contiene JSON: {texto[:200]}")
    return json.loads(m.group(0))


def _post_con_reintentos(url: str, **kwargs) -> httpx.Response:
    for intento in range(3):
        r = httpx.post(url, timeout=90, **kwargs)
        if r.status_code == 429 or r.status_code >= 500:
            espera = float(r.headers.get("retry-after", 5 * (intento + 1)))
            log.warning("IA %s (intento %s), esperando %.0fs", r.status_code, intento + 1, espera)
            time.sleep(min(espera, 60))
            continue
        return r
    return r


class Groq:
    nombre = "groq"

    def __init__(self, api_key: str, modelo: str):
        self.api_key, self.modelo = api_key, modelo

    def completar_json(self, sistema: str, usuario: str) -> tuple[dict, dict]:
        r = _post_con_reintentos(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.modelo,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": sistema},
                             {"role": "user", "content": usuario}],
            },
        )
        if r.status_code != 200:
            raise ErrorIA(f"Groq {r.status_code}: {r.text[:300]}")
        datos = r.json()
        uso = datos.get("usage", {})
        return _a_json(datos["choices"][0]["message"]["content"]), {
            "entrada": uso.get("prompt_tokens"), "salida": uso.get("completion_tokens")}


class Gemini:
    nombre = "gemini"

    def __init__(self, api_key: str, modelo: str):
        self.api_key, self.modelo = api_key, modelo

    def completar_json(self, sistema: str, usuario: str) -> tuple[dict, dict]:
        r = _post_con_reintentos(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.modelo}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": sistema}]},
                "contents": [{"role": "user", "parts": [{"text": usuario}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            },
        )
        if r.status_code != 200:
            raise ErrorIA(f"Gemini {r.status_code}: {r.text[:300]}")
        datos = r.json()
        uso = datos.get("usageMetadata", {})
        texto = datos["candidates"][0]["content"]["parts"][0]["text"]
        return _a_json(texto), {"entrada": uso.get("promptTokenCount"),
                                "salida": uso.get("candidatesTokenCount")}


def proveedores() -> list[Proveedor]:
    """Principal primero, respaldo despues (solo los que tienen llave)."""
    cfg = config()
    disponibles = {
        "groq": Groq(cfg.groq_api_key, cfg.groq_modelo) if cfg.groq_api_key else None,
        "gemini": Gemini(cfg.gemini_api_key, cfg.gemini_modelo) if cfg.gemini_api_key else None,
    }
    orden = [cfg.ia_proveedor] + [n for n in disponibles if n != cfg.ia_proveedor]
    lista = [disponibles[n] for n in orden if disponibles.get(n)]
    if not lista:
        raise ErrorIA("No hay proveedor de IA configurado (GROQ_API_KEY o GEMINI_API_KEY)")
    return lista


def _registrar_uso(p: Proveedor, tarea: str, uso: dict | None, error: str | None) -> None:
    with sesion_transaccion() as s:
        s.execute(
            text("""INSERT INTO uso_ia (proveedor, modelo, tarea, tokens_entrada, tokens_salida, exito, error)
                    VALUES (:p, :m, :t, :e, :s, :ok, :err)"""),
            {"p": p.nombre, "m": p.modelo, "t": tarea, "e": (uso or {}).get("entrada"),
             "s": (uso or {}).get("salida"), "ok": error is None, "err": error},
        )


def consultar(tarea: str, sistema: str, usuario: str, lista: list[Proveedor] | None = None) -> tuple[dict, str]:
    """Ejecuta la consulta con respaldo. Devuelve (json, 'proveedor/modelo')."""
    ultimo_error = None
    for p in lista or proveedores():
        try:
            respuesta, uso = p.completar_json(sistema, usuario)
            _registrar_uso(p, tarea, uso, None)
            return respuesta, f"{p.nombre}/{p.modelo}"
        except (ErrorIA, httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
            ultimo_error = exc
            log.warning("Fallo %s en %s: %s", p.nombre, tarea, exc)
            _registrar_uso(p, tarea, None, str(exc)[:500])
    raise ErrorIA(f"Todos los proveedores fallaron: {ultimo_error}")
