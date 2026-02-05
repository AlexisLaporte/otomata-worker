"""Otomata Worker - Distributed task execution with Claude Agent SDK."""

__version__ = "0.1.0"

from .models import Task, TaskStatus, Identity, RateLimit, Secret, SecretScope
from .database import get_session, init_db
from .secrets import SecretsService, secrets_service
from .identities import IdentityManager
from .rate_limiter import DBRateLimiter
from .task_manager import TaskManager

__all__ = [
    "Task",
    "TaskStatus",
    "Identity",
    "RateLimit",
    "Secret",
    "SecretScope",
    "get_session",
    "init_db",
    "SecretsService",
    "secrets_service",
    "IdentityManager",
    "DBRateLimiter",
    "TaskManager",
]
