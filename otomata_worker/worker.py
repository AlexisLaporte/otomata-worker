"""Worker polling loop."""

import asyncio
import os
import socket
import time
import signal
import sys
from typing import Optional

from .models import Task, TaskStatus
from .task_manager import TaskManager
from .chat_manager import ChatManager
from .secrets import secrets_service
from .events import event_store
from .executors.script import execute_script
from .executors.agent import execute_agent, run_agent


class Worker:
    """Task worker that polls for and executes tasks."""

    def __init__(
        self,
        workspace: Optional[str] = None,
        worker_id: Optional[str] = None,
        poll_interval: int = 5
    ):
        self.workspace = workspace or os.getcwd()
        self.worker_id = worker_id or f"worker-{socket.gethostname()}"
        self.poll_interval = poll_interval
        self.task_manager = TaskManager()
        self.chat_manager = ChatManager()
        self.running = False

    def execute_task(self, task: Task) -> dict:
        """Execute a single task."""
        workspace = task.workspace or self.workspace

        if task.task_type == 'script':
            required_secrets = None
            if task.params and 'required_secrets' in task.params:
                required_secrets = task.params['required_secrets']

            success, output, metadata = execute_script(
                script_path=task.script_path,
                params=task.params,
                workspace=workspace,
                required_secrets=required_secrets,
                task_id=task.id
            )

            if success:
                return {'success': True, 'output': output, 'metadata': metadata}
            else:
                return {'success': False, 'error': output, 'metadata': metadata}

        elif task.task_type == 'agent':
            secrets = None
            if task.params and 'required_secrets' in task.params:
                secrets = secrets_service.get_for_task(task.params['required_secrets'])

            # Chat-aware execution
            if task.chat_id:
                return self._execute_chat_agent(task, secrets)

            # Legacy standalone agent
            result = run_agent(task, secrets)
            if result.get('session_id'):
                self.task_manager.update_session_id(task.id, result['session_id'])
            return result

        else:
            return {'success': False, 'error': f"Unknown task type: {task.task_type}"}

    def _execute_chat_agent(self, task: Task, secrets: Optional[dict] = None) -> dict:
        """Execute agent task with chat context."""
        chat = self.chat_manager.get_chat(task.chat_id)
        if not chat:
            return {'success': False, 'error': f"Chat {task.chat_id} not found"}

        history = self.chat_manager.get_history(task.chat_id)

        result = run_agent(
            task,
            secrets=secrets,
            history=history,
            system_prompt=chat['system_prompt'],
            allowed_tools=chat['allowed_tools'],
            max_turns=chat['max_turns'],
            env={},
            emit_events=True,
        )

        if result.get('success'):
            # Save user message + assistant response
            self.chat_manager.add_message(
                chat_id=task.chat_id,
                role='user',
                content=task.prompt,
            )
            self.chat_manager.add_message(
                chat_id=task.chat_id,
                role='assistant',
                content=result.get('output', ''),
                tokens_input=result.get('input_tokens', 0),
                tokens_output=result.get('output_tokens', 0),
            )

        # Cleanup event store
        event_store.cleanup(task.id)

        return result

    def process_one(self) -> bool:
        """Try to claim and process one task.

        Returns:
            True if a task was processed
        """
        task = self.task_manager.claim(self.worker_id)
        if not task:
            return False

        print(f"[{self.worker_id}] Processing task {task.id} ({task.task_type})")

        try:
            result = self.execute_task(task)

            if result.get('success'):
                self.task_manager.complete(task.id, result)
                print(f"[{self.worker_id}] Task {task.id} completed")
            else:
                error = result.get('error', 'Unknown error')
                self.task_manager.fail(task.id, error)
                print(f"[{self.worker_id}] Task {task.id} failed: {error[:100]}")

        except Exception as e:
            self.task_manager.fail(task.id, str(e))
            print(f"[{self.worker_id}] Task {task.id} exception: {e}")

        return True

    def run(self):
        """Start the worker polling loop (blocking, for CLI use)."""
        self.running = True

        def handle_signal(signum, frame):
            print(f"\n[{self.worker_id}] Shutting down...")
            self.running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        print(f"[{self.worker_id}] Starting worker")
        print(f"[{self.worker_id}] Workspace: {self.workspace}")
        print(f"[{self.worker_id}] Poll interval: {self.poll_interval}s")

        while self.running:
            try:
                if not self.process_one():
                    time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[{self.worker_id}] Error: {e}")
                time.sleep(self.poll_interval)

        print(f"[{self.worker_id}] Worker stopped")


async def async_poll_loop(
    workspace: Optional[str] = None,
    worker_id: Optional[str] = None,
    poll_interval: int = 5
):
    """Async polling loop for coexistence with FastAPI event loop."""
    worker = Worker(
        workspace=workspace,
        worker_id=worker_id,
        poll_interval=poll_interval
    )
    worker.running = True

    print(f"[{worker.worker_id}] Starting async poll loop (interval: {poll_interval}s)")

    while worker.running:
        try:
            processed = await asyncio.to_thread(worker.process_one)
            if not processed:
                await asyncio.sleep(poll_interval)
        except Exception as e:
            print(f"[{worker.worker_id}] Poll error: {e}")
            await asyncio.sleep(poll_interval)


def run_worker(
    workspace: Optional[str] = None,
    worker_id: Optional[str] = None,
    poll_interval: int = 5
):
    """Run the worker (convenience function)."""
    worker = Worker(
        workspace=workspace,
        worker_id=worker_id,
        poll_interval=poll_interval
    )
    worker.run()
