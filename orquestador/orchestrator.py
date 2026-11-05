# orchestrator.py
# Orquestador simple para dos micro-tutores:
#  - "info"  -> Tutor RAG  (/ask)
#  - "class" -> Tutor Clases SL (/api/sl_chat)
# Incluye: circuit breaker, reintentos, rate-limit por sesión,
# formateo seguro para Second Life y fallback con avisos al usuario.

import os, time, re, threading
from typing import Dict, Any, List, Tuple
import requests
from flask import Flask, request, jsonify
from collections import defaultdict, deque

# =========================
# Config (env-overridable)
# =========================
RAG_URL = os.getenv("RAG_URL", "http://localhost:8000/ask")
RAG_HEALTH = os.getenv("RAG_HEALTH", "http://localhost:8000/health")

CLASS_URL = os.getenv("CLASS_URL", "http://localhost:5000/api/sl_chat")
CLASS_HEALTH = os.getenv("CLASS_HEALTH", "http://localhost:5000/api/health")

REQUEST_TIMEOUT_SEC = float(os.getenv("REQUEST_TIMEOUT_SEC", "12"))
RETRIES = int(os.getenv("RETRIES", "1"))

# SL local chat ~1023 bytes → usamos 800 como margen seguro
SL_MAX_LEN = int(os.getenv("SL_MAX_LEN", "800"))

RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "5"))
RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "6"))

CB_THRESHOLD = int(os.getenv("CB_THRESHOLD", "3"))
CB_COOLDOWN_SEC = int(os.getenv("CB_COOLDOWN_SEC", "30"))

ENABLE_BG_HEALTH = os.getenv("ENABLE_BG_HEALTH", "true").lower() == "true"
HEALTH_INTERVAL_SEC = int(os.getenv("HEALTH_INTERVAL_SEC", "15"))

# =========================
# REGEX MARKDOWN (FIX)
# =========================

_md_inline_code = re.compile(r"`{1,3}([^`]+?)`{1,3}")  # `for`
_md_codeblock = re.compile(r"```([\s\S]*?)```")  # ```code```
_md_bold_ital = re.compile(r"\*\*?(.*?)\*\*?")
_md_underscore = re.compile(r"__?(.*?)__?")
_md_strike = re.compile(r"~~(.*?)~~")
_md_link = re.compile(r"\[(.*?)\]\((.*?)\)")
_html_tag = re.compile(r"<[^>]+>")
_md_headings = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_md_bullets = re.compile(r"^\s{0,3}([-*+]\s+|\d+\.\s+)", re.M)


def strip_markdown(text: str) -> str:
    """
    Convierte Markdown/HTML ligero a texto plano para Second Life
    SIN eliminar contenido de código (for, if, while, etc.)
    """
    if not text:
        return text

    t = text

    # Mantener contenido de bloques de código
    t = _md_codeblock.sub(lambda m: m.group(1).strip(), t)

    # Mantener contenido de inline code
    t = _md_inline_code.sub(lambda m: m.group(1).strip(), t)

    t = _md_bold_ital.sub(r"\1", t)
    t = _md_underscore.sub(r"\1", t)
    t = _md_strike.sub(r"\1", t)
    t = _md_link.sub(r"\1", t)
    t = _html_tag.sub("", t)
    t = _md_headings.sub("", t)
    t = _md_bullets.sub("", t)

    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


# =========================
# CHUNKING PARA SL
# =========================
def chunk_for_sl(text: str, max_len: int = SL_MAX_LEN) -> List[str]:
    if not text:
        return []

    t = strip_markdown(text)
    if len(t) <= max_len:
        return [t]

    parts = re.split(r"(?<=[\.\!\?\;])\s+|\n+", t)
    chunks, buf = [], ""

    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) + 1 <= max_len:
            buf = (buf + " " + p).strip() if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_len:
                buf = p
            else:
                for i in range(0, len(p), max_len):
                    chunks.append(p[i : i + max_len])
                buf = ""

    if buf:
        chunks.append(buf)

    return chunks


def normalize_and_split_chunks(chunks: List[str], max_len: int) -> List[str]:
    out = []
    for c in chunks:
        if not c:
            continue
        t = strip_markdown(c)
        if len(t) <= max_len:
            out.append(t)
        else:
            out.extend(chunk_for_sl(t, max_len))
    return [x for x in out if x.strip()]


# =========================
# RATE LIMIT POR SESIÓN
# =========================
_access_log: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))


def rate_limited(session_id: str) -> Tuple[bool, int]:
    now = time.time()
    dq = _access_log[session_id]

    while dq and (now - dq[0]) > RATE_LIMIT_WINDOW_SEC:
        dq.popleft()

    if len(dq) >= RATE_LIMIT_MAX:
        retry_after = max(1, int(RATE_LIMIT_WINDOW_SEC - (now - dq[0])))
        return True, retry_after

    dq.append(now)
    return False, 0


# =========================
# CIRCUIT BREAKER
# =========================
class Circuit:
    def __init__(self):
        self.failures = 0
        self.open_until = 0.0

    def allow(self):
        return time.time() >= self.open_until

    def record_success(self):
        self.failures = 0

    def record_failure(self):
        self.failures += 1
        if self.failures >= CB_THRESHOLD:
            self.open_until = time.time() + CB_COOLDOWN_SEC


rag_cb = Circuit()
class_cb = Circuit()

# =========================
# HEALTH
# =========================
health_cache = {"rag": None, "class": None}


def check_health(kind: str):
    url = RAG_HEALTH if kind == "rag" else CLASS_HEALTH
    try:
        r = requests.get(url, timeout=4)
        ok = r.status_code == 200 and r.json().get("ok") is True
    except Exception:
        ok = False
    health_cache[kind] = ok
    (
        (rag_cb if kind == "rag" else class_cb).record_success()
        if ok
        else (rag_cb if kind == "rag" else class_cb).record_failure()
    )
    return ok


def bg_health_loop():
    while True:
        check_health("rag")
        check_health("class")
        time.sleep(HEALTH_INTERVAL_SEC)


# =========================
# HTTP HELPER
# =========================
def post_with_retries(url: str, payload: Dict[str, Any]):
    last_err = ""
    for i in range(1 + RETRIES):
        try:
            r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
            if r.status_code == 200:
                return True, r.json(), ""
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.4 * i)
    return False, {}, last_err


# =========================
# FLASK APP
# =========================
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


@app.post("/route")
def route():
    data = request.get_json(force=True) or {}

    mode = data.get("mode", "").lower()
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    channel = data.get("channel", "sl")
    user_name = data.get("user_name", "")

    if not message or mode not in ("info", "class"):
        return jsonify({"ok": False, "error": "Petición inválida"}), 400

    limited, retry_after = rate_limited(session_id)
    if limited:
        return (
            jsonify(
                {
                    "ok": False,
                    "retry_after": retry_after,
                    "answer": f"Demasiados mensajes seguidos. Intenta en {retry_after}s.",
                }
            ),
            429,
        )

    if mode == "info":
        target, cb = RAG_URL, rag_cb
        payload = {"question": message, "session_id": session_id}
    else:
        target, cb = CLASS_URL, class_cb
        payload = {"avatar_id": session_id, "message": message}

    if not cb.allow():
        return (
            jsonify(
                {
                    "ok": False,
                    "answer": "El tutor está temporalmente no disponible. Intenta más tarde.",
                }
            ),
            503,
        )

    ok, resp, err = post_with_retries(target, payload)
    if not ok:
        cb.record_failure()
        return jsonify({"ok": False, "answer": "Error del tutor."}), 502

    cb.record_success()

    text = resp.get("answer") or resp.get("reply") or ""
    native_chunks = resp.get("chunks")

    if channel == "sl":
        if isinstance(native_chunks, list) and native_chunks:
            chunks = normalize_and_split_chunks(native_chunks, SL_MAX_LEN - 20)
        else:
            chunks = chunk_for_sl(text, SL_MAX_LEN - 20)

        return jsonify(
            {"ok": True, "answer": chunks[0] if chunks else "", "chunks": chunks}
        )

    return jsonify({"ok": True, "answer": text})


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    if ENABLE_BG_HEALTH:
        threading.Thread(target=bg_health_loop, daemon=True).start()
    app.run(port=7000, debug=False)
