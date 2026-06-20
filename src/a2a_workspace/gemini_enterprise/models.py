"""Result types for the Discovery Engine integration.

Small, transport-independent value objects. The client parses the (streaming)
Discovery Engine responses into these so the rest of the system — and the
Antigravity proxy tools — never touch raw API JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Citation:
    """A grounding source backing part of an answer."""

    title: str
    uri: str = ""
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class AssistResult:
    """The aggregated outcome of an assist / agent invocation."""

    answer: str
    citations: tuple[Citation, ...] = ()
    session: str = ""
    state: str = ""

    def as_text(self) -> str:
        """Render a compact, model-friendly string (answer + numbered sources)."""
        if not self.citations:
            return self.answer
        lines = [self.answer, "", "Sources:"]
        for i, c in enumerate(self.citations, 1):
            ref = c.uri or c.title
            lines.append(f"[{i}] {c.title} — {ref}" if c.title else f"[{i}] {ref}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class AgentInfo:
    """A registered Gemini Enterprise agent that can be invoked."""

    name: str  # full resource name
    display_name: str = ""
    description: str = ""

    @property
    def agent_id(self) -> str:
        return self.name.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class DataStoreInfo:
    """A connector-backed data store available to the assistant."""

    name: str
    display_name: str = ""
    industry_vertical: str = ""
    solution_types: tuple[str, ...] = field(default_factory=tuple)

    @property
    def data_store_id(self) -> str:
        return self.name.rsplit("/", 1)[-1]
