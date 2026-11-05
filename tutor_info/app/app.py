# app.py — Tutor RAG (sin recortes para SL)
# ------------------------------------------------------------
# Cambios clave:
# - Se eliminó cualquier shaping/recorte por canal (antes cortaba a 320).
# - /ask devuelve SIEMPRE el texto completo de `answer(q)`.
# - El orquestador es el único responsable de limpiar/trozar para Second Life.
# ------------------------------------------------------------

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import logging

# Tu función actual de RAG (no modificar aquí)
# Debe devolver un string con la respuesta COMPLETA (puede traer markdown)
from retrieve import answer

app = Flask(__name__, template_folder="templates")
app.config["JSON_AS_ASCII"] = False  # permite UTF-8 (acentos, emojis) en JSON
CORS(app, resources={r"/*": {"origins": "*"}})

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tutor-rag")


# ---------- Rutas ----------
@app.get("/")
def home():
    # Renderiza templates/index.html si existe; si no, muestra texto simple
    try:
        return render_template("index.html")
    except Exception:
        return "Tutor RAG en ejecución.", 200


@app.get("/health")
def health():
    return jsonify({"ok": True, "status": "up"}), 200


@app.post("/ask")
def ask():
    """
    Espera JSON:
    {
      "question": "texto",
      "session_id": "opcional",
      "channel": "opcional (e.g., 'sl', 'web')",
      "user_name": "opcional"
    }

    Responde SIEMPRE el texto COMPLETO:
      { "ok": true, "answer": "<texto completo>" }

    NOTA:
    - No se limpia ni recorta por canal aquí.
    - El orquestador es quien limpia markdown/HTML y trocea para SL.
    """
    try:
        data = request.get_json(force=True) or {}

        q = (data.get("question") or "").strip()
        session_id = (data.get("session_id") or "").strip()
        channel = (data.get("channel") or "").strip().lower()
        user_name = (data.get("user_name") or "").strip()

        if not q:
            return jsonify({"ok": False, "error": "Falta 'question'"}), 400

        log.info(
            "ASK RAG | session_id=%s channel=%s user=%s q_len=%d",
            session_id or "-",
            channel or "-",
            user_name or "-",
            len(q),
        )

        # ⬇️ Obtén la respuesta COMPLETA (sin recortes ni shaping aquí)
        raw = answer(q)
        full_text = str(raw or "")

        # Devuelve el texto íntegro. El orquestador se encarga de trocear/limpiar para SL.
        return jsonify({"ok": True, "answer": full_text}), 200

    except Exception as e:
        log.exception("Error en /ask")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------- Main ----------
if __name__ == "__main__":
    # debug=False en producción; el puerto por defecto aquí es 8000
    app.run(host='127.0.0.1', port=8000, debug=False)
