"""Execute Claude agents using Claude Agent SDK."""

import asyncio
import os
from typing import Optional, Dict, Any, List

from ..models import Task
from ..events import event_store


async def execute_agent(
    task: Task,
    secrets: Optional[Dict[str, str]] = None,
    history: Optional[List[dict]] = None,
    system_prompt: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    max_turns: int = 50,
    env: Optional[Dict[str, str]] = None,
    emit_events: bool = False,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a Claude agent with the Agent SDK.

    Args:
        task: Task with prompt, workspace
        secrets: Secrets to inject as environment variables
        history: Conversation history [{"role": "user"|"assistant", "content": str}]
        system_prompt: System prompt for the agent
        allowed_tools: List of allowed tool names
        max_turns: Max agent turns
        env: Extra environment variables for agent
        emit_events: Emit events to event_store for SSE streaming
        model: Model override

    Returns:
        Dict with success, output, tokens, session_id
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage, ToolUseBlock
    except ImportError:
        return {
            'success': False,
            'error': 'claude-agent-sdk not installed. Install with: pip install otomata-worker[agent]'
        }

    if not task.prompt:
        return {'success': False, 'error': 'No prompt provided'}

    os.environ['CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK'] = '1'

    # Inject secrets into environment
    original_env = {}
    if secrets:
        for key, value in secrets.items():
            original_env[key] = os.environ.get(key)
            os.environ[key] = value

    try:
        # Build full prompt with history context
        full_prompt = task.prompt
        if history:
            context_parts = []
            for msg in history:
                role = "User" if msg["role"] == "user" else "Assistant"
                context_parts.append(f"{role}: {msg['content']}")
            conversation_context = "\n\n".join(context_parts)
            full_prompt = f"Previous conversation:\n\n{conversation_context}\n\nUser's new message: {task.prompt}"

        # Build agent env
        agent_env = dict(env or {})

        agent_model = model or os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')

        options = ClaudeAgentOptions(
            model=agent_model,
            system_prompt=system_prompt or "",
            cwd=task.workspace or os.getcwd(),
            allowed_tools=allowed_tools or [],
            permission_mode='acceptEdits',
            max_turns=max_turns,
            env=agent_env,
        )

        task_id = task.id
        print(f"[task-{task_id}] Starting agent model={agent_model}", flush=True)

        if emit_events:
            event_store.add_event(task_id, 'start', {'model': agent_model})

        response_text = ""
        input_tokens = 0
        output_tokens = 0
        tool_count = 0
        turn_count = 0

        async for message in query(prompt=full_prompt, options=options):
            if isinstance(message, ResultMessage):
                if hasattr(message, 'usage') and message.usage:
                    input_tokens = message.usage.get('input_tokens', 0)
                    output_tokens = message.usage.get('output_tokens', 0)
                continue

            if isinstance(message, AssistantMessage):
                turn_count += 1
                has_text = False
                tools_used = []

                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
                        has_text = True

                        # Emit text event for streaming
                        if emit_events:
                            event_store.add_event(task_id, 'text', {
                                'content': block.text,
                                'turn': turn_count,
                            })

                    elif isinstance(block, ToolUseBlock):
                        tool_count += 1
                        tool_name = block.name
                        tool_input = getattr(block, 'input', {})
                        tools_used.append({'name': tool_name, 'input': tool_input})

                        print(f"[task-{task_id}] Tool #{tool_count}: {tool_name}", flush=True)

                        if emit_events:
                            event_store.add_event(task_id, 'tool_use', {
                                'tool': tool_name,
                                'count': tool_count,
                                'input': tool_input,
                            })

                # Emit thinking event if text without tools
                if has_text and not tools_used and emit_events:
                    event_store.add_event(task_id, 'thinking', {'turn': turn_count})

        print(f"[task-{task_id}] Completed: {tool_count} tools, {input_tokens} in / {output_tokens} out", flush=True)

        if emit_events:
            event_store.add_event(task_id, 'complete', {
                'tool_count': tool_count,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
            })

        return {
            'success': True,
            'output': response_text,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'tool_count': tool_count,
        }

    except Exception as e:
        if emit_events:
            event_store.add_event(task.id, 'error', {'error': str(e)})
        return {'success': False, 'error': str(e)}

    finally:
        # Restore original environment
        if secrets:
            for key, original_value in original_env.items():
                if original_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original_value


def run_agent(task: Task, secrets: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
    """Synchronous wrapper for execute_agent."""
    return asyncio.run(execute_agent(task, secrets, **kwargs))
