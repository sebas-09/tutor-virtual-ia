# app.py (SL tutor, versión unificada con intake guiado + plan confirmable + ejercicios académicos + tutoría “clase real”)
# ---------------------------------------------------------------------------------------------------------------
# Flask backend del tutor virtual (Gemini + SQLAlchemy).
# Cambios incluidos:
#   1) Intake guiado obligatorio: dominio 1–5 y objetivo de hoy (antes de proponer plan).
#   2) Plan NO avanza a teach sin confirmación explícita (OK / sí / etc.).
#   3) Ejercicios “académicos tipo taller/examen” (esto se fuerza desde gemini.py; aquí se mejora el flujo).
#   4) Modo “clase real”: si el tutor dejó un ejercicio, el siguiente mensaje se interpreta como intento y se retroalimenta.
#   5) _trim_coletillas recibe phase para no recortar el plan.
# ---------------------------------------------------------------------------------------------------------------

import os, json, re, unicodedata, textwrap, time
from pathlib import Path
from dataclasses import asdict
from flask import Flask, request, jsonify, render_template
import logging

# --- LLM & lógica tutor ---
from gemini import (
    ClassState,
    next_tutor_phase,
    _call_genai,
    _system_preamble,
    _build_prompt,
    _trim_coletillas,  # ahora acepta phase opcional (ver gemini.py)
    GENAI_MODEL,
)

# --- DB/ORM (ajusta a tus módulos reales) ---
from db import SessionLocal
from sqlalchemy import select
from models import Subtopic, Topic
from crud import (
    get_or_create_user,
    get_session_by_key,
    create_session,
    update_session,
    delete_session_by_key,
    add_message,
    get_messages,
    add_memory_point,
    get_plan_template,
    get_hints_for_subtopic,
    log_activity,
    save_state_json,
    adjust_mastery,
    get_user_progress,
)

# --- Caché LLM (opcional; ignora si no lo usas) ---
try:
    from utils_cache import cache_key, get_cached, put_cache
except Exception:

    def cache_key(*args, **kwargs):
        return ""

    def get_cached(*args, **kwargs):
        return None

    def put_cache(*args, **kwargs):
        return None


# -----------------------------------------------------------
# Logging
# -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] SL_TUTOR %(message)s"
)
logger = logging.getLogger("SL_TUTOR")

# -----------------------------------------------------------
# Flask
# -----------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

app = Flask(__name__, template_folder=str(TEMPLATES_DIR), static_folder=None)
app.config["JSON_AS_ASCII"] = False
app.config["JSONIFY_MIMETYPE"] = "application/json; charset=utf-8"

MAX_CHUNK = 700  # tamaño por mensaje en SL


# ===========================================================
# Helpers generales
# ===========================================================
def _strip_quotes(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1].strip()
    return s


def _slug_lower(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.lower()


def _safe_setattr(obj, name, value):
    try:
        setattr(obj, name, value)
    except Exception:
        pass


def asdict_safe(st: ClassState) -> dict:
    try:
        d = asdict(st)
    except Exception:
        d = st.__dict__.copy()

    # defaults de compatibilidad (no fallar si no existen campos)
    d.setdefault("waiting_for_user", getattr(st, "waiting_for_user", False))
    d.setdefault("persona", getattr(st, "persona", "neutral"))
    d.setdefault("current_subtopic", getattr(st, "current_subtopic", None))
    d.setdefault("intake_slot_waiting", getattr(st, "intake_slot_waiting", None))
    d.setdefault("intake_completed", getattr(st, "intake_completed", False))
    d.setdefault("intake_retry_count", getattr(st, "intake_retry_count", 0))
    d.setdefault("last_user_msg_hash", getattr(st, "last_user_msg_hash", None))
    d.setdefault("last_msg_sig", getattr(st, "last_msg_sig", None))
    d.setdefault("last_msg_ts", getattr(st, "last_msg_ts", 0.0))

    # NUEVO: intake guiado / modo clase real
    d.setdefault("mastery_1_5", getattr(st, "mastery_1_5", 0))
    d.setdefault("intake_step", getattr(st, "intake_step", 0))
    d.setdefault("awaiting", getattr(st, "awaiting", ""))
    d.setdefault("last_question", getattr(st, "last_question", ""))

    return d


def _state_snapshot(st: ClassState) -> dict:
    return {
        "phase": getattr(st, "phase", None),
        "topic": getattr(st, "topic", None),
        "level": getattr(st, "level", None),
        "style": getattr(st, "style", None),
        "pace": getattr(st, "pace", None),
        "detail": getattr(st, "detail", None),
        "goal": getattr(st, "goal", None),
        "persona": getattr(st, "persona", None),
        "waiting_for_user": getattr(st, "waiting_for_user", None),
        "intake_completed": getattr(st, "intake_completed", None),
        "intake_slot_waiting": getattr(st, "intake_slot_waiting", None),
        "intake_retry_count": getattr(st, "intake_retry_count", 0),
        "last_user_msg_hash": getattr(st, "last_user_msg_hash", None),
        "last_msg_sig": getattr(st, "last_msg_sig", None),
        "last_msg_ts": getattr(st, "last_msg_ts", 0.0),
        # NUEVO
        "mastery_1_5": getattr(st, "mastery_1_5", 0),
        "intake_step": getattr(st, "intake_step", 0),
        "awaiting": getattr(st, "awaiting", ""),
    }


def _hash_msg(s: str) -> str:
    return str(abs(hash((s or "").strip())))


def _sentence_split(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[\.\?\!;])\s+", text)
    merged, buf = [], ""
    for p in parts:
        if len(buf) + len(p) + 1 <= MAX_CHUNK:
            buf = (buf + " " + p).strip() if buf else p
        else:
            if buf:
                merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)
    return merged


def _wrap_chunks(text: str) -> list[str]:
    sentences = _sentence_split(text)
    chunks: list[str] = []
    for sent in sentences:
        if len(sent) <= MAX_CHUNK:
            chunks.append(sent)
        else:
            chunks.extend(textwrap.wrap(sent, width=MAX_CHUNK))
    return chunks


def _chunk_for_sl(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    return _wrap_chunks(text)


# ===========================================================
# NUEVO: Intake guiado y confirmación plan
# ===========================================================
def _parse_mastery_1_5(msg: str) -> int | None:
    m = re.search(r"\b([1-5])\b", (msg or "").strip())
    return int(m.group(1)) if m else None


def _level_from_mastery(m: int) -> str:
    if m <= 2:
        return "basico"
    if m == 3:
        return "intermedio"
    return "avanzado"


def _is_plan_confirm(msg: str) -> bool:
    low = (msg or "").strip().lower()
    return any(
        k in low
        for k in ["ok", "sí", "si", "dale", "aprobado", "perfecto", "vamos", "listo"]
    )


# ===========================================================
# Normalizadores
# ===========================================================
STYLE_MAP_IN = {
    "teorico": "socratico",
    "teórico": "socratico",
    "practico": "paso-a-paso",
    "práctico": "paso-a-paso",
    "mixto": "mixto",
    "socratico": "socratico",
    "paso-a-paso": "paso-a-paso",
}
PERSONA_MAP_IN = {
    "neutral": "neutral",
    "paciente": "amable",
    "motivador": "amable",
    "directo": "estricto",
    "amable": "amable",
    "divertido": "divertido",
    "estricto": "estricto",
}
DETAIL_MAP_IN = {
    "basico": "breve",
    "básico": "breve",
    "breve": "breve",
    "intermedio": "intermedio",
    "profundo": "profundo",
}


def _norm_style(x: str) -> str:
    return STYLE_MAP_IN.get(_slug_lower(x), "mixto")


def _norm_persona(x: str) -> str:
    return PERSONA_MAP_IN.get(_slug_lower(x), "neutral")


def _norm_detail(x: str) -> str:
    return DETAIL_MAP_IN.get(_slug_lower(x), "intermedio")


# ===========================================================
# Intake legacy (lo dejamos para cambios inline, pero el intake guiado manda)
# ===========================================================
INTAKE_SCHEMA = {
    "topic": {
        "prompt": "Tema de estudio?",
        "normalize": lambda x: (x or "").strip()[:120],
        "validate": lambda x: bool(x),
    },
    "level": {
        "prompt": "Nivel (basico / intermedio / avanzado)?",
        "normalize": lambda x: _slug_lower(x).replace("básico", "basico"),
        "validate": lambda x: x in {"basico", "intermedio", "avanzado"},
    },
    "style": {
        "prompt": "Estilo (socratico / paso-a-paso / mixto)?",
        "normalize": _norm_style,
        "validate": lambda x: x in {"socratico", "paso-a-paso", "mixto"},
    },
    "pace": {
        "prompt": "Ritmo (lento / medio / rapido)?",
        "normalize": lambda x: _slug_lower(x).replace("rápido", "rapido"),
        "validate": lambda x: x in {"lento", "medio", "rapido"},
    },
    "detail": {
        "prompt": "Detalle (breve / intermedio / profundo)?",
        "normalize": _norm_detail,
        "validate": lambda x: x in {"breve", "intermedio", "profundo"},
    },
    "goal": {
        "prompt": "Objetivo concreto?",
        "normalize": lambda x: (x or "").strip()[:200],
        "validate": lambda x: bool((x or "").strip() and len((x or "").strip()) > 3),
    },
    "persona": {
        "prompt": "Tono (neutral / amable / divertido / estricto)?",
        "normalize": _norm_persona,
        "validate": lambda x: x in {"neutral", "amable", "divertido", "estricto"},
    },
}


def _try_fill_slot(slot: str, msg: str):
    spec = INTAKE_SCHEMA[slot]
    v = spec["normalize"](msg or "")
    return v if spec["validate"](v) else None


def _intake_summary(st) -> str:
    return (
        f"Tema: {st.topic} | Nivel: {st.level} | Estilo: {st.style} | Ritmo: {st.pace} | "
        f"Detalle: {st.detail} | Objetivo: {st.goal} | Tono: {getattr(st,'persona','neutral')} | "
        f"Dominio 1–5: {getattr(st,'mastery_1_5',0) or 0}"
    )


CHANGE_PATTERNS = [
    (r"(cambi(a|ar).*(ritmo|pace).*(lento|medio|rapido|rápido))", "pace"),
    (r"(cambi(a|ar).*(nivel|level).*(basico|intermedio|avanzado))", "level"),
    (
        r"(cambi(a|ar).*(estilo|style).*(socratico|paso-a-paso|mixto|teorico|teórico|practico|práctico))",
        "style",
    ),
    (
        r"(cambi(a|ar).*(detalle|detail).*(breve|intermedio|profundo|basico|básico))",
        "detail",
    ),
    (
        r"(tono|persona).*(neutral|amable|divertido|estricto|paciente|motivador|directo)",
        "persona",
    ),
]


def try_inline_change(st, msg):
    low = _slug_lower(msg)
    for pat, slot in CHANGE_PATTERNS:
        if re.search(pat, low):
            val = _try_fill_slot(slot, msg)
            if val:
                if slot == "pace":
                    st.pace = val
                elif slot == "level":
                    st.level = val
                elif slot == "style":
                    st.style = val
                elif slot == "detail":
                    st.detail = val
                elif slot == "persona":
                    st.persona = val
    return st


# ===========================================================
# Subtemas alias (opcional, se mantiene)
# ===========================================================
ALIAS = {
    "derivadas": "Derivadas básicas",
    "regla de la cadena": "Derivadas básicas",
    "pseudocodigo": "Algoritmos y Pseudocódigo",
    "pseudocódigo": "Algoritmos y Pseudocódigo",
    "listas enlazadas": "Estructuras de datos: listas",
    "joins sql": "SQL: JOINs",
    "normalizacion bd": "Normalización de bases de datos",
    "normalización bd": "Normalización de bases de datos",
}


def resolve_subtopic_name(txt: str | None) -> str | None:
    if not txt:
        return None
    k = _slug_lower(txt)
    for a, canonical in ALIAS.items():
        if a in k:
            return canonical
    return None


# ===========================================================
# Sanitización del historial
# ===========================================================
BAD_WORDS = ["verga", "pendejo", "mierda", "chucha", "imbécil", "idiota", "hp", "hpta"]
BAD_PAT = re.compile(
    r"\b(" + "|".join(map(re.escape, BAD_WORDS)) + r")\b", re.IGNORECASE
)


def sanitize_text(text: str) -> str:
    if not text:
        return text
    text = BAD_PAT.sub("[*]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_history(history: list[dict]) -> list[dict]:
    out = []
    for m in history[-12:]:
        out.append(
            {
                "role": m.get("role", "user"),
                "content": sanitize_text(m.get("content", "")),
            }
        )
    return out


# ===========================================================
# Carga/guarda estado
# ===========================================================
def as_session_defaults(st: ClassState):
    _safe_setattr(st, "level", getattr(st, "level", "basico"))
    _safe_setattr(st, "style", getattr(st, "style", "mixto"))
    _safe_setattr(st, "pace", getattr(st, "pace", "medio"))
    _safe_setattr(st, "detail", getattr(st, "detail", "intermedio"))
    _safe_setattr(st, "persona", getattr(st, "persona", "neutral"))
    _safe_setattr(st, "phase", getattr(st, "phase", "intake"))
    _safe_setattr(st, "waiting_for_user", getattr(st, "waiting_for_user", False))
    _safe_setattr(st, "intake_retry_count", getattr(st, "intake_retry_count", 0))

    # NUEVO
    _safe_setattr(st, "mastery_1_5", getattr(st, "mastery_1_5", 0))
    _safe_setattr(st, "intake_step", getattr(st, "intake_step", 0))
    _safe_setattr(st, "awaiting", getattr(st, "awaiting", ""))
    _safe_setattr(st, "last_question", getattr(st, "last_question", ""))


def load_state_from_db(db, session_key: str) -> ClassState:
    s = get_session_by_key(db, session_key)
    if not s:
        st = ClassState()
        as_session_defaults(st)
        logger.info("load_state_from_db: nueva sesión -> %s", _state_snapshot(st))
        return st

    try:
        if getattr(s, "state_json", None):
            st_dict = json.loads(s.state_json)
            st = ClassState()
            for k, v in (st_dict or {}).items():
                _safe_setattr(st, k, v)
            as_session_defaults(st)
            logger.info("load_state_from_db: cargado -> %s", _state_snapshot(st))
            return st
    except Exception as e:
        logger.warning("load_state_from_db: error state_json (%s). Reconstruyendo.", e)

    st = ClassState(
        topic=s.topic or "",
        level=s.level or "basico",
        style=s.style or "mixto",
        pace=s.pace or "medio",
        detail=s.detail or "intermedio",
        goal=s.goal or "",
        phase=s.phase or "intake",
        persona=getattr(s, "persona", "neutral"),
        waiting_for_user=getattr(s, "waiting_for_user", False),
    )

    as_session_defaults(st)
    try:
        save_state_json(db, s, asdict_safe(st))
        logger.info("load_state_from_db: reconstruido -> %s", _state_snapshot(st))
    except Exception:
        pass
    return st


def persist_state_to_db(db, session_key: str, st: ClassState, user_id=None):
    s = get_session_by_key(db, session_key)
    values = dict(
        phase=st.phase,
        topic=(st.topic or "")[:255],
        level=st.level,
        style=st.style,
        pace=st.pace,
        detail=st.detail,
        goal=(st.goal or "")[:255],
        persona=getattr(st, "persona", "neutral"),
        waiting_for_user=getattr(st, "waiting_for_user", False),
    )
    if s:
        if user_id is not None and getattr(s, "user_id", None) != user_id:
            values["user_id"] = user_id
        update_session(db, s, **values)
        save_state_json(db, s, asdict_safe(st))
        logger.info("persist_state_to_db: actualizado %s", _state_snapshot(st))
        return s
    else:
        s = create_session(db, session_key=session_key, user_id=user_id, **values)
        save_state_json(db, s, asdict_safe(st))
        logger.info("persist_state_to_db: creado %s", _state_snapshot(st))
        return s


# ===========================================================
# Rutas
# ===========================================================
@app.get("/")
def home():
    try:
        return render_template("index.html")
    except Exception:
        return "Tutor virtual (SL) en ejecución.", 200


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "status": "up"})


@app.get("/api/llm_test")
def llm_test():
    try:
        txt = _call_genai("Responde solo con: ok")
    except Exception as e:
        return jsonify({"ok": False, "model": GENAI_MODEL, "error": f"LLM: {e}"}), 503
    return jsonify({"ok": True, "model": GENAI_MODEL, "reply": (txt or "").strip()})


# -----------------------------------------------------------
# Chat para Second Life (session_key = avatar_id)
# -----------------------------------------------------------
@app.post("/api/sl_chat")
def sl_chat():
    data = request.get_json(force=True) or {}
    avatar_id = (data.get("avatar_id") or "").strip()
    msg_raw = (data.get("message") or "").strip()
    topic_hint = (data.get("topic") or "").strip()

    if not avatar_id or not msg_raw:
        return jsonify({"ok": False, "error": "avatar_id y message requeridos"}), 400

    session_key = avatar_id
    logger.info("sl_chat: IN avatar_id=%s msg=%r", session_key, msg_raw)

    # Fix leve de mojibake
    def _fix_encoding(s: str) -> str:
        if not s:
            return s
        try:
            if "�" in s:
                return (
                    s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
                    or s
                )
        except Exception:
            pass
        return s

    msg = _fix_encoding(msg_raw)

    with SessionLocal() as db:
        st = load_state_from_db(db, session_key)
        logger.info("sl_chat: estado inicial -> %s", _state_snapshot(st))

        # De-duplicación por ventana
        now = time.time()
        msg_hash = _hash_msg(msg)
        last_hash = getattr(st, "last_user_msg_hash", None)
        last_ts = float(getattr(st, "last_msg_ts", 0.0) or 0.0)
        is_dup = (msg_hash == last_hash) and (now - last_ts < 15.0)
        st.last_user_msg_hash = msg_hash
        st.last_msg_ts = now

        if is_dup and getattr(st, "waiting_for_user", True):
            logger.info("sl_chat: duplicado detectado; ignorando. hash=%s", msg_hash)
            persist_state_to_db(db, session_key, st)
            return jsonify(
                {
                    "ok": True,
                    "chunks": [],
                    "phase": getattr(st, "phase", "intake"),
                    "state": asdict_safe(st),
                }
            )

        st.last_user_msg = msg
        st.waiting_for_user = False

        s = get_session_by_key(db, session_key)
        if not s:
            s = persist_state_to_db(db, session_key, st)

        # Cambios inline (ritmo/estilo/detalle/persona)
        st = try_inline_change(st, msg)

        # Prellenar topic por alias/hint (si hay)
        if not getattr(st, "intake_completed", False) and not getattr(
            st, "topic", None
        ):
            alias = resolve_subtopic_name(topic_hint) or resolve_subtopic_name(msg)
            if alias:
                st.topic = alias
                logger.info("sl_chat: topic prellenado por alias -> %r", alias)
            elif topic_hint:
                v_hint = _try_fill_slot("topic", topic_hint)
                if v_hint:
                    st.topic = v_hint
                    logger.info("sl_chat: topic prellenado por hint -> %r", v_hint)
            if not getattr(st, "topic", None):
                v_top = _try_fill_slot("topic", msg)
                if v_top:
                    st.topic = v_top
                    logger.info("sl_chat: topic detectado desde mensaje -> %r", v_top)

        # ===========================================================
        # INTAKE GUIADO OBLIGATORIO (dominio 1–5 y objetivo de hoy)
        # ===========================================================
        if not getattr(st, "intake_completed", False):
            st.phase = "intake"

            # Paso 0: dominio 1–5
            if getattr(st, "intake_step", 0) == 0:
                val = _parse_mastery_1_5(msg)
                if val is None:
                    q = "Antes de empezar: del 1 al 5, ¿qué tanto dominas este tema? (1=nada, 5=domino muy bien)"
                    st.awaiting = "mastery"
                    st.last_question = q
                    st.waiting_for_user = True
                    persist_state_to_db(db, session_key, st, user_id=s.user_id)
                    add_message(db, s, role="user", content=msg)
                    add_message(db, s, role="assistant", content=q)
                    return jsonify(
                        {
                            "ok": True,
                            "chunks": _chunk_for_sl(q),
                            "phase": st.phase,
                            "state": asdict_safe(st),
                        }
                    )
                else:
                    st.mastery_1_5 = val
                    st.level = _level_from_mastery(val)
                    st.intake_step = 1

            # Paso 1: objetivo de hoy
            if getattr(st, "intake_step", 0) == 1:
                raw = (msg or "").strip()

                # Si el mensaje parece solo el número (o muy corto), pedir objetivo
                if (_parse_mastery_1_5(raw) is not None and len(raw) <= 2) or len(
                    raw
                ) < 4:
                    q = (
                        "Perfecto. ¿Cuál es tu objetivo de HOY? "
                        "(ej.: entender conceptos clave, resolver ejercicios tipo examen, "
                        "repasar para una prueba, hacer una tarea específica)"
                    )
                    st.awaiting = "goal"
                    st.last_question = q
                    st.waiting_for_user = True
                    persist_state_to_db(db, session_key, st, user_id=s.user_id)
                    add_message(db, s, role="user", content=msg)
                    add_message(db, s, role="assistant", content=q)
                    return jsonify(
                        {
                            "ok": True,
                            "chunks": _chunk_for_sl(q),
                            "phase": st.phase,
                            "state": asdict_safe(st),
                        }
                    )

                # Aceptar objetivo textual (tolerante)
                st.goal = raw[:200]
                st.intake_step = 2
                st.intake_completed = True
                st.phase = "plan"
                st.awaiting = "plan_confirm"
                st.waiting_for_user = True

                summary = _intake_summary(st)
                confirm = f"Listo. {summary}. Ahora te propongo un plan de estudio. ¿Te parece?"
                persist_state_to_db(db, session_key, st, user_id=s.user_id)
                add_message(db, s, role="user", content=msg)
                add_message(db, s, role="assistant", content=confirm)
                return jsonify(
                    {
                        "ok": True,
                        "chunks": _chunk_for_sl(confirm),
                        "phase": st.phase,
                        "state": asdict_safe(st),
                    }
                )

        # ===========================================================
        # Post-intake
        # ===========================================================
        rows = get_messages(db, s, limit=40)
        history = [{"role": m.role, "content": (m.content or "").strip()} for m in rows]
        history.append({"role": "user", "content": msg})
        history = sanitize_history(history)

        # (A) Si estamos en plan y el usuario confirma, avanzamos a teach
        if st.phase == "plan" and _is_plan_confirm(msg):
            st.phase = "teach"
            st.awaiting = ""

        # (B) Plan templates (atajo, si tienes BD con steps)
        tpl = None
        try:
            if st.phase == "plan":
                tpl = get_plan_template(
                    db,
                    getattr(st, "current_subtopic", st.topic),
                    st.level,
                    st.style,
                    st.pace,
                )
        except Exception:
            tpl = None

        if st.phase == "plan" and tpl and getattr(tpl, "steps", None):
            steps = tpl.steps if isinstance(tpl.steps, list) else []
            if steps:
                reply = (
                    "Plan propuesto:\n- "
                    + "\n- ".join(steps[:5])
                    + "\n\nSi te parece bien, responde: OK. Si quieres ajustar algo, dime qué cambio."
                )
                add_message(db, s, role="user", content=msg)
                add_message(db, s, role="assistant", content=reply)
                st.waiting_for_user = True
                st.awaiting = "plan_confirm"
                persist_state_to_db(db, session_key, st, user_id=s.user_id)
                log_activity(db, s.id, "plan", "plantilla_usada")
                return jsonify(
                    {
                        "ok": True,
                        "chunks": _chunk_for_sl(reply),
                        "phase": st.phase,
                        "state": asdict_safe(st),
                    }
                )

        # (C) Construcción del prompt: modo “clase real” si hay ejercicio pendiente
        if getattr(st, "awaiting", "") == "exercise_answer":
            user_message_for_llm = (
                "El estudiante está respondiendo el ejercicio anterior. "
                "Primero evalúa su intento (correcto/incorrecto/parcial). "
                "Luego da una corrección breve o 1 pista concreta. "
                "Después propone un siguiente ejercicio académico similar (tipo taller/examen). "
                "Finaliza con UNA pregunta concreta.\n\n"
                f"Intento del estudiante: {msg}"
            )
        else:
            user_message_for_llm = msg

        prompt = _build_prompt(user_message_for_llm, history, st)
        ckey = cache_key(GENAI_MODEL, prompt)

        reply = None
        cache_hit = None
        try:
            cached = get_cached(db, ckey)
            if cached:
                reply = (cached or "").strip()
                cache_hit = True
        except Exception:
            cache_hit = None

        if not reply:
            try:
                raw = _call_genai(prompt)

                # IMPORTANTE: no recortar planes demasiado (phase-aware)
                reply = _trim_coletillas((raw or "").strip(), phase=st.phase)

                if not reply:
                    reply = "Tuve un problema generando la respuesta. ¿Puedes reformular en una frase?"

                # Solo aquí, con éxito, movemos de fase:
                st.phase = next_tutor_phase(st, msg)

            except Exception as e:
                emsg = str(e)
                logger.exception("sl_chat: error LLM %s", e)
                # NO avanzar de fase en error LLM
                if (
                    ("429" in emsg)
                    or ("rate" in emsg.lower())
                    or ("quota" in emsg.lower())
                    or ("limit: 0" in emsg.lower())
                ):
                    reply = "Estoy limitado por cuota. Intenta de nuevo con un mensaje corto."
                elif "finish_reason=2" in emsg or "bloqueada" in emsg.lower():
                    reply = "El motor bloqueó esa redacción. Probemos con otra formulación o un ejemplo concreto."
                else:
                    reply = "No pude generar una respuesta útil ahora. Reformulemos en una frase o cambia de enfoque."

            try:
                put_cache(db, ckey, GENAI_MODEL, prompt, reply, ttl_s=86400)
                if cache_hit is None:
                    cache_hit = False
            except Exception:
                pass

        # (D) Activar “espera de intento” si el tutor dejó ejercicio
        if reply and re.search(
            r"\b(ejercicio|mini[- ]?ejercicio|resuelve|calcula|demuestra|define|completa)\b",
            reply.lower(),
        ):
            st.awaiting = "exercise_answer"
            st.last_question = reply[-400:]
        else:
            # Mantener awaiting=plan_confirm si sigue en plan
            if st.phase == "plan":
                st.awaiting = "plan_confirm"
            else:
                st.awaiting = ""
                st.last_question = ""

        add_message(db, s, role="user", content=msg)
        add_message(db, s, role="assistant", content=reply)

        if st.phase == "teach":
            add_memory_point(db, s, f"Interacción sobre {st.topic or 'tema'}")

        # Progreso (simple)
        signal_ok = re.search(
            r"\b(entend[ií]|claro|perfecto|listo|ok)\b", _slug_lower(msg)
        )
        if s.user_id and (getattr(st, "current_subtopic", None) or st.topic):
            if signal_ok:
                adjust_mastery(
                    db,
                    user_id=s.user_id,
                    subtopic_name=getattr(st, "current_subtopic", st.topic),
                    delta=1,
                    note=f"Progreso positivo. Último mensaje: {msg[:200]}",
                    clamp_min=0,
                    clamp_max=5,
                )

        st.waiting_for_user = True
        persist_state_to_db(db, session_key, st, user_id=s.user_id)
        log_activity(
            db, s.id, "phase", f"Fase actual: {st.phase}; cache_hit={cache_hit}"
        )
        logger.info(
            "sl_chat: respuesta entregada. phase=%s; cache_hit=%s; snapshot=%s",
            st.phase,
            cache_hit,
            _state_snapshot(st),
        )

        return jsonify(
            {
                "ok": True,
                "chunks": _chunk_for_sl(reply),
                "phase": st.phase,
                "state": asdict_safe(st),
            }
        )


# -----------------------------------------------------------
# Progreso y reset
# -----------------------------------------------------------
@app.get("/api/progress")
def progress():
    email = (request.args.get("email") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "Falta parámetro 'email'"}), 400
    with SessionLocal() as db:
        u = get_or_create_user(db, email=email)
        data = get_user_progress(db, u.id)
        return jsonify({"ok": True, "email": email, "progress": data})


@app.post("/api/reset")
def reset():
    data = request.get_json(force=True) or {}
    session_key = (
        data.get("session_id") or data.get("avatar_id") or "default"
    ).strip() or "default"
    with SessionLocal() as db:
        s = get_session_by_key(db, session_key)
        if s:
            log_activity(db, s.id, "reset", "Sesión reiniciada")
        delete_session_by_key(db, session_key)
        logger.info("reset: sesión %s reiniciada", session_key)
        return jsonify({"ok": True, "message": f"Sesión {session_key} reiniciada."})


# -----------------------------------------------------------
if __name__ == "__main__":
    # Para prod, usa WSGI (gunicorn/uwsgi)
    # export PY

    app.run(port=5000, debug=False)
