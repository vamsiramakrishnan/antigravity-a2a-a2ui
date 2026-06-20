"""The Antigravity local connection configuration.

Mirrors the Antigravity SDK's local connection surface: a set of read-only
``skills_paths`` and a writable ``app_data_dir``. We build it from a materialized
session.

A hard rule, enforced by :meth:`LocalConnectionStrategy.assert_no_credentials`
and by tests: **no storage credential ever appears in this object**. Everything
here is visible, in principle, to the model and the Antigravity runtime; a token
placed here is a token that can be exfiltrated. Storage credentials live only in
the per-request ``StorageAdapter`` and are gone before the conversation starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from a2a_workspace.identity.authorization import ToolCredential


@dataclass(frozen=True, slots=True)
class LocalConnectionStrategy:
    """Shape consumed by the Antigravity SDK's local connection."""

    skills_paths: tuple[str, ...]
    app_data_dir: str
    read_only_skills: bool = True
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.assert_no_credentials()

    def assert_no_credentials(self) -> None:
        """Fail loudly if anything credential-shaped leaked into the config.

        This is a structural guard, not a content scanner: ``env`` must not carry
        a raw ``ToolCredential`` or obvious token keys. The real protection is
        that the lifecycle never passes a credential here in the first place;
        this catches a future refactor that forgets.
        """
        for key, value in self.env.items():
            if isinstance(value, ToolCredential):  # type: ignore[unreachable]
                raise ValueError("ToolCredential must never be placed in connection env")
            lowered = key.lower()
            if any(tok in lowered for tok in ("token", "secret", "credential", "bearer")):
                raise ValueError(
                    f"connection env key {key!r} looks like a credential; the "
                    "Antigravity runtime must never receive storage credentials"
                )

    @classmethod
    def for_session(
        cls,
        *,
        skills_dir: Path | str,
        app_data_dir: Path | str,
        extra_skills_paths: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
    ) -> "LocalConnectionStrategy":
        paths = (str(skills_dir), *extra_skills_paths)
        return cls(
            skills_paths=paths,
            app_data_dir=str(app_data_dir),
            env=dict(env or {}),
        )
