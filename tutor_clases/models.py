# models.py (versión optimizada 2025-10-22)
# -------------------------------------------
# Define la estructura ORM de SQLAlchemy según la BD 'tutor_ai'
# Compatible con MySQL 8.0+
# Incluye soporte para persona, waiting_for_user y notas extendidas

from datetime import datetime
from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Enum,
    Text,
    JSON,
    TIMESTAMP,
    ForeignKey,
    Boolean,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ============================================================
# USUARIOS
# ============================================================
class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    email = Column(String(190), unique=True, nullable=False)
    name = Column(String(190), nullable=False)
    default_level = Column(Enum("basico", "intermedio", "avanzado"), default="basico")
    default_style = Column(Enum("socratico", "paso-a-paso", "mixto"), default="mixto")
    default_pace = Column(Enum("lento", "medio", "rapido"), default="medio")
    default_detail = Column(
        Enum("breve", "intermedio", "profundo"), default="intermedio"
    )
    locale = Column(String(10), default="es")
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    sessions = relationship(
        "Session", back_populates="user", cascade="all,delete-orphan"
    )


# ============================================================
# SESIONES DE TUTORÍA
# ============================================================
class Session(Base):
    __tablename__ = "sessions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_key = Column(String(64), unique=True, nullable=False)

    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    phase = Column(Enum("intake", "plan", "teach", "reflect"), default="intake")

    topic = Column(String(255))
    current_subtopic_id = Column(
        BigInteger, ForeignKey("subtopics.id", ondelete="SET NULL")
    )
    current_subtopic_name = Column(String(190))

    level = Column(Enum("basico", "intermedio", "avanzado"), default="basico")
    style = Column(Enum("socratico", "paso-a-paso", "mixto"), default="mixto")
    pace = Column(Enum("lento", "medio", "rapido"), default="medio")
    detail = Column(Enum("breve", "intermedio", "profundo"), default="intermedio")
    goal = Column(String(255))

    # nuevos campos
    persona = Column(
        Enum("neutral", "amable", "divertido", "estricto"), default="neutral"
    )
    waiting_for_user = Column(Boolean, default=False)

    state_json = Column(JSON)  # snapshot completo del estado
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    messages = relationship(
        "Message", back_populates="session", cascade="all,delete-orphan"
    )
    memory_points = relationship(
        "SessionMemoryPoint", back_populates="session", cascade="all,delete-orphan"
    )


# ============================================================
# MENSAJES DE CHAT
# ============================================================
class Message(Base):
    __tablename__ = "messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        BigInteger, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(Enum("user", "assistant"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    session = relationship("Session", back_populates="messages")

    __table_args__ = (
        Index("idx_messages_session_created", "session_id", "created_at"),
    )


# ============================================================
# PUNTOS DE MEMORIA (para recordar avances)
# ============================================================
class SessionMemoryPoint(Base):
    __tablename__ = "session_memory_points"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        BigInteger, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    point_text = Column(String(500), nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    session = relationship("Session", back_populates="memory_points")


# ============================================================
# TEMAS Y SUBTEMAS
# ============================================================
class Topic(Base):
    __tablename__ = "topics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(190), unique=True, nullable=False)
    description = Column(Text)


class Subtopic(Base):
    __tablename__ = "subtopics"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    topic_id = Column(
        BigInteger, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(190), nullable=False)

    __table_args__ = (UniqueConstraint("topic_id", "name", name="uq_topic_subtopic"),)


# ============================================================
# EJERCICIOS Y PISTAS
# ============================================================
class Exercise(Base):
    __tablename__ = "exercises"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    subtopic_id = Column(
        BigInteger, ForeignKey("subtopics.id", ondelete="CASCADE"), nullable=False
    )
    difficulty = Column(Integer, default=1)
    prompt = Column(Text, nullable=False)
    solution = Column(Text)
    tags = Column(JSON)


class ExerciseHint(Base):
    __tablename__ = "exercise_hints"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    exercise_id = Column(
        BigInteger, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False
    )
    level = Column(Enum("leve", "dirigida", "resolucion"), nullable=False)
    text = Column(Text, nullable=False)


# ============================================================
# PLANTILLAS DE PLANES
# ============================================================
class PlanTemplate(Base):
    __tablename__ = "plan_templates"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    subtopic_id = Column(
        BigInteger, ForeignKey("subtopics.id", ondelete="CASCADE"), nullable=False
    )
    level = Column(Enum("basico", "intermedio", "avanzado"), nullable=False)
    style = Column(Enum("socratico", "paso-a-paso", "mixto"), nullable=False)
    pace = Column(Enum("lento", "medio", "rapido"), nullable=False)
    steps = Column(JSON, nullable=False)

    __table_args__ = (
        Index("idx_plan_templates_full", "subtopic_id", "level", "style", "pace"),
    )


# ============================================================
# CONOCIMIENTO Y DOMINIO DEL USUARIO
# ============================================================
class UserKnowledge(Base):
    __tablename__ = "user_knowledge"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subtopic_id = Column(
        BigInteger, ForeignKey("subtopics.id", ondelete="CASCADE"), nullable=False
    )
    mastery = Column(Integer, default=0)
    notes = Column(Text)  # extendido a MEDIUMTEXT en BD real

    __table_args__ = (
        UniqueConstraint("user_id", "subtopic_id", name="uq_user_subtopic"),
    )


# ============================================================
# CACHE DE RESPUESTAS LLM (para optimización de rendimiento)
# ============================================================
class LlmCache(Base):
    __tablename__ = "llm_cache"

    hash = Column(String(64), primary_key=True)
    model = Column(String(190))
    prompt = Column(Text)
    response = Column(Text)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    ttl_s = Column(Integer, default=86400)


class SessionActivityLog(Base):
    """Registro de actividad utilizado por crud.log_activity y el reinicio."""
    __tablename__ = "session_activity_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        BigInteger, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    action = Column(String(100), nullable=False)
    detail = Column(Text)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
