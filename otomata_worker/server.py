"""FastAPI server with REST API, SSE streaming, and integrated worker."""

import asyncio
import json
import os
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .database import init_db
from .task_manager import TaskManager
from .chat_manager import ChatManager
from .events import event_store
from .worker import async_poll_loop

logger = logging.getLogger(__name__)

# --- Auth ---

API_KEY = os.environ.get('OTOMATA_API_KEY', '')


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verify API key if OTOMATA_API_KEY is set."""
    if not API_KEY:
        return  # No auth in dev
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# --- Pydantic models ---

class CreateChatRequest(BaseModel):
    tenant: str
    system_prompt: str
    workspace: Optional[str] = None
    allowed_tools: Optional[list] = None
    max_turns: int = 50
    metadata: Optional[dict] = None


class UpdateChatRequest(BaseModel):
    system_prompt: Optional[str] = None
    workspace: Optional[str] = None
    allowed_tools: Optional[list] = None
    max_turns: Optional[int] = None
    metadata: Optional[dict] = None


class SendMessageRequest(BaseModel):
    content: str


# --- App factory ---

def create_app() -> FastAPI:
    app = FastAPI(title="Otomata Worker")

    # CORS
    origins = os.environ.get('CORS_ORIGINS', '*').split(',')
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    task_manager = TaskManager()
    chat_manager = ChatManager()
    background_tasks = {}

    @app.on_event("startup")
    async def startup():
        init_db()
        logger.info("Database initialized")

        poll_interval = int(os.environ.get('POLL_INTERVAL', '5'))
        poll_task = asyncio.create_task(async_poll_loop(poll_interval=poll_interval))
        background_tasks['poll'] = poll_task
        logger.info(f"Worker poll loop started (interval: {poll_interval}s)")

    @app.on_event("shutdown")
    async def shutdown():
        for name, task in background_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # --- Health ---

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # --- Chats ---

    @app.get("/chats", dependencies=[Depends(verify_api_key)])
    def list_chats(tenant: Optional[str] = None, metadata_client_id: Optional[str] = None):
        meta_filter = {}
        if metadata_client_id:
            meta_filter['client_id'] = metadata_client_id
        return chat_manager.list_chats(tenant=tenant, metadata_filter=meta_filter or None)

    @app.post("/chats", status_code=201, dependencies=[Depends(verify_api_key)])
    def create_chat(req: CreateChatRequest):
        chat_id = chat_manager.create_chat(
            tenant=req.tenant,
            system_prompt=req.system_prompt,
            workspace=req.workspace,
            allowed_tools=req.allowed_tools,
            max_turns=req.max_turns,
            metadata=req.metadata,
        )
        return {"id": chat_id}

    @app.get("/chats/{chat_id}", dependencies=[Depends(verify_api_key)])
    def get_chat(chat_id: int):
        chat = chat_manager.get_chat_with_messages(chat_id)
        if not chat:
            raise HTTPException(404, "Chat not found")
        return chat

    @app.patch("/chats/{chat_id}", dependencies=[Depends(verify_api_key)])
    def update_chat(chat_id: int, req: UpdateChatRequest):
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(400, "No fields to update")
        ok = chat_manager.update_chat(chat_id, **updates)
        if not ok:
            raise HTTPException(404, "Chat not found")
        return {"ok": True}

    @app.get("/chats/{chat_id}/messages", dependencies=[Depends(verify_api_key)])
    def list_messages(chat_id: int, include_tools: bool = False):
        chat = chat_manager.get_chat(chat_id)
        if not chat:
            raise HTTPException(404, "Chat not found")
        return chat_manager.list_messages(chat_id, include_tools=include_tools)

    @app.post("/chats/{chat_id}/messages", status_code=202, dependencies=[Depends(verify_api_key)])
    def send_message(chat_id: int, req: SendMessageRequest):
        chat = chat_manager.get_chat(chat_id)
        if not chat:
            raise HTTPException(404, "Chat not found")

        # Check no active task already
        active = task_manager.get_active_task_for_chat(chat_id)
        if active:
            raise HTTPException(409, f"Chat already has active task {active.id}")

        # Create agent task linked to chat
        task_id = task_manager.create(
            task_type='agent',
            prompt=req.content,
            workspace=chat.get('workspace'),
            chat_id=chat_id,
        )
        return {"task_id": task_id}

    @app.get("/chats/{chat_id}/events", dependencies=[Depends(verify_api_key)])
    async def stream_events(chat_id: int):
        """SSE endpoint streaming events for the active task of a chat."""
        async def event_stream():
            # Find active task
            task = task_manager.get_active_task_for_chat(chat_id)
            if not task:
                yield f"data: {json.dumps({'type': 'no_task'})}\n\n"
                return

            task_id = task.id
            event_index = 0

            while True:
                events = event_store.get_events(task_id, after_index=event_index)

                for event in events:
                    yield f"data: {json.dumps(event)}\n\n"
                    event_index += 1

                    if event['type'] in ('complete', 'error'):
                        return

                has_new = await event_store.wait_for_event(task_id, timeout=30.0)
                if not has_new:
                    # Keepalive
                    yield ": keepalive\n\n"

                    # Check if task finished (DB)
                    t = task_manager.get(task_id)
                    if t and t.status.value in ('completed', 'failed'):
                        yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                        return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

    # --- Usage ---

    @app.get("/usage", dependencies=[Depends(verify_api_key)])
    def get_usage(tenant: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None):
        """Get aggregated token usage with cost estimate."""
        from datetime import date as date_type
        since_date = date_type.fromisoformat(since) if since else None
        until_date = date_type.fromisoformat(until) if until else None
        usage = chat_manager.get_usage(tenant=tenant, since=since_date, until=until_date)

        # Cost estimate (Sonnet 4: $3/MTok input, $15/MTok output)
        input_cost = usage['total_input_tokens'] * 3.0 / 1_000_000
        output_cost = usage['total_output_tokens'] * 15.0 / 1_000_000
        usage['estimated_cost_usd'] = round(input_cost + output_cost, 4)
        usage['pricing_note'] = 'claude-sonnet-4 ($3/MTok in, $15/MTok out)'

        return usage

    # --- Tasks ---

    @app.get("/tasks", dependencies=[Depends(verify_api_key)])
    def list_tasks(status: Optional[str] = None, limit: int = 50):
        from .models import TaskStatus
        filter_status = TaskStatus(status) if status else None
        return task_manager.list_tasks(status=filter_status, limit=limit)

    @app.get("/tasks/{task_id}", dependencies=[Depends(verify_api_key)])
    def get_task(task_id: int):
        task = task_manager.get(task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return {
            'id': task.id,
            'task_type': task.task_type,
            'status': task.status.value,
            'chat_id': task.chat_id,
            'claimed_by': task.claimed_by,
            'created_at': task.created_at.isoformat() if task.created_at else None,
            'started_at': task.started_at.isoformat() if task.started_at else None,
            'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            'error': task.error,
        }

    @app.post("/tasks/{task_id}/retry", dependencies=[Depends(verify_api_key)])
    def retry_task(task_id: int):
        if task_manager.retry(task_id):
            return {"ok": True}
        raise HTTPException(400, "Cannot retry (not failed or not found)")

    return app


# Module-level app instance for uvicorn
app = create_app()
