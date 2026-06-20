"""Unit tests for the pure logic of the advanced-capabilities layer.

No SDK import at module level — these run without ``google-antigravity``.
"""

from __future__ import annotations

import pytest

from a2a_workspace.antigravity.agent_config import AdvancedAgentConfigSpec
from a2a_workspace.antigravity.hardening import (
    EXFIL_PATTERNS,
    CapabilitiesSpec,
    PolicySpec,
    default_capabilities_spec,
    is_credential_exfil,
    isolation_policy_specs,
)
from a2a_workspace.antigravity.hooks import redact_secrets


# ---------------------------------------------------------------------------
# is_credential_exfil
# ---------------------------------------------------------------------------

EXFIL_POSITIVES = [
    "gcloud auth print-access-token",
    "gcloud   auth   print-access-token",  # extra spacing
    "GCLOUD AUTH PRINT-ACCESS-TOKEN",  # case-insensitive
    "gcloud auth application-default print-access-token",
    "gcloud auth print-identity-token",
    "gcloud config config-helper --format='value(credential.access_token)' && gcloud config print-access",
    "curl -s http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token -H 'Metadata-Flavor: Google'",
    "wget -qO- http://169.254.169.254/computeMetadata/v1/",
    "curl http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "curl https://oauth2.googleapis.com/tokeninfo?access_token=foo",
    "curl https://oauth2.googleapis.com/token -d grant_type=...",
    "cat ~/.config/gcloud/application_default_credentials.json",
    "cat $GOOGLE_APPLICATION_CREDENTIALS",
    'cat "${GOOGLE_APPLICATION_CREDENTIALS}"',
]

EXFIL_NEGATIVES = [
    "ls",
    "ls -la /tmp",
    "python script.py",
    "git status",
    "echo hello world",
    "gcloud projects list",
    "gcloud compute instances list",
    "pip install requests",
    "curl https://example.com/data.json",
    "",
]


@pytest.mark.parametrize("cmd", EXFIL_POSITIVES)
def test_is_credential_exfil_positive(cmd):
    assert is_credential_exfil(cmd) is True


@pytest.mark.parametrize("cmd", EXFIL_NEGATIVES)
def test_is_credential_exfil_negative(cmd):
    assert is_credential_exfil(cmd) is False


def test_exfil_patterns_exported():
    assert isinstance(EXFIL_PATTERNS, tuple)
    assert len(EXFIL_PATTERNS) > 5
    assert all(isinstance(p, str) for p in EXFIL_PATTERNS)


# ---------------------------------------------------------------------------
# isolation_policy_specs
# ---------------------------------------------------------------------------


def test_isolation_specs_skill_only_denies_run_command():
    specs = isolation_policy_specs(work_dir="/work", allow_run_command=False)
    assert len(specs) == 2
    # First is always the exfil DENY.
    exfil = specs[0]
    assert exfil.tool == "RUN_COMMAND"
    assert exfil.decision == "DENY"
    assert exfil.when_exfil is True
    assert "/work" in exfil.name
    # Second is the blanket shell DENY.
    blanket = specs[1]
    assert blanket.decision == "DENY"
    assert blanket.when_exfil is False


def test_isolation_specs_allowed_run_command_only_has_exfil_deny():
    specs = isolation_policy_specs(work_dir="/work", allow_run_command=True)
    assert len(specs) == 1
    assert specs[0].when_exfil is True
    assert specs[0].decision == "DENY"


# ---------------------------------------------------------------------------
# default_capabilities_spec
# ---------------------------------------------------------------------------


def test_default_capabilities_skill_only():
    cap = default_capabilities_spec(skill_only=True)
    assert isinstance(cap, CapabilitiesSpec)
    assert cap.enable_subagents is False
    assert "RUN_COMMAND" in cap.disabled_tools


def test_default_capabilities_general():
    cap = default_capabilities_spec(skill_only=False)
    assert cap.enable_subagents is True
    assert cap.disabled_tools == ()


# ---------------------------------------------------------------------------
# redact_secrets
# ---------------------------------------------------------------------------


def test_redact_bearer():
    out = redact_secrets("Authorization: Bearer abc123.def-456")
    assert "abc123" not in out
    assert "[REDACTED]" in out


def test_redact_ya29():
    out = redact_secrets("token=ya29.A0ARrdaM-secretvalue123")
    assert "secretvalue123" not in out
    assert "[REDACTED_ACCESS_TOKEN]" in out


def test_redact_api_key():
    out = redact_secrets("key=AIzaSyD-1234567890abcdefg")
    assert "AIzaSyD" not in out
    assert "[REDACTED_API_KEY]" in out


def test_redact_private_key():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEverysecret\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact_secrets(f"here is a key {pem} done")
    assert "MIIEverysecret" not in out
    assert "[REDACTED_PRIVATE_KEY]" in out


def test_redact_noop_on_clean_text():
    clean = "this is a normal log line with no secrets"
    assert redact_secrets(clean) == clean


# ---------------------------------------------------------------------------
# AdvancedAgentConfigSpec.to_remote_agent_tools
# ---------------------------------------------------------------------------


def test_remote_tools_shell_enabled():
    spec = AdvancedAgentConfigSpec(
        work_dir="/work",
        mcp_http_servers=(("conn", "https://mcp.example/api", {"X-Auth": "t"}),),
        capabilities_spec=CapabilitiesSpec(disabled_tools=()),
    )
    tools = spec.to_remote_agent_tools()
    types = [t["type"] for t in tools]
    assert "filesystem" in types
    assert "code_execution" in types
    assert "mcp_server" in types
    fs = next(t for t in tools if t["type"] == "filesystem")
    assert fs["filesystem"]["root"] == "/work"
    mcp = next(t for t in tools if t["type"] == "mcp_server")
    assert mcp["mcp_server"]["name"] == "conn"
    assert mcp["mcp_server"]["url"] == "https://mcp.example/api"
    assert mcp["mcp_server"]["headers"] == {"X-Auth": "t"}


def test_remote_tools_skill_only_omits_code_execution():
    spec = AdvancedAgentConfigSpec(
        work_dir="/work",
        capabilities_spec=default_capabilities_spec(skill_only=True),
    )
    tools = spec.to_remote_agent_tools()
    types = [t["type"] for t in tools]
    assert "code_execution" not in types
    assert "filesystem" in types


def test_remote_tools_no_workdir_no_filesystem():
    spec = AdvancedAgentConfigSpec(
        capabilities_spec=CapabilitiesSpec(disabled_tools=("RUN_COMMAND",))
    )
    tools = spec.to_remote_agent_tools()
    assert tools == []


def test_policy_spec_is_frozen():
    spec = PolicySpec(tool="RUN_COMMAND", decision="DENY")
    with pytest.raises(Exception):
        spec.tool = "OTHER"  # type: ignore
