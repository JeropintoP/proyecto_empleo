from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    zona_horaria: str = "America/Bogota"

    jwt_secreto: str                         # obligatorio: openssl rand -hex 32
    jwt_horas: int = 72

    ia_proveedor: str = "groq"
    groq_api_key: str = ""
    groq_modelo: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_modelo: str = "gemini-flash-latest"

    jooble_api_key: str = ""
    jooble_host: str = "jooble.org"          # si Jooble entrega una key por pais: co.jooble.org
    careerjet_api_key: str = ""
    careerjet_locale: str = "es_CO"
    careerjet_user_ip: str = ""              # IP publica del servidor
    careerjet_referer: str = "https://jeropintop.github.io/"  # el sitio registrado en Careerjet

    dominio: str = ""
    healthcheck_url: str = ""      # p. ej. https://hc-ping.com/<uuid> (Healthchecks.io, gratis)

    smtp_servidor: str = "smtp.gmail.com"
    smtp_puerto: int = 587

    imap_servidor: str = "imap.gmail.com"
    imap_usuario: str = ""
    imap_clave_aplicacion: str = ""
    imap_carpeta: str = "Empleos"          # solo se leen sus subcarpetas (una por usuario)

    @field_validator("jwt_secreto")
    @classmethod
    def _secreto_fuerte(cls, v: str) -> str:
        # Con un secreto debil o el de ejemplo (que esta en el repositorio publico) cualquiera
        # podria fabricar una sesion valida.
        if len(v) < 32 or v.startswith("cambiar_por"):
            raise ValueError("JWT_SECRETO debe ser aleatorio y de al menos 32 caracteres: "
                             "genere uno con `openssl rand -hex 32` y pongalo en el .env")
        return v


@lru_cache
def config() -> Config:
    return Config()
