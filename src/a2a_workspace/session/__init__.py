"""Antigravity session lifecycle.

Ties the pieces together: verify principal -> ensure workspace -> resolve active
generation -> materialize -> start a conversation pinned to that generation. The
conversation is pinned for life; activating a newer revision only affects *new*
conversations.
"""

from a2a_workspace.session.connection import LocalConnectionStrategy
from a2a_workspace.session.conversations import (
    Conversation,
    ConversationStore,
)
from a2a_workspace.session.lifecycle import SessionLifecycle

__all__ = [
    "Conversation",
    "ConversationStore",
    "LocalConnectionStrategy",
    "SessionLifecycle",
]
