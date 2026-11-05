# crud.py (versión optimizada 2025-10-22)
# ---------------------------------------------------
# Funciones CRUD para el tutor virtual adaptativo
# Incluye soporte para 'persona', 'waiting_for_user'
# y rendimiento mejorado con SQLAlchemy 2.x

from datetime import datetime
from typing import Optional, List, Tuple, Dict
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from models import (
    User,
    Session as SessionDB,
    Message,
    SessionMemoryPoint,
    PlanTemplate,
    Exercise,
    ExerciseHint,
    Subtopic,
    Topic,
    UserKnowledge,
)
import json


# ============================================================
# USUARIOS
# ============================================================


def get_or_create_user(db: Session, email: str, name: Optional[str] = None) -> User:
    """Obtiene o crea un usuario nuevo por email."""
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if u:
        return u
    u = User(email=email, name=name or email.split("@")[0])
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ============================================================
# SESIONES
# ============================================================


def get_session_by_key(db: Session, session_key: str) -> Optional[SessionDB]:
    """Obtiene una sesión por su clave única."""
    return db.execute(
        select(SessionDB).where(SessionDB.session_key == session_key)
    ).scalar_one_or_none()


def create_session(
    db: Session, session_key: str, user_id: Optional[int], **kwargs
) -> SessionDB:
    """Crea una nueva sesión de tutoría."""
    s = SessionDB(session_key=session_key, user_id=user_id, **kwargs)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def update_session(db: Session, session: SessionDB, **kwargs) -> SessionDB:
    """Actualiza campos de una sesión existente."""
    for k, v in kwargs.items():
        setattr(session, k, v)
    session.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session


def delete_session_by_key(db: Session, session_key: str) -> bool:
    """Elimina una sesión por su clave."""
    s = get_session_by_key(db, session_key)
    if not s:
        return False
    db.delete(s)
    db.commit()
    return True


# ============================================================
# MENSAJES
# ============================================================


def add_message(db: Session, session: SessionDB, role: str, content: str) -> Message:
    """Guarda un mensaje en la sesión."""
    m = Message(session_id=session.id, role=role, content=content)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def get_messages(db: Session, session: SessionDB, limit: int = 40) -> List[Message]:
    """Obtiene los últimos mensajes de una sesión (orden cronológico)."""
    stmt = (
        select(Message)
        .where(Message.session_id == session.id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    return list(reversed(rows))


# ============================================================
# MEMORIA PEDAGÓGICA
# ============================================================


def add_memory_point(db: Session, session: SessionDB, point: str) -> SessionMemoryPoint:
    """Registra un punto de memoria o hito pedagógico."""
    mp = SessionMemoryPoint(session_id=session.id, point_text=point)
    db.add(mp)
    db.commit()
    db.refresh(mp)
    return mp


# ============================================================
# CONTENIDO Y PLANTILLAS
# ============================================================


def get_plan_template_by_id(
    db: Session, subtopic_id: int, level: str, style: str, pace: str
) -> Optional[PlanTemplate]:
    """Busca una plantilla de plan de aprendizaje por ID exacto."""
    q = (
        select(PlanTemplate)
        .where(PlanTemplate.subtopic_id == subtopic_id)
        .where(PlanTemplate.level == level)
        .where(PlanTemplate.style == style)
        .where(PlanTemplate.pace == pace)
        .limit(1)
    )
    tpl = db.execute(q).scalar_one_or_none()
    if tpl:
        return tpl

    # Fallbacks: relaja los filtros progresivamente
    q2 = (
        select(PlanTemplate)
        .where(PlanTemplate.subtopic_id == subtopic_id)
        .where(PlanTemplate.level == level)
        .limit(1)
    )
    return db.execute(q2).scalar_one_or_none()


def get_hints_for_subtopic_id(db: Session, subtopic_id: int) -> List[ExerciseHint]:
    """Obtiene todas las pistas de ejercicios asociadas a un subtema."""
    q = (
        select(ExerciseHint)
        .join(Exercise, ExerciseHint.exercise_id == Exercise.id)
        .where(Exercise.subtopic_id == subtopic_id)
    )
    return db.execute(q).scalars().all()


# ------------------------------------------------------------
# Wrappers por nombre o ID
# ------------------------------------------------------------


def _get_subtopic_by_name(db: Session, name: str) -> Optional[Subtopic]:
    """Busca un subtema por nombre exacto."""
    return db.execute(
        select(Subtopic).where(Subtopic.name == name)
    ).scalar_one_or_none()


def get_plan_template(db: Session, subtopic_ref, level: str, style: str, pace: str):
    """Busca una plantilla de plan de estudio por nombre o ID."""
    sub_id = subtopic_ref if isinstance(subtopic_ref, int) else None
    if not sub_id:
        sub = _get_subtopic_by_name(db, str(subtopic_ref))
        if sub:
            sub_id = sub.id
    if not sub_id:
        return None
    return get_plan_template_by_id(db, sub_id, level, style, pace)


def get_hints_for_subtopic(db: Session, subtopic_ref):
    """Obtiene pistas asociadas a un subtema (por nombre o ID)."""
    sub_id = subtopic_ref if isinstance(subtopic_ref, int) else None
    if not sub_id:
        sub = _get_subtopic_by_name(db, str(subtopic_ref))
        if sub:
            sub_id = sub.id
    if not sub_id:
        return []
    return get_hints_for_subtopic_id(db, sub_id)


# ============================================================
# LOG DE ACTIVIDAD DE SESIÓN
# ============================================================


def log_activity(
    db: Session, session_id: int, action: str, detail: Optional[str] = None
):
    """Registra acciones o eventos relevantes dentro de una sesión."""
    stmt = text(
        """
        INSERT INTO session_activity_log (session_id, action, detail, created_at)
        VALUES (:sid, :a, :d, CURRENT_TIMESTAMP)
    """
    )
    db.execute(stmt, {"sid": session_id, "a": (action or "")[:100], "d": detail or ""})
    db.commit()


# ============================================================
# PERSISTENCIA DE SNAPSHOT (STATE_JSON)
# ============================================================


def save_state_json(db: Session, session: SessionDB, state_dict: dict):
    """Guarda un snapshot JSON del estado completo del tutor."""
    try:
        state_json = json.dumps(state_dict, ensure_ascii=False)
        session.state_json = state_json
        session.updated_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        print(f"[WARN] No se pudo guardar state_json: {e}")


# ============================================================
# USER KNOWLEDGE (DOMINIO ADAPTATIVO)
# ============================================================


def get_or_create_user_knowledge(
    db: Session, user_id: int, subtopic_id: int
) -> UserKnowledge:
    """Obtiene o crea registro de conocimiento del usuario."""
    uk = db.execute(
        select(UserKnowledge).where(
            UserKnowledge.user_id == user_id,
            UserKnowledge.subtopic_id == subtopic_id,
        )
    ).scalar_one_or_none()
    if uk:
        return uk
    uk = UserKnowledge(user_id=user_id, subtopic_id=subtopic_id, mastery=0, notes=None)
    db.add(uk)
    db.commit()
    db.refresh(uk)
    return uk


def adjust_mastery(
    db: Session,
    user_id: int,
    subtopic_name: str,
    delta: int,
    note: Optional[str] = None,
    clamp_min: int = 0,
    clamp_max: int = 5,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Ajusta el nivel de dominio de un usuario sobre un subtema.
    Retorna (anterior, nuevo).
    """
    sub = _get_subtopic_by_name(db, subtopic_name)
    if not sub:
        return (None, None)

    uk = get_or_create_user_knowledge(db, user_id, sub.id)
    prev = uk.mastery or 0
    new_val = max(min(prev + (delta or 0), clamp_max), clamp_min)
    uk.mastery = new_val

    if note:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        note_line = f"[{ts} UTC] {note}"
        uk.notes = (uk.notes or "") + ("\n" if uk.notes else "") + note_line
        uk.notes = uk.notes[:65535]  # evita overflow

    db.commit()
    return (prev, new_val)


def get_user_progress(db: Session, user_id: int) -> List[Dict]:
    """Devuelve el progreso del usuario en todos los subtemas."""
    stmt = (
        select(
            UserKnowledge.mastery,
            UserKnowledge.notes,
            Subtopic.id,
            Subtopic.name,
            Topic.name,
        )
        .select_from(UserKnowledge)
        .join(Subtopic, UserKnowledge.subtopic_id == Subtopic.id)
        .join(Topic, Subtopic.topic_id == Topic.id)
        .where(UserKnowledge.user_id == user_id)
        .order_by(Topic.name.asc(), Subtopic.name.asc())
    )
    rows = db.execute(stmt).all()
    return [
        {
            "topic": topic_name,
            "subtopic": sub_name,
            "mastery": mastery,
            "notes": notes or "",
        }
        for mastery, notes, sub_id, sub_name, topic_name in rows
    ]
