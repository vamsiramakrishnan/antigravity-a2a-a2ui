"""Pure decision logic for the Antigravity advanced-capabilities layer.

This module deliberately imports NO SDK. It holds the *decisions* — what to
deny, what to ask about, which commands look like credential exfiltration — so
that every security-relevant rule can be unit-tested without ``google-antigravity``
installed. The import-guarded *realizers* (``policies.py``, ``hooks.py``,
``triggers.py``, ``agent_config.py``) consume the specs produced here.

The centerpiece is :func:`is_credential_exfil`, which flags shell commands that
try to read ambient cloud credentials/tokens. Agents running with any kind of
metadata-server access or gcloud login are one ``RUN_COMMAND`` away from minting
an access token and walking off with it; this is the choke point that refuses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Credential-exfiltration detection
# ---------------------------------------------------------------------------

# Each entry is a case-insensitive regex matched against a whitespace-normalized
# copy of the command. Patterns are intentionally broad (substring/token style)
# because an attacker controls quoting and spacing; we would rather over-deny a
# RUN_COMMAND than leak a token. Comments document the threat each one covers.
EXFIL_PATTERNS: tuple[str, ...] = (
    # gcloud minting a raw access token: `gcloud auth print-access-token`,
    # `gcloud auth application-default print-access-token`, etc.
    r"gcloud\s+auth\b[^\n]*\bprint-access-token\b",
    # any gcloud auth subcommand that prints/produces a token
    r"gcloud\s+auth\b[^\n]*\b(print-identity-token|token)\b",
    # gcloud config surface that can echo an access token
    r"gcloud\s+config\b[^\n]*\bprint-access",
    # GCE/GKE metadata server by hostname (token + service-account endpoints)
    r"metadata\.google\.internal",
    # metadata server by its well-known link-local IP
    r"169\.254\.169\.254",
    # the computeMetadata HTTP path (works regardless of host/IP spelling)
    r"/computeMetadata/",
    # OAuth2 tokeninfo / token endpoints used to validate or exchange tokens
    r"oauth2[^\s]*/tokeninfo",
    r"oauth2[^\s]*/token\b",
    # reading Application Default Credentials off disk
    r"application_default_credentials\.json",
    # dereferencing the ADC/service-account key env var
    r"\$\{?GOOGLE_APPLICATION_CREDENTIALS\}?",
    # AWS instance metadata path (defense in depth for mixed clouds)
    r"/latest/meta-data/",
)

_COMPILED_EXFIL = tuple(re.compile(p, re.IGNORECASE) for p in EXFIL_PATTERNS)

# A curl/wget reaching the metadata IP/host is covered above, but the bare
# combination of a fetch tool + "Metadata-Flavor" header is a strong signal too.
_METADATA_HEADER = re.compile(r"Metadata-Flavor\s*:\s*Google", re.IGNORECASE)


def is_credential_exfil(command: str) -> bool:
    """Return ``True`` if ``command`` looks like an attempt to read ambient
    cloud credentials or tokens.

    Robust to quoting and spacing: the command is whitespace-normalized and
    matched case-insensitively against :data:`EXFIL_PATTERNS`. This is a
    deny-side heuristic — false positives merely require the user to approve a
    command interactively; false negatives leak a token.
    """
    if not command:
        return False
    # Normalize runs of whitespace (incl. newlines/tabs) to single spaces so
    # that `gcloud   auth\n print-access-token` still matches.
    normalized = re.sub(r"\s+", " ", command)
    for pattern in _COMPILED_EXFIL:
        if pattern.search(normalized):
            return True
    if _METADATA_HEADER.search(normalized):
        return True
    return False


# ---------------------------------------------------------------------------
# SDK-free intent specs
# ---------------------------------------------------------------------------

Decision = Literal["APPROVE", "DENY", "ASK_USER"]


@dataclass(frozen=True, slots=True)
class PolicySpec:
    """A single tool policy, described without the SDK.

    ``when_exfil`` marks a policy whose ``when=`` predicate is the credential
    exfiltration check (only meaningful for run-command DENY rules).
    """

    tool: str
    decision: Decision
    when_exfil: bool = False
    name: str = ""


@dataclass(frozen=True, slots=True)
class HookSpec:
    """A hook to install, described without the SDK."""

    event: str
    kind: Literal["inspect", "decide", "transform"]
    name: str


@dataclass(frozen=True, slots=True)
class TriggerSpec:
    """A trigger to install, described without the SDK."""

    kind: Literal["every", "on_file_change"]
    interval_seconds: float | None = None
    path: str | None = None
    name: str = ""


@dataclass(frozen=True, slots=True)
class CapabilitiesSpec:
    """Capabilities flags, described without the SDK."""

    enable_subagents: bool = False
    disabled_tools: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] | None = None
    compaction_threshold: int | None = None


# ---------------------------------------------------------------------------
# Pure spec builders
# ---------------------------------------------------------------------------

# Canonical name of the shell tool in the Antigravity BuiltinTools enum.
RUN_COMMAND = "RUN_COMMAND"


def isolation_policy_specs(
    *, work_dir: str, allow_run_command: bool
) -> list[PolicySpec]:
    """Produce the policy specs that confine an agent to ``work_dir`` and refuse
    credential exfiltration.

    The resulting list always contains, in priority order:

    1. A ``DENY`` of ``RUN_COMMAND`` *when* the proposed command matches the
       credential-exfil heuristic. This is unconditional — even agents allowed
       to run commands may never mint/steal a token.
    2. Either a blanket ``DENY`` of ``RUN_COMMAND`` (when ``allow_run_command``
       is ``False``) or nothing further for the shell.

    Workspace confinement itself is realized in ``policies.py`` via
    ``policy.workspace_only([work_dir])``; ``work_dir`` is threaded through here
    so callers carry a single spec object.
    """
    specs: list[PolicySpec] = [
        PolicySpec(
            tool=RUN_COMMAND,
            decision="DENY",
            when_exfil=True,
            name=f"deny-credential-exfil[{work_dir}]",
        )
    ]
    if not allow_run_command:
        specs.append(
            PolicySpec(
                tool=RUN_COMMAND,
                decision="DENY",
                name="deny-run-command",
            )
        )
    return specs


def default_capabilities_spec(*, skill_only: bool) -> CapabilitiesSpec:
    """Sensible default capabilities.

    A *skill-only* agent runs published skills and must not shell out, so
    ``RUN_COMMAND`` is disabled and subagents stay off. The general case enables
    subagents and leaves the shell available (still guarded by the exfil DENY).
    """
    if skill_only:
        return CapabilitiesSpec(
            enable_subagents=False,
            disabled_tools=(RUN_COMMAND,),
            compaction_threshold=None,
        )
    return CapabilitiesSpec(
        enable_subagents=True,
        disabled_tools=(),
        compaction_threshold=None,
    )
