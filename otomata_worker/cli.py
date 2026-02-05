"""CLI for otomata-worker."""

import json
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Otomata Worker - Distributed task execution")
console = Console()

# Sub-apps
task_app = typer.Typer(help="Task management")
secrets_app = typer.Typer(help="Secrets management")
identities_app = typer.Typer(help="Identity management")
db_app = typer.Typer(help="Database management")

app.add_typer(task_app, name="task")
app.add_typer(secrets_app, name="secrets")
app.add_typer(identities_app, name="identities")
app.add_typer(db_app, name="db")


# === Worker ===

@app.command()
def run(
    workspace: str = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    worker_id: str = typer.Option(None, "--id", help="Worker ID"),
    poll_interval: int = typer.Option(5, "--interval", "-i", help="Poll interval in seconds")
):
    """Run the worker loop (poll only, no API)."""
    from .worker import run_worker
    run_worker(
        workspace=workspace or os.getcwd(),
        worker_id=worker_id,
        poll_interval=poll_interval
    )


@app.command()
def serve(
    port: int = typer.Option(7001, "--port", "-p", help="Server port"),
    host: str = typer.Option("0.0.0.0", "--host", help="Server host"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on changes")
):
    """Start FastAPI server with integrated worker."""
    import uvicorn
    uvicorn.run(
        "otomata_worker.server:app",
        host=host,
        port=port,
        reload=reload,
    )


# === Tasks ===

@task_app.command("create")
def task_create(
    task_type: str = typer.Option(..., "--type", "-t", help="Task type (script, agent)"),
    prompt: str = typer.Option(None, "--prompt", "-p", help="Agent prompt"),
    script: str = typer.Option(None, "--script", "-s", help="Script path"),
    workspace: str = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    params: str = typer.Option(None, "--params", help="JSON params")
):
    """Create a new task."""
    from .task_manager import TaskManager

    tm = TaskManager()
    parsed_params = json.loads(params) if params else None

    task_id = tm.create(
        task_type=task_type,
        prompt=prompt,
        script_path=script,
        workspace=workspace,
        params=parsed_params
    )

    console.print(f"[green]Created task {task_id}[/green]")


@task_app.command("list")
def task_list(
    status: str = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max tasks to show")
):
    """List tasks."""
    from .task_manager import TaskManager
    from .models import TaskStatus

    tm = TaskManager()
    filter_status = TaskStatus(status) if status else None
    tasks = tm.list_tasks(status=filter_status, limit=limit)

    table = Table(title="Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Worker")
    table.add_column("Created")

    for t in tasks:
        status_style = {
            'pending': 'yellow',
            'running': 'blue',
            'completed': 'green',
            'failed': 'red'
        }.get(t['status'], '')

        table.add_row(
            str(t['id']),
            t['task_type'] or '',
            f"[{status_style}]{t['status']}[/{status_style}]",
            t['claimed_by'] or '',
            t['created_at'][:19] if t['created_at'] else ''
        )

    console.print(table)


@task_app.command("status")
def task_status(task_id: int = typer.Argument(..., help="Task ID")):
    """Show task details."""
    from .task_manager import TaskManager

    tm = TaskManager()
    task = tm.get(task_id)

    if not task:
        console.print(f"[red]Task {task_id} not found[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Task {task.id}[/bold]")
    console.print(f"  Type: {task.task_type}")
    console.print(f"  Status: {task.status.value}")
    console.print(f"  Worker: {task.claimed_by or '-'}")
    console.print(f"  Created: {task.created_at}")
    console.print(f"  Started: {task.started_at or '-'}")
    console.print(f"  Completed: {task.completed_at or '-'}")

    if task.prompt:
        console.print(f"\n[bold]Prompt:[/bold]\n{task.prompt[:500]}")
    if task.script_path:
        console.print(f"\n[bold]Script:[/bold] {task.script_path}")
    if task.error:
        console.print(f"\n[bold red]Error:[/bold red]\n{task.error}")
    if task.result:
        console.print(f"\n[bold]Result:[/bold]\n{json.dumps(task.result, indent=2)[:1000]}")


@task_app.command("retry")
def task_retry(task_id: int = typer.Argument(..., help="Task ID")):
    """Retry a failed task."""
    from .task_manager import TaskManager

    tm = TaskManager()
    if tm.retry(task_id):
        console.print(f"[green]Task {task_id} reset to pending[/green]")
    else:
        console.print(f"[red]Cannot retry task {task_id} (not failed or not found)[/red]")


@task_app.command("cancel")
def task_cancel(task_id: int = typer.Argument(..., help="Task ID")):
    """Cancel a pending task."""
    from .task_manager import TaskManager

    tm = TaskManager()
    if tm.cancel(task_id):
        console.print(f"[green]Task {task_id} cancelled[/green]")
    else:
        console.print(f"[red]Cannot cancel task {task_id} (not pending or not found)[/red]")


# === Secrets ===

@secrets_app.command("list")
def secrets_list():
    """List secrets (without values)."""
    from .secrets import secrets_service

    secrets = secrets_service.list_keys()

    table = Table(title="Secrets")
    table.add_column("Key", style="cyan")
    table.add_column("Scope")
    table.add_column("Description")
    table.add_column("Updated")

    for s in secrets:
        table.add_row(
            s['key'],
            s['scope'],
            s['description'] or '',
            s['updated_at'][:19] if s['updated_at'] else ''
        )

    console.print(table)


@secrets_app.command("set")
def secrets_set(
    key: str = typer.Argument(..., help="Secret key"),
    value: str = typer.Argument(..., help="Secret value"),
    description: str = typer.Option(None, "--desc", "-d", help="Description")
):
    """Set a secret."""
    from .secrets import secrets_service

    secrets_service.set(key, value, description=description)
    console.print(f"[green]Secret '{key}' saved[/green]")


@secrets_app.command("get")
def secrets_get(key: str = typer.Argument(..., help="Secret key")):
    """Get a secret value."""
    from .secrets import secrets_service

    value = secrets_service.get(key)
    if value:
        console.print(value)
    else:
        console.print(f"[red]Secret '{key}' not found[/red]")
        raise typer.Exit(1)


@secrets_app.command("delete")
def secrets_delete(key: str = typer.Argument(..., help="Secret key")):
    """Delete a secret."""
    from .secrets import secrets_service

    if secrets_service.delete(key):
        console.print(f"[green]Secret '{key}' deleted[/green]")
    else:
        console.print(f"[red]Secret '{key}' not found[/red]")


# === Identities ===

@identities_app.command("list")
def identities_list(
    platform: str = typer.Option(None, "--platform", "-p", help="Filter by platform"),
    status: str = typer.Option(None, "--status", "-s", help="Filter by status")
):
    """List identities."""
    from .identities import IdentityManager

    im = IdentityManager()
    identities = im.list_all(platform=platform, status=status)

    table = Table(title="Identities")
    table.add_column("ID", style="cyan")
    table.add_column("Platform")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Last Used")

    for i in identities:
        status_style = {
            'active': 'green',
            'blocked': 'red',
            'warming': 'yellow'
        }.get(i['status'], '')

        table.add_row(
            str(i['id']),
            i['platform'],
            i['name'],
            i['account_type'] or '',
            f"[{status_style}]{i['status']}[/{status_style}]",
            i['last_used_at'][:19] if i['last_used_at'] else '-'
        )

    console.print(table)


@identities_app.command("add")
def identities_add(
    platform: str = typer.Argument(..., help="Platform (linkedin, kaspr)"),
    name: str = typer.Argument(..., help="Identity name"),
    cookie: str = typer.Option(None, "--cookie", "-c", help="Session cookie"),
    account_type: str = typer.Option("free", "--type", "-t", help="Account type"),
    user_agent: str = typer.Option(None, "--ua", help="User agent")
):
    """Add a new identity."""
    from .identities import IdentityManager

    im = IdentityManager()
    identity_id = im.create(
        platform=platform,
        name=name,
        cookie=cookie,
        account_type=account_type,
        user_agent=user_agent
    )

    console.print(f"[green]Identity {identity_id} created: {platform}/{name}[/green]")


@identities_app.command("status")
def identities_status(identity_id: int = typer.Argument(..., help="Identity ID")):
    """Show identity details and rate limit stats."""
    from .identities import IdentityManager
    from .rate_limiter import DBRateLimiter

    im = IdentityManager()
    identity = im.get_by_id(identity_id)

    if not identity:
        console.print(f"[red]Identity {identity_id} not found[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]{identity['platform']}/{identity['name']}[/bold]")
    console.print(f"  Status: {identity['status']}")
    console.print(f"  Type: {identity['account_type']}")
    console.print(f"  Last used: {identity['last_used_at'] or '-'}")

    if identity['blocked_at']:
        console.print(f"  [red]Blocked: {identity['blocked_at']}[/red]")
        console.print(f"  [red]Reason: {identity['blocked_reason']}[/red]")

    # Rate limits
    rl = DBRateLimiter()
    stats = rl.get_stats(identity_id)

    if stats:
        console.print("\n[bold]Rate Limits:[/bold]")
        for action, data in stats.items():
            console.print(
                f"  {action}: {data['hourly_used']}/{data['hourly_limit']} hourly, "
                f"{data['daily_used']}/{data['daily_limit']} daily"
            )


@identities_app.command("block")
def identities_block(
    identity_id: int = typer.Argument(..., help="Identity ID"),
    reason: str = typer.Option("Manual block", "--reason", "-r", help="Block reason")
):
    """Mark identity as blocked."""
    from .identities import IdentityManager

    im = IdentityManager()
    im.mark_blocked(identity_id, reason)
    console.print(f"[yellow]Identity {identity_id} marked as blocked[/yellow]")


@identities_app.command("unblock")
def identities_unblock(identity_id: int = typer.Argument(..., help="Identity ID")):
    """Unblock identity."""
    from .identities import IdentityManager

    im = IdentityManager()
    im.mark_active(identity_id)
    console.print(f"[green]Identity {identity_id} unblocked[/green]")


# === Database ===

@db_app.command("init")
def db_init():
    """Initialize database tables."""
    from .database import init_db

    init_db()
    console.print("[green]Database initialized[/green]")


@db_app.command("migrate")
def db_migrate():
    """Run migrations (add missing columns/tables)."""
    from .database import get_db_engine
    from .models import Base, Chat
    from sqlalchemy import inspect, text

    engine = get_db_engine()
    inspector = inspect(engine)
    expected_chat_cols = {'id', 'tenant', 'metadata', 'system_prompt', 'workspace', 'allowed_tools', 'max_turns', 'created_at', 'updated_at'}

    # Check if chats table exists with old schema (needs drop+recreate)
    if 'chats' in inspector.get_table_names():
        actual_cols = {c['name'] for c in inspector.get_columns('chats')}
        missing = expected_chat_cols - actual_cols
        if missing:
            console.print(f"[yellow]chats table missing columns: {missing} — recreating[/yellow]")
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS task_events CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS messages CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS chats CASCADE"))

    # Add chat_id to tasks if missing
    if 'tasks' in inspector.get_table_names():
        columns = [c['name'] for c in inspector.get_columns('tasks')]
        if 'chat_id' not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN chat_id INTEGER REFERENCES chats(id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_chat_id ON tasks(chat_id)"))
            console.print("[green]Added chat_id to tasks[/green]")

    # Create new/missing tables
    Base.metadata.create_all(engine)
    console.print("[green]Migration complete[/green]")


@db_app.command("url")
def db_url():
    """Show current DATABASE_URL."""
    url = os.environ.get('DATABASE_URL', '')
    if url:
        # Mask password
        if '@' in url:
            prefix, suffix = url.split('@', 1)
            if ':' in prefix:
                scheme_user = prefix.rsplit(':', 1)[0]
                url = f"{scheme_user}:***@{suffix}"
        console.print(url)
    else:
        console.print("[red]DATABASE_URL not set[/red]")


if __name__ == "__main__":
    app()
