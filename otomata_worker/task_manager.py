"""Task management - create, claim, complete tasks."""

from datetime import datetime
from typing import Optional

from sqlalchemy import text

from .models import Task, TaskStatus
from .database import get_session


class TaskManager:
    """Manage task lifecycle."""

    def create(
        self,
        task_type: str,
        script_path: Optional[str] = None,
        params: Optional[dict] = None,
        prompt: Optional[str] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> int:
        """Create a new task.

        Args:
            task_type: Type of task ('script' or 'agent')
            script_path: Path to script (for type='script')
            params: Parameters dict
            prompt: Agent prompt (for type='agent')
            workspace: Working directory
            session_id: Claude session ID (for resume)

        Returns:
            Created Task ID
        """
        with get_session() as session:
            task = Task(
                task_type=task_type,
                status=TaskStatus.PENDING,
                script_path=script_path,
                params=params,
                prompt=prompt,
                workspace=workspace,
                session_id=session_id
            )
            session.add(task)
            session.flush()
            task_id = task.id
            return task_id

    def claim(self, worker_id: str) -> Optional[Task]:
        """Claim the next available pending task.

        Uses SELECT FOR UPDATE SKIP LOCKED for safe concurrent claiming.

        Args:
            worker_id: Unique worker identifier (e.g., 'worker-hostname')

        Returns:
            Claimed Task or None if no tasks available
        """
        with get_session() as session:
            # Use raw SQL for SKIP LOCKED (PostgreSQL)
            result = session.execute(
                text("""
                    SELECT id FROM tasks
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                """)
            )
            row = result.fetchone()

            if not row:
                return None

            task = session.query(Task).get(row[0])
            task.status = TaskStatus.RUNNING
            task.claimed_by = worker_id
            task.started_at = datetime.utcnow()

            session.flush()
            session.expunge(task)
            return task

    def complete(self, task_id: int, result: Optional[dict] = None):
        """Mark task as completed.

        Args:
            task_id: Task ID
            result: Result data dict
        """
        with get_session() as session:
            task = session.query(Task).get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.utcnow()
                task.result = result

    def fail(self, task_id: int, error: str):
        """Mark task as failed.

        Args:
            task_id: Task ID
            error: Error message
        """
        with get_session() as session:
            task = session.query(Task).get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.utcnow()
                task.error = error

    def get(self, task_id: int) -> Optional[Task]:
        """Get task by ID."""
        with get_session() as session:
            task = session.query(Task).get(task_id)
            if task:
                session.expunge(task)
            return task

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50
    ) -> list[dict]:
        """List tasks with optional status filter.

        Args:
            status: Filter by status
            limit: Max tasks to return

        Returns:
            List of task dicts
        """
        with get_session() as session:
            query = session.query(Task)

            if status:
                query = query.filter(Task.status == status)

            tasks = query.order_by(Task.created_at.desc()).limit(limit).all()

            return [
                {
                    'id': t.id,
                    'task_type': t.task_type,
                    'status': t.status.value,
                    'claimed_by': t.claimed_by,
                    'created_at': t.created_at.isoformat() if t.created_at else None,
                    'started_at': t.started_at.isoformat() if t.started_at else None,
                    'completed_at': t.completed_at.isoformat() if t.completed_at else None,
                    'error': t.error,
                }
                for t in tasks
            ]

    def update_session_id(self, task_id: int, session_id: str):
        """Update Claude session ID for agent tasks."""
        with get_session() as session:
            task = session.query(Task).get(task_id)
            if task:
                task.session_id = session_id

    def retry(self, task_id: int) -> bool:
        """Reset a failed task to pending.

        Args:
            task_id: Task ID

        Returns:
            True if task was reset
        """
        with get_session() as session:
            task = session.query(Task).get(task_id)
            if task and task.status == TaskStatus.FAILED:
                task.status = TaskStatus.PENDING
                task.claimed_by = None
                task.started_at = None
                task.completed_at = None
                task.error = None
                return True
            return False

    def cancel(self, task_id: int) -> bool:
        """Cancel a pending task.

        Args:
            task_id: Task ID

        Returns:
            True if task was cancelled
        """
        with get_session() as session:
            task = session.query(Task).get(task_id)
            if task and task.status == TaskStatus.PENDING:
                session.delete(task)
                return True
            return False
