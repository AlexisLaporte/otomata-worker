# otomata-worker

Generic task worker with Claude Agent SDK. Multi-tenant, chat-based agent execution with SSE streaming.

## Stack
- Python 3.10+, FastAPI, SQLAlchemy, PostgreSQL
- claude-agent-sdk (agent executor)
- Typer (CLI), Rich (output)
- Install: `pip install -e ".[agent,dev]" --break-system-packages`

## Structure
```
otomata_worker/
├── server.py        # FastAPI app, REST API + SSE streaming
├── cli.py           # Typer CLI (serve, run, task, secrets, identities, db)
├── models.py        # SQLAlchemy: Chat, Message, Task, TaskEvent, Identity, RateLimit, Secret
├── chat_manager.py  # Chat CRUD, message history, list_chats(tenant, metadata_filter)
├── task_manager.py  # Task lifecycle (create, claim, complete, fail, retry)
├── worker.py        # Poll loop: claim pending tasks, execute, async_poll_loop for FastAPI
├── events.py        # TaskEventStore: in-memory + DB persist, asyncio signaling for SSE
├── database.py      # Session management (get_session context manager, auto-commit/rollback)
├── identities.py    # Identity management (LinkedIn, etc.)
├── rate_limiter.py   # DB-backed rate limits per identity/action
├── secrets.py       # Encrypted secrets (Fernet)
└── executors/
    ├── agent.py     # Claude Agent SDK executor, emits SSE events (text, tool_use, thinking, complete)
    └── script.py    # Script executor
```

## API

**Chats:**
- `GET /chats?tenant=X&metadata_client_id=Y` - List chats (filtered)
- `POST /chats` - Create chat (tenant, system_prompt, workspace, allowed_tools, max_turns, metadata)
- `GET /chats/{id}` - Chat detail with messages
- `PATCH /chats/{id}` - Update chat (system_prompt, workspace, allowed_tools, max_turns, metadata)
- `GET /chats/{id}/messages?include_tools=true` - List messages (with per-turn text + tool_use interleaved)
- `POST /chats/{id}/messages` - Send message → creates agent task
- `GET /chats/{id}/events` - SSE stream for active task

**Tasks:**
- `GET /tasks?status=X` - List tasks
- `GET /tasks/{id}` - Task detail
- `POST /tasks/{id}/retry` - Retry failed task

## SSE Events

Stream via `GET /chats/{id}/events`. JSON events:

| Event | Fields | Description |
|-------|--------|-------------|
| `start` | `model` | Agent execution begins |
| `text` | `content`, `turn` | Agent text output (per turn) |
| `thinking` | `turn` | Agent thinking (no tool use) |
| `tool_use` | `tool`, `count`, `input` | Tool invocation |
| `complete` | `tool_count`, `input_tokens`, `output_tokens` | Execution finished |
| `error` | `error` | Exception occurred |
| `no_task` | — | No active task for chat |

## Key Patterns
- `get_session()` context manager with auto-commit/rollback
- `SELECT FOR UPDATE SKIP LOCKED` for PostgreSQL task claiming
- `event_store` global singleton: in-memory + DB persist, asyncio signaling for SSE
- `async_poll_loop` uses `asyncio.to_thread(worker.process_one)` for FastAPI coexistence
- Chat metadata (JSON): stores tenant-specific data (e.g. `client_id`, `title` for FinanceX)

## CLI

```bash
otomata-worker serve [--port 7001]     # FastAPI server + worker loop
otomata-worker run                      # Worker loop only (no API)
otomata-worker task list [--status X]   # List tasks
otomata-worker task create --type agent --prompt "..."
otomata-worker db init                  # Create tables
otomata-worker db migrate              # Migrate schema
otomata-worker secrets list/set/get/delete
otomata-worker identities list/add/status/block/unblock
```

## Env

| Var | Description |
|-----|-------------|
| DATABASE_URL | PostgreSQL connection string |
| OTOMATA_API_KEY | API auth (empty = no auth) |
| CORS_ORIGINS | Allowed origins (default: *) |
| POLL_INTERVAL | Worker poll seconds (default: 5) |
| ANTHROPIC_API_KEY | Claude API key |

## Prod

```bash
ssh -i ~/.ssh/alexis root@51.15.225.121
cd /opt/otomata-worker
```

Service: `financex-worker` (systemd), installed globally via `pip install -e ".[agent]"`
