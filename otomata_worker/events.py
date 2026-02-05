"""Event streaming for task execution progress."""

import json
import asyncio
from datetime import datetime
from collections import defaultdict

from .models import TaskEvent
from .database import get_session


class TaskEventStore:
    """Store and retrieve task events for SSE streaming."""

    def __init__(self):
        self.events: dict[int, list[dict]] = defaultdict(list)
        self.event_signals: dict[int, asyncio.Event] = {}

    def add_event(self, task_id: int, event_type: str, data: dict):
        """Add an event for a task (in-memory + DB)."""
        event = {
            'type': event_type,
            'timestamp': datetime.utcnow().isoformat(),
            **data
        }
        self.events[task_id].append(event)

        # Persist to DB
        self._save_to_db(task_id, event_type, data)

        # Signal waiting listeners
        if task_id in self.event_signals:
            self.event_signals[task_id].set()

    def _save_to_db(self, task_id: int, event_type: str, data: dict):
        """Persist event to database."""
        try:
            with get_session() as session:
                from sqlalchemy import func
                max_seq = session.query(func.max(TaskEvent.sequence)).filter(
                    TaskEvent.task_id == task_id
                ).scalar() or 0

                event = TaskEvent(
                    task_id=task_id,
                    event_type=event_type,
                    event_data=data,
                    sequence=max_seq + 1
                )
                session.add(event)
        except Exception as e:
            print(f"[EventStore] Failed to save event to DB: {e}")

    def get_events(self, task_id: int, after_index: int = 0) -> list[dict]:
        """Get events for a task after a given index."""
        return self.events[task_id][after_index:]

    async def wait_for_event(self, task_id: int, timeout: float = 30.0) -> bool:
        """Wait for a new event on this task. Returns True if event arrived."""
        if task_id not in self.event_signals:
            self.event_signals[task_id] = asyncio.Event()

        try:
            await asyncio.wait_for(self.event_signals[task_id].wait(), timeout)
            self.event_signals[task_id].clear()
            return True
        except asyncio.TimeoutError:
            return False

    def cleanup(self, task_id: int):
        """Free memory for completed task."""
        self.events.pop(task_id, None)
        self.event_signals.pop(task_id, None)


# Global singleton
event_store = TaskEventStore()
