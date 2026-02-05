"""Chat management - create chats, add messages, get history."""

from datetime import datetime
from typing import Optional

from .models import Chat, Message, MessageRole, Task, TaskEvent
from .database import get_session


class ChatManager:
    """Manage chat lifecycle and messages."""

    def create_chat(
        self,
        tenant: str,
        system_prompt: str,
        workspace: Optional[str] = None,
        allowed_tools: Optional[list] = None,
        max_turns: int = 50,
        metadata: Optional[dict] = None
    ) -> int:
        """Create a new chat session.

        Returns:
            Created Chat ID
        """
        with get_session() as session:
            chat = Chat(
                tenant=tenant,
                system_prompt=system_prompt,
                workspace=workspace,
                allowed_tools=allowed_tools or [],
                max_turns=max_turns,
                metadata_=metadata,
            )
            session.add(chat)
            session.flush()
            return chat.id

    def list_chats(self, tenant: str = None, metadata_filter: dict = None) -> list[dict]:
        """List chats, optionally filtered by tenant and metadata keys."""
        with get_session() as session:
            q = session.query(Chat).order_by(Chat.created_at.desc())
            if tenant:
                q = q.filter(Chat.tenant == tenant)
            chats = q.all()

            # Filter by metadata keys (in-memory, good enough for reasonable volumes)
            if metadata_filter:
                filtered = []
                for c in chats:
                    meta = c.metadata_ or {}
                    if all(str(meta.get(k)) == str(v) for k, v in metadata_filter.items()):
                        filtered.append(c)
                chats = filtered

            return [
                {
                    'id': c.id,
                    'tenant': c.tenant,
                    'metadata': c.metadata_,
                    'created_at': c.created_at.isoformat() if c.created_at else None,
                    'updated_at': c.updated_at.isoformat() if c.updated_at else None,
                }
                for c in chats
            ]

    def get_chat(self, chat_id: int) -> Optional[dict]:
        """Get chat by ID (without messages)."""
        with get_session() as session:
            chat = session.query(Chat).get(chat_id)
            if not chat:
                return None
            return {
                'id': chat.id,
                'tenant': chat.tenant,
                'system_prompt': chat.system_prompt,
                'workspace': chat.workspace,
                'allowed_tools': chat.allowed_tools,
                'max_turns': chat.max_turns,
                'metadata': chat.metadata_,
                'created_at': chat.created_at.isoformat() if chat.created_at else None,
                'updated_at': chat.updated_at.isoformat() if chat.updated_at else None,
            }

    def get_chat_with_messages(self, chat_id: int) -> Optional[dict]:
        """Get chat with all messages."""
        with get_session() as session:
            chat = session.query(Chat).get(chat_id)
            if not chat:
                return None
            return {
                'id': chat.id,
                'tenant': chat.tenant,
                'system_prompt': chat.system_prompt,
                'workspace': chat.workspace,
                'allowed_tools': chat.allowed_tools,
                'max_turns': chat.max_turns,
                'metadata': chat.metadata_,
                'created_at': chat.created_at.isoformat() if chat.created_at else None,
                'updated_at': chat.updated_at.isoformat() if chat.updated_at else None,
                'messages': [
                    {
                        'id': m.id,
                        'role': m.role.value,
                        'content': m.content,
                        'sequence': m.sequence,
                        'tokens_input': m.tokens_input,
                        'tokens_output': m.tokens_output,
                        'created_at': m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in chat.messages
                ],
            }

    def list_messages(self, chat_id: int, include_tools: bool = False) -> list[dict]:
        """List messages for a chat, optionally with tool_use events interleaved."""
        with get_session() as session:
            messages = session.query(Message).filter(
                Message.chat_id == chat_id
            ).order_by(Message.sequence).all()

            result = [
                {
                    'id': m.id,
                    'role': m.role.value,
                    'content': m.content,
                    'sequence': m.sequence,
                    'tokens_input': m.tokens_input,
                    'tokens_output': m.tokens_output,
                    'created_at': m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
            ]

            if not include_tools:
                return result

            # Fetch text + tool_use events per task, use them instead of
            # the single concatenated assistant Message record.
            from collections import defaultdict

            task_events = session.query(TaskEvent).join(Task).filter(
                Task.chat_id == chat_id,
                TaskEvent.event_type.in_(['text', 'tool_use']),
            ).order_by(TaskEvent.created_at).all()

            # Build per-task event lists (chronological)
            events_by_task = defaultdict(list)
            for te in task_events:
                data = te.event_data or {}
                if te.event_type == 'tool_use':
                    detail = data.get('tool', 'tool')
                    inp = data.get('input', {})
                    if detail == 'Bash' and isinstance(inp, dict) and inp.get('command'):
                        cmd = inp['command']
                        detail = f"Bash: {cmd[:80]}..." if len(cmd) > 80 else f"Bash: {cmd}"
                    elif detail in ('Read', 'Write', 'Edit') and isinstance(inp, dict) and inp.get('file_path'):
                        detail = f"{detail}: {inp['file_path']}"
                    elif detail in ('Glob', 'Grep') and isinstance(inp, dict) and inp.get('pattern'):
                        detail = f"{detail}: {inp['pattern']}"
                    events_by_task[te.task_id].append({
                        'role': 'tool_use',
                        'content': detail,
                        'created_at': te.created_at.isoformat() if te.created_at else None,
                    })
                else:  # text
                    events_by_task[te.task_id].append({
                        'role': 'assistant',
                        'content': data.get('content', ''),
                        'created_at': te.created_at.isoformat() if te.created_at else None,
                    })

            # Match tasks to user messages by creation order
            tasks = session.query(Task).filter(
                Task.chat_id == chat_id
            ).order_by(Task.created_at).all()

            user_msg_indices = [i for i, m in enumerate(result) if m['role'] == 'user']

            # Replace concatenated assistant messages with per-turn events
            # Build: user_msg → text1 → tool1 → tool2 → text2 → ...
            task_for_user = {}
            for task_idx, task in enumerate(tasks):
                if task_idx < len(user_msg_indices):
                    task_for_user[user_msg_indices[task_idx]] = task.id

            # Find assistant messages to skip (replaced by per-turn text events)
            skip_indices = set()
            for user_idx, task_id in task_for_user.items():
                if events_by_task.get(task_id):
                    for j in range(user_idx + 1, len(result)):
                        if result[j]['role'] == 'assistant':
                            skip_indices.add(j)
                            break

            final = []
            for i, msg in enumerate(result):
                if i in skip_indices:
                    continue
                final.append(msg)
                if i in task_for_user:
                    final.extend(events_by_task.get(task_for_user[i], []))

            return final

    def add_message(
        self,
        chat_id: int,
        role: str,
        content: str,
        tokens_input: int = 0,
        tokens_output: int = 0
    ) -> int:
        """Add a message to a chat.

        Returns:
            Created Message ID
        """
        with get_session() as session:
            # Get next sequence
            from sqlalchemy import func
            max_seq = session.query(func.max(Message.sequence)).filter(
                Message.chat_id == chat_id
            ).scalar() or 0

            msg = Message(
                chat_id=chat_id,
                role=MessageRole(role),
                content=content,
                sequence=max_seq + 1,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
            )
            session.add(msg)
            session.flush()
            return msg.id

    def get_history(self, chat_id: int) -> list[dict]:
        """Get message history as role+content pairs (for agent context)."""
        with get_session() as session:
            messages = session.query(Message).filter(
                Message.chat_id == chat_id
            ).order_by(Message.sequence).all()
            return [
                {"role": m.role.value, "content": m.content}
                for m in messages
            ]

    def update_chat(self, chat_id: int, **kwargs) -> bool:
        """Update chat fields.

        Accepted kwargs: system_prompt, workspace, allowed_tools, max_turns, metadata
        """
        allowed = {'system_prompt', 'workspace', 'allowed_tools', 'max_turns', 'metadata'}
        with get_session() as session:
            chat = session.query(Chat).get(chat_id)
            if not chat:
                return False
            for key, value in kwargs.items():
                if key not in allowed:
                    continue
                if key == 'metadata':
                    chat.metadata_ = value
                else:
                    setattr(chat, key, value)
            return True
