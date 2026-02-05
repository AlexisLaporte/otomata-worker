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

    # Execution
    claimed_by = Column(String(100), index=True)  # worker-{hostname}
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error = Column(Text)
    result = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)

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
