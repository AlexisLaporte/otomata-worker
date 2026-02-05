"""Task executors."""

from .script import execute_script
from .agent import execute_agent

__all__ = ["execute_script", "execute_agent"]
