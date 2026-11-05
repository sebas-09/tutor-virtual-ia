"""Regresiones sin red: alineación entre corpus, embeddings e índice."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import faiss
import numpy as np

INFO = Path(__file__).resolve().parents[1] / "tutor_info" / "app"
sys.path.insert(0, str(INFO))
# Configuración ficticia; no se leen credenciales ni se consumen APIs.
with patch.dict(os.environ, {"GENAI_API_KEY": "offline-test-placeholder"}):
    ingest = importlib.import_module("ingest")
    retrieve = importlib.import_module("retrieve")


class RagIntegrityTests(unittest.TestCase):
    def test_un_embedding_fallido_cancela_la_ingesta(self):
        with patch.object(ingest.genai, "configure"), patch.object(
            ingest.genai, "embed_content",
            side_effect=[{"embedding": [1.0, 0.0]}, RuntimeError("fallo")] + [RuntimeError("fallo")] * 2,
        ), patch("time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "fragmento 2"):
                ingest.embed_texts(["primer documento", "segundo documento"])

    def test_embeddigs_conservan_el_orden_del_corpus(self):
        with patch.object(ingest.genai, "configure"), patch.object(
            ingest.genai, "embed_content",
            side_effect=[{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}],
        ):
            vectors = ingest.embed_texts(["primero", "segundo"])
        np.testing.assert_array_equal(vectors, [[1.0, 0.0], [0.0, 1.0]])

    def test_no_escribe_un_indice_si_faltan_vectores(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            ingest, "STORAGE_DIR", Path(tmp)
        ), patch.object(ingest, "build_corpus", return_value=[
            {"text": "primero"}, {"text": "segundo"}
        ]), patch.object(ingest, "embed_texts", return_value=np.array([[1.0, 0.0]], dtype="float32")):
            with self.assertRaisesRegex(RuntimeError, "no coincide"):
                ingest.main()
            self.assertFalse((Path(tmp) / "computacion.faiss").exists())

    def test_rechaza_indices_historicos_desalineados(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            retrieve, "STORAGE_DIR", Path(tmp)
        ), patch.object(retrieve.pickle, "load", return_value=[]):
            folder = Path(tmp)
            index = faiss.IndexFlatIP(2)
            index.add(np.array([[1.0, 0.0]], dtype="float32"))
            faiss.write_index(index, str(folder / "computacion.faiss"))
            (folder / "computacion_meta.pkl").touch()
            with self.assertRaisesRegex(ValueError, "desalineados"):
                retrieve._load_index()


if __name__ == "__main__":
    unittest.main()
