# otomata-worker

Generic task worker with Claude Agent SDK. Multi-tenant, chat-based agent execution with SSE streaming.

## Features

- **Chat management** — multi-tenant chats with metadata filtering
- **Agent execution** — Claude Agent SDK with configurable tools, workspace, and system prompts
- **SSE streaming** — real-time text, tool_use, and thinking events
- **Task queue** — PostgreSQL-backed with `SELECT FOR UPDATE SKIP LOCKED`
- **Per-turn events** — text and tool_use events persisted for replay on page reload
- **Identity management** — rate-limited identities for external services
- **Encrypted secrets** — Fernet-encrypted key-value store

## Stack

- Python 3.11+, FastAPI, SQLAlchemy, PostgreSQL
- [claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/)
- Typer (CLI), Rich (output)

## Install

```bash
pip install -e ".[agent,dev]"
```

## Quick Start

```bash
# Set environment
export DATABASE_URL=postgresql://user:pass@localhost/otomata
export ANTHROPIC_API_KEY=sk-ant-...

# Init database
otomata-worker db init

# Start server + worker
otomata-worker serve --port 7001
```

## API

### Chats
- `GET /chats?tenant=X&metadata_client_id=Y` — List chats (filtered)
- `POST /chats` — Create chat (tenant, system_prompt, workspace, allowed_tools, max_turns, metadata)
- `GET /chats/{id}` — Chat detail with messages
- `PATCH /chats/{id}` — Update chat
- `GET /chats/{id}/messages?include_tools=true` — Messages with per-turn text + tool_use interleaved
- `POST /chats/{id}/messages` — Send message → creates agent task
- `GET /chats/{id}/events` — SSE stream for active task

### Tasks
- `GET /tasks?status=X` — List tasks
- `GET /tasks/{id}` — Task detail
- `POST /tasks/{id}/retry` — Retry failed task

## SSE Events

Stream via `GET /chats/{id}/events`. JSON events:

| Event | Fields | Description |
|-------|--------|-------------|
| `start` | `model` | Agent execution begins |
| `text` | `content`, `turn` | Agent text output (per turn) |
| `thinking` | `turn` | Agent thinking |
| `tool_use` | `tool`, `count`, `input` | Tool invocation |
| `complete` | `tool_count`, `input_tokens`, `output_tokens` | Execution finished |
| `error` | `error` | Exception occurred |
| `no_task` | — | No active task for chat |

## CLI

```bash
otomata-worker serve [--port 7001]          # FastAPI server + worker loop
otomata-worker run                           # Worker loop only (no API)
otomata-worker task list [--status X]        # List tasks
otomata-worker task create --type agent --prompt "..."
otomata-worker db init                       # Create tables
otomata-worker db migrate                    # Migrate schema
otomata-worker secrets list/set/get/delete
otomata-worker identities list/add/status/block/unblock
```

## Environment

| Var | Description |
|-----|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Claude API key |
| `OTOMATA_API_KEY` | API auth (empty = no auth) |
| `CORS_ORIGINS` | Allowed origins (default: `*`) |
| `POLL_INTERVAL` | Worker poll seconds (default: `5`) |

## Architecture

```
otomata_worker/
├── server.py        # FastAPI app, REST API + SSE streaming
├── cli.py           # Typer CLI
├── models.py        # SQLAlchemy: Chat, Message, Task, TaskEvent, Identity, RateLimit, Secret
├── chat_manager.py  # Chat CRUD, message history, per-turn event interleaving
├── task_manager.py  # Task lifecycle (create, claim, complete, fail, retry)
├── worker.py        # Poll loop, async_poll_loop for FastAPI coexistence
├── events.py        # In-memory + DB event store, asyncio signaling for SSE
├── database.py      # Session management (get_session context manager)
├── identities.py    # Identity management
├── rate_limiter.py  # DB-backed rate limits per identity/action
├── secrets.py       # Encrypted secrets (Fernet)
└── executors/
    ├── agent.py     # Claude Agent SDK executor, emits SSE events
    └── script.py    # Script executor
```

## License

MIT
