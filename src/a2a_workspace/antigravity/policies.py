"""Import-guarded realizer: turn :class:`PolicySpec` objects into real
``google.antigravity.policy`` objects.

Pure decisions live in :mod:`a2a_workspace.antigravity.hardening`; this module is
the thin SDK adapter. It raises a clear ``ImportError`` (matching
``config_builder``) when the SDK is absent rather than degrading silently.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

from a2a_workspace.antigravity.hardening import PolicySpec, is_credential_exfil


def _exfil_when_predicate(default: Callable[[str], bool]):
    """Build a ``when=`` predicate that returns ``True`` (rule applies) when the
    proposed command looks like credential exfiltration.

    The SDK's predicate signature is inspected defensively at call time: depending
    on version it may pass a context object, keyword args, or the raw tool input.
    We dig the command string out of whatever we are handed and fall back to a
    permissive ``False`` (rule does not apply) if we genuinely cannot find one.
    """

    def predicate(*args, **kwargs) -> bool:
        command = _extract_command(args, kwargs)
        if command is None:
            return False
        return default(command)

    return predicate


def _extract_command(args: tuple, kwargs: dict) -> str | None:
    """Best-effort extraction of the shell command from a hook/policy callback's
    arguments, tolerant of the several shapes the SDK may use."""
    # Explicit keyword wins.
    for key in ("command", "cmd"):
        val = kwargs.get(key)
        if isinstance(val, str):
            return val
    # Scan positional args and any dict-like tool input.
    candidates = list(args) + list(kwargs.values())
    for cand in candidates:
        if isinstance(cand, str):
            # Heuristic: a bare string positional is the command.
            return cand
        params = _maybe_params(cand)
        if params is not None:
            for key in ("command", "cmd"):
                val = params.get(key)
                if isinstance(val, str):
                    return val
    return None


def _maybe_params(obj) -> dict | None:
    """Return a parameter dict from a context-like object, if discoverable."""
    if isinstance(obj, dict):
        return obj
    for attr in ("params", "tool_input", "arguments", "input"):
        val = getattr(obj, attr, None)
        if isinstance(val, dict):
            return val
    return None


def build_policies(
    specs: list[PolicySpec],
    *,
    work_dir: str,
    exfil_predicate: Callable[[str], bool] | None = None,
) -> list:
    """Map ``specs`` to real ``policy.*`` objects, prepended with workspace
    confinement to ``work_dir``.

    ``exfil_predicate`` defaults to :func:`hardening.is_credential_exfil` and is
    wrapped into an SDK-shaped ``when=`` predicate for any spec with
    ``when_exfil=True``.
    """
    try:
        from google.antigravity import policy  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional SDK
        raise ImportError(
            "build_policies requires the Antigravity SDK: pip install "
            "google-antigravity"
        ) from exc

    predicate_fn = exfil_predicate or is_credential_exfil
    when_exfil = _exfil_when_predicate(predicate_fn)

    policies: list = []
    # Workspace confinement first: everything the agent touches stays under
    # work_dir. workspace_only returns a list of Policy objects.
    policies.extend(policy.workspace_only([work_dir]))

    decision_enum = getattr(policy, "Decision", None)

    for spec in specs:
        if spec.decision == "DENY":
            kwargs = {"name": spec.name} if spec.name else {}
            if spec.when_exfil:
                kwargs["when"] = when_exfil
            policies.append(policy.deny(spec.tool, **kwargs))
        elif spec.decision == "APPROVE":
            policies.append(policy.allow(spec.tool))
        elif spec.decision == "ASK_USER":
            # ask_user requires a handler; without one we fall back to a
            # constructed Policy if the enum is available, else deny defensively.
            if decision_enum is not None:
                policies.append(
                    policy.Policy(
                        spec.tool,
                        decision_enum.ASK_USER,
                        name=spec.name,
                    )
                )
            else:  # pragma: no cover - SDK shape fallback
                policies.append(policy.deny(spec.tool, name=spec.name))
    return policies
