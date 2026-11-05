import pickle
import re
from pathlib import Path
from typing import List, Dict
import numpy as np
import faiss
import google.generativeai as genai
from config import settings

# ======== CONFIGURACIÓN DE RUTAS ========
BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"  # Ruta donde se guardan FAISS y metadatos


# ======== FUNCIONES AUXILIARES ========
def _load_index():
    """Carga el índice FAISS y el metadato correspondiente."""
    index_path = STORAGE_DIR / "computacion.faiss"
    meta_path = STORAGE_DIR / "computacion_meta.pkl"

    if not index_path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            "No se encontraron los archivos del índice FAISS o metadatos."
        )

    index = faiss.read_index(str(index_path))
    with open(meta_path, "rb") as f:
        meta = pickle.load(f)

    if index.ntotal != len(meta):
        raise ValueError("Indice y metadatos desalineados; vuelve a ejecutar ingest.py.")

    return index, meta


def _embed_query(q: str) -> np.ndarray:
    """Genera el embedding para la pregunta del usuario."""
    genai.configure(api_key=settings.GENAI_API_KEY)
    out = genai.embed_content(
        model=settings.EMBEDDING_MODEL,
        content=q,
        task_type="retrieval_query",
    )
    v = np.array(out["embedding"], dtype="float32")
    faiss.normalize_L2(v.reshape(1, -1))
    return v


def retrieve(question: str, k=5) -> List[Dict]:
    """
    Recupera los k fragmentos más relevantes del índice FAISS
    según la similitud con la pregunta.
    """
    index, meta = _load_index()
    v = _embed_query(question)
    D, I = index.search(v.reshape(1, -1), k)

    hits = []
    for score, idx in zip(D[0].tolist(), I[0].tolist()):
        if idx == -1:
            continue
        m = meta[idx].copy()
        m["score"] = float(score)
        hits.append(m)

    return hits


def answer(question: str) -> str:
    """
    Genera la respuesta final basada en los textos más relevantes
    y el modelo de generación de Google Gemini.
    """
    contexts = retrieve(question, k=6)
    context_text = "\n\n---\n\n".join([c["text"] for c in contexts if c.get("text")])

    # Mensaje del sistema: define tono y estilo institucional
    system = (
        f"Eres un asistente virtual de {settings.INSTITUTION_NAME}. "
        "Responde de forma clara, ordenada y con tono profesional, como si fueras parte del personal académico. "
        "No menciones nombres de archivos, rutas internas ni códigos técnicos. "
        "Usa subtítulos y viñetas cuando sea apropiado para la comprensión. "
        "Si la información no está disponible o no existe en el contexto, dilo con amabilidad "
        "y sugiere contactar a la Secretaría de Carrera o al Director correspondiente."
    )

    # Prompt que se envía al modelo Gemini
    prompt = f"""{system}

Pregunta: {question}

Contexto interno (extractos relevantes):
{context_text}

Instrucciones:
- Responde en español neutro.
- No incluyas nombres de archivos, rutas ni etiquetas internas.
- Si es un trámite, explica los pasos concretos.
- Si no hay información suficiente, indícalo amablemente.
- Estructura el texto de manera ordenada y legible.
"""

    # ======== GENERACIÓN CON GEMINI ========
    genai.configure(api_key=settings.GENAI_API_KEY)
    model = genai.GenerativeModel(settings.GENAI_MODEL)
    resp = model.generate_content(prompt)
    text = (resp.text or "").strip()

    # Limpieza final por seguridad (por si el modelo cita algo interno)
    text = re.sub(r"\((?:md:[^)]+|departamento_yaml_raw|faq)[^)]+\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text
