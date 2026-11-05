# ingest.py  (VERSIÓN: v5-single-embed-compatible)
import os, csv, pickle, json
from pathlib import Path
from typing import List, Dict

import numpy as np
import faiss
import google.generativeai as genai

from config import settings


# ===============  LOG SEGURO (sin Unicode roto en Windows) ===============
def log(msg: str):
    """Imprime evitando errores Unicode en consola Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


# ===============  INICIO  ===============
log("=== [ingest] VERSION v5-single-embed-compatible ===")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

log(f"[ingest] BASE_DIR  = {BASE_DIR}")
log(f"[ingest] DATA_DIR  = {DATA_DIR} (exists={DATA_DIR.exists()})")
log(f"[ingest] STORAGE   = {STORAGE_DIR} (exists={STORAGE_DIR.exists()})")


# ===============  UTILIDADES  ===============
def load_csv(fp: Path):
    rows = []
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def load_markdowns(dirp: Path):
    docs = []
    md_files = sorted(dirp.glob("*.md"))
    log(f"[ingest] MD files in {dirp.name}: {[f.name for f in md_files]}")
    for fp in md_files:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            docs.append({"title": fp.stem, "content": f.read(), "source": str(fp)})
    return docs


def chunk_text(text: str, max_chars=1800, overlap=200) -> List[str]:
    """
    Divide texto en chunks con solapamiento sin riesgo de bucles infinitos.
    """
    text = text.strip()
    if not text:
        return []

    if max_chars <= overlap:
        overlap = max(0, min(overlap, max_chars - 1))

    chunks = []
    n = len(text)
    start = 0

    while start < n:
        end = min(n, start + max_chars)
        chunks.append(text[start:end])

        if end == n:
            break  # último chunk

        start = end - overlap
        if start < 0:
            start = 0

    return chunks


# ===============  CONSTRUCCIÓN DE CORPUS  ===============
def build_corpus() -> List[Dict]:
    corpus: List[Dict] = []

    # 1) YAML tratado como texto crudo
    yfp = DATA_DIR / "departamento.yaml"
    log(f"[ingest] YAML (raw) -> {yfp} (exists={yfp.exists()})")
    if yfp.exists():
        raw = yfp.read_text(encoding="utf-8-sig", errors="replace")
        preview = raw[:200].replace(os.linesep, " ")
        log(f"[ingest][YAML][RAW] bytes={len(raw.encode('utf-8'))}, preview={preview}")
        for i, ch in enumerate(chunk_text(raw)):
            corpus.append(
                {
                    "text": ch,
                    "section": "departamento_yaml_raw",
                    "source": str(yfp),
                    "idx": i,
                }
            )
        log(f"[ingest] YAML RAW OK -> {len(corpus)} chunks (hasta ahora)")

    # 2) CSV FAQ
    cfp = DATA_DIR / "faq.csv"
    log(f"[ingest] CSV  -> {cfp} (exists={cfp.exists()})")
    if cfp.exists():
        try:
            rows = load_csv(cfp)
            for row in rows:
                txt = (
                    f"PREGUNTA: {row.get('pregunta','').strip()}\n"
                    f"RESPUESTA: {row.get('respuesta','').strip()}\n"
                    f"ETIQUETAS: {row.get('etiquetas','')}"
                )
                corpus.append(
                    {
                        "text": txt,
                        "section": "faq",
                        "source": str(cfp),
                        "idx": row.get("pregunta", "")[:40],
                    }
                )
            log(f"[ingest] CSV OK -> +{len(rows)} filas")
        except Exception as e:
            log(f"[ingest][WARN] CSV inválido ({e}). Se omite.")

    # 3) Markdown
    sdir = DATA_DIR / "secciones"
    log(f"[ingest] MD dir -> {sdir} (exists={sdir.exists()})")
    if sdir.exists():
        try:
            for doc in load_markdowns(sdir):
                for i, ch in enumerate(chunk_text(doc["content"])):
                    corpus.append(
                        {
                            "text": ch,
                            "section": f"md:{doc['title']}",
                            "source": doc["source"],
                            "idx": i,
                        }
                    )
            log(f"[ingest] MD OK -> total chunks ahora: {len(corpus)}")
        except Exception as e:
            log(f"[ingest][WARN] Error leyendo MD ({e}).")

    log(f"[ingest] total chunks construidos: {len(corpus)}")
    return corpus


# ===============  EMBEDDINGS (uno por uno, compatible SDK)  ===============
def _extract_embedding(out_obj):
    """
    Normaliza distintas formas de respuesta del SDK de google.generativeai
    para obtener el vector de embedding (lista de floats).
    """
    # dict estilo {'embedding': [...]} o {'data': {'embedding': [...]}}
    if isinstance(out_obj, dict):
        if "embedding" in out_obj:
            return out_obj["embedding"]
        data = out_obj.get("data")
        if isinstance(data, dict) and "embedding" in data:
            return data["embedding"]
        # algunos retornan {'values': [...]}
        if "values" in out_obj:
            return out_obj["values"]

    # objeto con atributos
    emb = getattr(out_obj, "embedding", None)
    if emb is not None:
        return emb
    values = getattr(out_obj, "values", None)
    if values is not None:
        return values

    # si no encontramos nada, devolvemos None
    return None


def embed_texts(texts: List[str]) -> np.ndarray:
    """
    Genera embeddings (una llamada por texto) con reintentos.
    Evita batch_embed_contents porque no existe en tu SDK.
    """
    import time
    import math

    genai.configure(api_key=settings.GENAI_API_KEY)
    model = getattr(settings, "EMBEDDING_MODEL", "text-embedding-004")

    total = len(texts)
    all_vecs: List[List[float]] = []
    log(f"[ingest] Generando embeddings (modelo: {model}) para {total} textos...")

    for i, t in enumerate(texts, start=1):
        ok = False
        for attempt in range(1, 4):
            try:
                out = genai.embed_content(
                    model=model,
                    content=t,
                    task_type="retrieval_document",
                )
                vec = _extract_embedding(out)
                if vec is None:
                    raise RuntimeError(f"Respuesta de embedding sin vector (idx {i})")
                all_vecs.append(vec)
                if i == total or i % 10 == 0:
                    log(f"[ingest] Progreso embeddings: {i}/{total}")
                ok = True
                break
            except Exception as e:
                log(f"[ingest][ERROR] Embed idx {i} intento {attempt}: {e}")
                time.sleep(1.5 * attempt)

        if not ok:
            raise RuntimeError(
                f"No se pudo generar el embedding del fragmento {i}; "
                "se cancela para evitar desalinear el indice y los textos."
            )

    log(f"[ingest] Embeddings generados: {len(all_vecs)} / {total}")
    if not all_vecs:
        return np.empty((0, 0), dtype="float32")

    # Un fallo interrumpe la ingesta para conservar la correspondencia con el corpus.
    return np.array(all_vecs, dtype="float32")


# ===============  MAIN  ===============
def main():
    corpus = build_corpus()
    if not corpus:
        log("[ingest] No hay documentos para indexar.")
        return

    texts = [c["text"] for c in corpus]

    try:
        vecs = embed_texts(texts)
    except Exception as e:
        log(f"[ingest][ERROR] Falló la generación de embeddings: {e}")
        log(" - Verifica GENAI_API_KEY/EMBEDDING_MODEL en .env")
        return

    if vecs.size == 0:
        log("[ingest][ERROR] No se generaron embeddings. Abortando indexación.")
        return

    if len(vecs) != len(corpus):
        raise RuntimeError("El numero de vectores no coincide con el corpus.")

    dim = vecs.shape[1]
    log(f"[ingest] Dimensión de embedding: {dim}")
    faiss.normalize_L2(vecs)
    index = faiss.IndexFlatIP(dim)
    index.add(vecs)

    faiss_path = STORAGE_DIR / "computacion.faiss"
    meta_path = STORAGE_DIR / "computacion_meta.pkl"
    faiss.write_index(index, str(faiss_path))
    with open(meta_path, "wb") as f:
        pickle.dump(corpus, f)

    log(f"[ingest] Ingesta OK -> {len(corpus)} chunks guardados en {faiss_path}")


if __name__ == "__main__":
    main()
