# app/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    GENAI_API_KEY: str
    GENAI_MODEL: str = "gemini-2.5-flash"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    INSTITUTION_NAME: str = "Instituto de Demostracion"
    APP_ENV: str = "dev"

    # Pydantic v2: usa SettingsConfigDict y referencia el .env por ruta absoluta
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # evita error si hay variables extra
    )


settings = Settings()
