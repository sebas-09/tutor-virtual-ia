# =========================
# gemini.py (MODIFICADO)
# =========================
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from pathlib import Path
import google.generativeai as genai

load_dotenv(Path(__file__).resolve().parent / '.env')

GENAI_API_KEY = os.getenv("GENAI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
GENAI_MODEL = (os.getenv("GENAI_MODEL") or "gemini-2.5-flash").strip()

if not GENAI_API_KEY:
    raise RuntimeError("❌ Falta GENAI_API_KEY en el entorno o en el archivo .env.")

genai.configure(api_key=GENAI_API_KEY)


# -----------------------------------------------------------
# Estado del tutor (CLASE COMPLETA MODIFICADA)
# -----------------------------------------------------------
@dataclass
class ClassState:
    topic: str = ""
    level: str = "basico"  # se asigna a partir de mastery_1_5 tras intake guiado
    style: str = "mixto"
    pace: str = "medio"
    detail: str = "intermedio"
    goal: str = ""
    subtopics: List[str] = field(default_factory=list)
    phase: str = "intake"
    plan: List[Dict[str, Any]] = field(default_factory=list)
    memory_points: List[str] = field(default_factory=list)
    last_user_msg: str = ""
    persona: str = "neutral"
    waiting_for_user: bool = False

    # --- NUEVO: intake guiado + tutoría “clase real”
    mastery_1_5: int = 0  # 1..5
    intake_step: int = 0  # 0: mastery, 1: goal, 2: listo
    awaiting: str = ""  # "", "mastery", "goal", "plan_confirm", "exercise_answer"
    last_question: str = ""  # última pregunta/ejercicio emitido


def _call_genai(prompt: str) -> str:
    try:
        model = genai.GenerativeModel(GENAI_MODEL)
        response = model.generate_content(prompt)
        return (response.text or "").strip()
    except Exception as e:
        raise RuntimeError(f"Error LLM: {e}")


# -----------------------------------------------------------
# Prompting del tutor (INSTRUCCIONES MODIFICADAS)
# -----------------------------------------------------------
SYSTEM_INSTRUCTIONS = (
    "Eres un tutor virtual MUY interactivo, paciente y experto.\n"
    "Reglas generales:\n"
    "1) Responde SIEMPRE en el idioma del estudiante.\n"
    "2) Explica en bloques CORTOS (máx. 4–5 frases por bloque).\n"
    "3) Si el estudiante pide ejercicios, práctica o mini-ejercicios: genera ejercicios ACADÉMICOS "
    "propios de la materia (tipo taller/examen). NO uses contextos de vida cotidiana "
    "a menos que el estudiante lo solicite explícitamente.\n"
    "4) Evita tecnicismos innecesarios; si los usas, explícalos.\n"
    "5) No des discursos largos: enseña, luego haz una pausa con una pregunta.\n"
    "6) Termina SIEMPRE con una pregunta o mini-tarea concreta.\n"
    "\n"
    "Formato para mini-ejercicios (cuando se pidan):\n"
    "- Entrega 3 ítems cortos (Fácil/Media/Difícil).\n"
    "- No des la solución completa; espera el intento del estudiante y da retroalimentación.\n"
    "\n"
    "Interactividad según estilo:\n"
    "- Estilo 'socratico': haz preguntas guiadas; no reveles todo de golpe.\n"
    "- Estilo 'paso-a-paso': divide en pasos numerados y verifica comprensión.\n"
    "- Estilo 'mixto': explicación breve + ejercicio/pregunta.\n"
)


def _interaction_guidelines(state: Optional[ClassState]) -> str:
    if not state:
        return ""

    style = state.style or "mixto"
    level = state.level or "basico"
    detail = state.detail or "intermedio"
    phase = state.phase or "intake"

    partes: List[str] = []

    if phase == "intake":
        partes.append(
            "Durante 'intake', realiza diagnóstico breve: (1) dominio 1–5 y (2) objetivo de hoy. "
            "No des teoría todavía."
        )
    elif phase == "plan":
        partes.append(
            "Durante 'plan', propone un plan de estudio en 3–5 pasos (máximo), claro y accionable. "
            "Pide confirmación explícita (OK / sí) antes de avanzar."
        )
    elif phase == "teach":
        partes.append(
            "Durante 'teach', sigue el ciclo: (1) idea pequeña, (2) pregunta/mini-ejercicio, "
            "(3) espera el intento del estudiante, (4) retroalimentación breve, (5) siguiente paso."
        )
    else:
        partes.append(
            "Durante 'reflect', pide al estudiante resumir y señalar qué le cuesta más."
        )

    if style == "socratico":
        partes.append(
            "Usa estilo socrático: preguntas guiadas antes de dar la respuesta."
        )
    elif style == "paso-a-paso":
        partes.append("Usa estilo paso-a-paso: pasos numerados y check de comprensión.")
    else:
        partes.append("Usa estilo mixto: breve explicación + ejemplo/ejercicio.")

    if level == "basico":
        partes.append(
            "Nivel básico: inicia con intuición y ejercicios fáciles tipo taller."
        )
    elif level == "avanzado":
        partes.append(
            "Nivel avanzado: puedes incluir técnica, pero con bloques cortos y checks."
        )

    if detail == "breve":
        partes.append("Detalle breve: 2–3 ideas clave.")
    elif detail == "profundo":
        partes.append(
            "Detalle profundo: segunda vuelta técnica tras confirmar comprensión."
        )

    return "\n".join(partes)


def _system_preamble(state: Optional[ClassState]) -> str:
    ctx = SYSTEM_INSTRUCTIONS
    if state:
        ctx += (
            f"\n\n[Contexto de la clase]\n"
            f"Tema: {state.topic or 'por definir'}\n"
            f"Nivel: {state.level}\n"
            f"Estilo: {state.style}\n"
            f"Ritmo: {state.pace}\n"
            f"Detalle: {state.detail}\n"
            f"Fase: {state.phase}\n"
            f"Objetivo: {state.goal or 'por definir'}\n"
            f"Dominio 1–5: {getattr(state,'mastery_1_5',0) or 0}\n"
            f"Personalidad: {state.persona}\n"
            "\n[Guías de interacción]\n"
            f"{_interaction_guidelines(state)}\n"
        )
    return ctx


def _build_prompt(
    user_message: str, history: List[Dict[str, str]], state: ClassState
) -> str:
    hist_lines: List[str] = []
    for m in history[-10:]:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        tag = "Estudiante" if role == "user" else "Tutor"
        if content:
            hist_lines.append(f"{tag}: {content}")
    hist_block = "\n".join(hist_lines) if hist_lines else "(sin historial previo)"

    instrucciones_respuesta = (
        "Estructura tu respuesta así:\n"
        "1) Explicación breve en 1–3 bloques cortos.\n"
        "2) Ejemplo o mini-ejercicio (si aplica).\n"
        "3) UNA pregunta final clara.\n"
        "Regla crítica: si el estudiante pide ejercicios o práctica, deben ser ACADÉMICOS "
        "tipo taller/examen; evita contextos de vida cotidiana salvo petición explícita.\n"
        "No des soluciones completas si no te las piden; primero deja que el estudiante intente."
    )

    return (
        f"{_system_preamble(state)}\n\n"
        f"[Historial reciente]\n{hist_block}\n\n"
        f"[Mensaje actual del estudiante]\n{user_message}\n\n"
        f"{instrucciones_respuesta}\n\n"
        "Responde de forma clara, amable y natural."
    )


# -----------------------------------------------------------
# Recorte (FUNCIÓN COMPLETA MODIFICADA: respeta fase plan)
# -----------------------------------------------------------
def _trim_coletillas(txt: str, phase: str = "") -> str:
    if not txt:
        return ""
    lines = [ln for ln in txt.strip().splitlines() if ln.strip()]
    limit = 20 if phase == "plan" else 8  # más aire para el plan
    txt = "\n".join(lines[:limit]).strip()
    if txt.endswith("?"):
        return txt
    return txt + "\n\n¿Con qué parte quieres que trabajemos ahora?"


# -----------------------------------------------------------
# Fases (FUNCIÓN COMPLETA MODIFICADA: plan requiere confirmación)
# -----------------------------------------------------------
def next_tutor_phase(state: ClassState, user_message: str) -> str:
    msg = (user_message or "").lower()
    if state.phase == "intake":
        return "plan"

    if state.phase == "plan":
        if any(
            k in msg
            for k in [
                "ok",
                "sí",
                "si",
                "dale",
                "aprobado",
                "perfecto",
                "vamos",
                "listo",
            ]
        ):
            return "teach"
        return "plan"

    if state.phase == "teach":
        if any(
            k in msg for k in ["gracias", "entendi", "entendí", "ok", "listo", "ya"]
        ):
            return "reflect"
        return "teach"

    return "reflect"
