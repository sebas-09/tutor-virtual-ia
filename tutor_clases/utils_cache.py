# utils_cache.py (versión optimizada 2025-10-22)
# -----------------------------------------------------------
# Módulo de caché LLM: guarda respuestas del modelo
# para mejorar rendimiento y reducir latencia.

import hashlib
import time
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from models import LlmCache


def cache_key(model: str, prompt: str) -> str:
    """Genera una clave hash SHA256 única para (modelo + prompt)."""
    return hashlib.sha256(f"{model}||{prompt}".encode("utf-8")).hexdigest()


def get_cached(db: Session, key: str) -> str | None:
    """Devuelve una respuesta del caché si no ha expirado."""
    row = db.execute(select(LlmCache).where(LlmCache.hash == key)).scalar_one_or_none()
    if not row:
        return None

    # Verifica expiración por TTL
    if row.ttl_s and (time.time() - row.created_at.timestamp()) > row.ttl_s:
        db.execute(delete(LlmCache).where(LlmCache.hash == key))
        db.commit()
        return None
    return row.response


def put_cache(
    db: Session, key: str, model: str, prompt: str, response: str, ttl_s: int = 86400
):
    """Guarda o actualiza una entrada de caché."""
    db.merge(
        LlmCache(
            hash=key,
            model=model,
            prompt=prompt,
            response=response,
            ttl_s=ttl_s,
        )
    )
    db.commit()
