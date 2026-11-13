from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    zona_horaria: str = "America/Bogota"

    jwt_secreto: str = "solo-desarrollo"
    jwt_horas: int = 72

    ia_proveedor: str = "groq"
    groq_api_key: str = ""
    groq_modelo: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""

    jooble_api_key: str = ""
    careerjet_api_key: str = ""

    imap_servidor: str = "imap.gmail.com"
    imap_usuario: str = ""
    imap_clave_aplicacion: str = ""


@lru_cache
def config() -> Config:
    return Config()
