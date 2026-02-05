"""SQLAlchemy models for otomata-worker."""

import os
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, JSON, ForeignKey,
    Enum as SQLEnum, create_engine
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Chat(Base):
    """Chat session with system prompt and config."""
    __tablename__ = 'chats'

    id = Column(Integer, primary_key=True)
    tenant = Column(String(100), index=True)
    metadata_ = Column('metadata', JSON)
    system_prompt = Column(Text)
    workspace = Column(String(255))
    allowed_tools = Column(JSON)
    max_turns = Column(Integer, default=50)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    messages = relationship("Message", back_populates="chat", order_by="Message.sequence")
    tasks = relationship("Task", back_populates="chat")

    def __repr__(self):
        return f"<Chat {self.id} tenant={self.tenant}>"


class Message(Base):
    """Chat message."""
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), index=True)
    role = Column(
        SQLEnum(MessageRole, values_callable=lambda x: [e.value for e in x], create_constraint=False, native_enum=True, name='messagerole'),
    )
    content = Column(Text)
    sequence = Column(Integer)
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    chat = relationship("Chat", back_populates="messages")

    def __repr__(self):
        return f"<Message {self.id} chat={self.chat_id} role={self.role}>"


class TaskEvent(Base):
    """Event emitted during task execution."""
    __tablename__ = 'task_events'

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'), index=True)
    event_type = Column(String(50))
    event_data = Column(JSON)
    sequence = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="events")

    def __repr__(self):
        return f"<TaskEvent {self.id} task={self.task_id} type={self.event_type}>"


class Task(Base):
    """Task model - simplified job execution."""
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    status = Column(
        SQLEnum(TaskStatus, values_callable=lambda x: [e.value for e in x], create_constraint=False, native_enum=True, name='taskstatus'),
        default=TaskStatus.PENDING,
        index=True
    )

    # Type and config
    task_type = Column(String(20))  # script, agent
    script_path = Column(Text)  # For type=script
    params = Column(JSON)  # Parameters

    # Agent (Claude SDK)
    prompt = Column(Text)  # For type=agent
    session_id = Column(String(100))  # Claude session_id (for resume)
    workspace = Column(String(255))  # cwd for agent

    # Chat link
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=True, index=True)

    # Execution
    claimed_by = Column(String(100), index=True)  # worker-{hostname}
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error = Column(Text)
    result = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    chat = relationship("Chat", back_populates="tasks")
    events = relationship("TaskEvent", back_populates="task", order_by="TaskEvent.sequence")

    def __repr__(self):
        return f"<Task {self.id} type={self.task_type} status={self.status}>"


class Identity(Base):
    """Platform identity (LinkedIn, Kaspr, etc.)."""
    __tablename__ = 'identities'

    id = Column(Integer, primary_key=True)
    platform = Column(String(50), index=True)  # linkedin, kaspr
    name = Column(String(100))  # marie.dupont
    account_type = Column(String(20))  # free, premium

    # Credentials (encrypted via SecretsService)
    cookie_encrypted = Column(Text)  # li_at for LinkedIn
    user_agent = Column(Text)

    # Status
    status = Column(String(20), default='active')  # active, blocked, warming
    blocked_at = Column(DateTime)
    blocked_reason = Column(Text)
    last_used_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    rate_limits = relationship("RateLimit", back_populates="identity")

    def __repr__(self):
        return f"<Identity {self.platform}/{self.name} status={self.status}>"


class RateLimit(Base):
    """DB-backed rate limits per identity/action."""
    __tablename__ = 'rate_limits'

    id = Column(Integer, primary_key=True)
    identity_id = Column(Integer, ForeignKey('identities.id'), index=True)
    action_type = Column(String(50))  # profile_visit, search

    date = Column(Date, index=True)
    hourly_timestamps = Column(JSON, default=list)  # Timestamps last hour
    daily_count = Column(Integer, default=0)
    last_request_at = Column(DateTime)

    # Relationships
    identity = relationship("Identity", back_populates="rate_limits")

    def __repr__(self):
        return f"<RateLimit identity={self.identity_id} action={self.action_type} daily={self.daily_count}>"


class SecretScope(str, Enum):
    PLATFORM = "PLATFORM"
    USER = "USER"


class Secret(Base):
    """Encrypted secrets storage."""
    __tablename__ = 'secrets'

    id = Column(Integer, primary_key=True)
    key = Column(String(100), index=True)
    scope = Column(
        SQLEnum(SecretScope, values_callable=lambda x: [e.value for e in x], create_constraint=False, native_enum=True, name='secretscope'),
        default=SecretScope.PLATFORM
    )
    user_id = Column(Integer, nullable=True)
    encrypted_value = Column(Text)
    description = Column(Text)
    expires_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Secret {self.key} scope={self.scope}>"


def get_engine():
    """Get SQLAlchemy engine from DATABASE_URL."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL not set")
    return create_engine(database_url)


def init_db(engine=None):
    """Initialize database tables."""
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
    return engine
