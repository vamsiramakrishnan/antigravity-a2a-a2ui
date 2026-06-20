"""Import-guarded realizer: turn :class:`TriggerSpec` objects into real
``google.antigravity.triggers`` registrations.

Each spec names a handler key; ``handlers[name]`` supplies the callback. Pure
spec construction lives in :mod:`a2a_workspace.antigravity.hardening`.
"""

from __future__ import annotations

from collections.abc import Callable

from a2a_workspace.antigravity.hardening import TriggerSpec


def build_triggers(
    specs: list[TriggerSpec], handlers: dict[str, Callable]
) -> list:
    """Map ``specs`` to ``triggers.every`` / ``triggers.on_file_change`` objects.

    ``handlers`` maps each spec's ``name`` to its callback. Raises ``KeyError``
    for a spec with no matching handler and ``ValueError`` for a spec missing the
    field its kind requires. Raises ``ImportError`` if the SDK is missing.
    """
    try:
        from google.antigravity import triggers  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional SDK
        raise ImportError(
            "build_triggers requires the Antigravity SDK: pip install "
            "google-antigravity"
        ) from exc

    built: list = []
    for spec in specs:
        callback = handlers[spec.name]
        if spec.kind == "every":
            if spec.interval_seconds is None:
                raise ValueError(
                    f"trigger {spec.name!r} of kind 'every' needs interval_seconds"
                )
            built.append(triggers.every(spec.interval_seconds, callback))
        elif spec.kind == "on_file_change":
            if spec.path is None:
                raise ValueError(
                    f"trigger {spec.name!r} of kind 'on_file_change' needs path"
                )
            built.append(triggers.on_file_change(spec.path, callback))
        else:  # pragma: no cover - guarded by Literal type
            raise ValueError(f"unknown trigger kind: {spec.kind!r}")
    return built
