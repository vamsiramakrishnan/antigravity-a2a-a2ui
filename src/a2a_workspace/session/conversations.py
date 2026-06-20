"""Conversation records, pinned to a single skill generation.

The pinning invariant lives here: a :class:`Conversation` stores the generation
number it was created on and never changes it. ``ConversationStore.create``
records that pin; nothing in the API can re-point an existing conversation at a
newer generation. Re-materializing a conversation always uses its recorded
generation, even if the workspace has since activated a newer one.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from a2a_workspace.errors import NotFoundError
from a2a_workspace.identity.principal import Principal


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str
    principal_key: str
    workspace_id: str
    generation: int
    content_digest: str
    created_at_epoch: float = field(default_factory=time.time)


class ConversationStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, Conversation] = {}

    def create(
        self,
        *,
        principal: Principal,
        workspace_id: str,
        generation: int,
        content_digest: str,
    ) -> Conversation:
        conv = Conversation(
            conversation_id=f"conv_{uuid.uuid4().hex[:16]}",
            principal_key=principal.key,
            workspace_id=workspace_id,
            generation=generation,
            content_digest=content_digest,
        )
        with self._lock:
            self._items[conv.conversation_id] = conv
        return conv

    def get(self, conversation_id: str, *, principal: Principal) -> Conversation:
        """Fetch a conversation, enforcing that the caller owns it.

        Ownership is checked against the verified principal key, so one user can
        never resume another user's conversation by guessing an id.
        """
        with self._lock:
            conv = self._items.get(conversation_id)
        if conv is None:
            raise NotFoundError(f"conversation not found: {conversation_id}")
        if conv.principal_key != principal.key:
            from a2a_workspace.errors import IsolationError

            raise IsolationError("conversation belongs to a different principal")
        return conv
