"""Execute Claude agents using Claude Code SDK."""

import asyncio
from typing import Optional, Dict, Any

from ..models import Task


async def execute_agent(
    task: Task,
    secrets: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Execute a Claude agent with the SDK.

    Args:
        task: Task with prompt, workspace, and optional session_id
        secrets: Optional secrets to inject as environment variables

    Returns:
        Dict with session_id, success, and output
    """
    try:
        from claude_code_sdk import query, ClaudeCodeOptions
    except ImportError:
        return {
            'success': False,
            'error': 'claude-code-sdk not installed. Install with: pip install claude-code-sdk'
        }

    if not task.prompt:
        return {'success': False, 'error': 'No prompt provided'}

    import os

    # Inject secrets into environment
    original_env = {}
    if secrets:
        for key, value in secrets.items():
            original_env[key] = os.environ.get(key)
            os.environ[key] = value

    try:
        options = ClaudeCodeOptions(
            model="claude-sonnet-4-5-20250929",
            cwd=task.workspace or os.getcwd(),
            permission_mode="bypassPermissions",
        )

        # Add resume if session_id exists
        if task.session_id:
            options.resume = task.session_id

        session_id = None
        output_parts = []
        cost_usd = 0.0

        async for message in query(prompt=task.prompt, options=options):
            if hasattr(message, 'type'):
                if message.type == 'system' and hasattr(message, 'subtype'):
                    if message.subtype == 'init' and hasattr(message, 'session_id'):
                        session_id = message.session_id
                elif message.type == 'result':
                    if hasattr(message, 'cost_usd'):
                        cost_usd = message.cost_usd
                elif message.type == 'assistant':
                    if hasattr(message, 'content'):
                        # Extract text from content
                        if isinstance(message.content, list):
                            for block in message.content:
                                if hasattr(block, 'text'):
                                    output_parts.append(block.text)
                        elif isinstance(message.content, str):
                            output_parts.append(message.content)

        return {
            'success': True,
            'session_id': session_id,
            'output': '\n'.join(output_parts),
            'cost_usd': cost_usd
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
    finally:
        # Restore original environment
        if secrets:
            for key, original_value in original_env.items():
                if original_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original_value


def run_agent(task: Task, secrets: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Synchronous wrapper for execute_agent."""
    return asyncio.run(execute_agent(task, secrets))
