"""Glue for Google's ``agents-cli`` (PyPI ``google-agents-cli``).

This module is intentionally pure: no Google SDKs, no network, no FastAPI. It
holds the small, testable pieces that bridge *our* gateway's A2A surface to what
``agents-cli`` / Discovery Engine expect when registering an A2A agent into a
Gemini Enterprise app.

Three concerns live here:

* :func:`agent_card_to_a2a` — adapt the gateway's ``/.well-known/agent.json`` card
  (see :mod:`a2a_workspace.gateway.a2a`) into the A2A "agent-card" JSON shape the
  ``agents-cli publish`` command fetches and Discovery Engine consumes.
* :func:`build_publish_command` — produce the exact ``agents-cli publish
  gemini-enterprise`` argv, including the well-known card URL suffix.
* :func:`discoveryengine_invoker_member` — the IAM member string that must hold
  ``roles/run.servicesInvoker`` so Discovery Engine can call the Cloud Run A2A
  service.

:func:`manifest_dict` mirrors ``agents-cli-manifest.yaml`` for tests/tooling.
"""

from __future__ import annotations

# The path ``agents-cli`` (and Discovery Engine) expects the A2A agent card to be
# served from, relative to the deployed service base URL. Our compat router
# (a2a_workspace.gateway.agents_cli_compat) serves the card here.
WELL_KNOWN_AGENT_CARD_PATH = "/a2a/app/.well-known/agent-card.json"


def agent_card_to_a2a(card: dict, *, base_url: str) -> dict:
    """Adapt our gateway agent card into the A2A agent-card schema.

    The gateway card (:func:`a2a_workspace.gateway.a2a.agent_card`) is close to
    the A2A spec but uses our own field set. The A2A agent-card schema that
    ``agents-cli`` fetches and Discovery Engine registers wants, at minimum:

    * ``name`` / ``description`` / ``version`` — carried straight through.
    * ``url`` — the A2A endpoint clients call. We point it at the gateway's
      invocation endpoint (``{base_url}/a2a/invoke``); the well-known card itself
      is served at :data:`WELL_KNOWN_AGENT_CARD_PATH`.
    * ``capabilities`` — A2A advertises ``streaming``/``pushNotifications``/
      ``stateTransitionHistory`` booleans. We map our ``capabilities.streaming``
      and default the rest to ``False``. Our extra ``a2ui`` flag is preserved
      under ``capabilities`` (it is additive and ignored by consumers that don't
      know it).
    * ``defaultInputModes`` / ``defaultOutputModes`` — A2A media-type lists. We
      infer ``["text/plain"]`` for both since the card has no explicit modes; the
      a2ui capability is surfaced via the capability flag, not a media type.
    * ``skills`` — A2A skills require ``id``/``name``/``description`` plus
      ``tags`` and per-skill input/output modes. Our card already carries the
      first three; we add an empty ``tags`` list and the default modes for each.

    Fields not present in the source card are inferred (documented above) rather
    than invented from nothing, so the adapter stays a faithful projection.
    """
    base = base_url.rstrip("/")
    src_caps = card.get("capabilities") or {}

    capabilities: dict = {
        "streaming": bool(src_caps.get("streaming", False)),
        "pushNotifications": False,
        "stateTransitionHistory": False,
    }
    # Preserve our additive a2ui flag if present (harmless to A2A consumers).
    if "a2ui" in src_caps:
        capabilities["a2ui"] = bool(src_caps["a2ui"])

    default_input_modes = ["text/plain"]
    default_output_modes = ["text/plain"]

    skills = []
    for skill in card.get("skills", []) or []:
        skills.append(
            {
                "id": skill.get("id", ""),
                "name": skill.get("name", skill.get("id", "")),
                "description": skill.get("description", ""),
                "tags": list(skill.get("tags", []) or []),
                "inputModes": list(skill.get("inputModes", default_input_modes)),
                "outputModes": list(skill.get("outputModes", default_output_modes)),
            }
        )

    return {
        "name": card.get("name", ""),
        "description": card.get("description", ""),
        "version": card.get("version", "0.1.0"),
        # The A2A endpoint clients invoke (our gateway's A2A invoke route).
        "url": f"{base}/a2a/invoke",
        "capabilities": capabilities,
        "defaultInputModes": default_input_modes,
        "defaultOutputModes": default_output_modes,
        "skills": skills,
    }


def build_publish_command(
    *,
    base_url: str,
    app_engine_id: str,
    display_name: str,
    description: str = "",
) -> list[str]:
    """Return the exact ``agents-cli publish gemini-enterprise`` argv.

    ``app_engine_id`` is the full Gemini Enterprise engine resource name, e.g.
    ``projects/<n>/locations/global/collections/default_collection/engines/<app>``.
    The agent-card URL is derived as
    ``{base_url}{WELL_KNOWN_AGENT_CARD_PATH}``; ``agents-cli`` fetches it and
    registers the card with Discovery Engine.
    """
    base = base_url.rstrip("/")
    card_url = f"{base}{WELL_KNOWN_AGENT_CARD_PATH}"
    argv = [
        "agents-cli",
        "publish",
        "gemini-enterprise",
        "--registration-type",
        "a2a",
        "--agent-card-url",
        card_url,
        "--gemini-enterprise-app-id",
        app_engine_id,
        "--display-name",
        display_name,
    ]
    if description:
        argv += ["--description", description]
    return argv


def discoveryengine_invoker_member(project_number: str | int) -> str:
    """IAM member for the Discovery Engine service agent.

    For an A2A agent on Cloud Run, this member must be granted
    ``roles/run.servicesInvoker`` so Discovery Engine can invoke the deployed
    A2A service.
    """
    return (
        f"serviceAccount:service-{project_number}"
        "@gcp-sa-discoveryengine.iam.gserviceaccount.com"
    )


def manifest_dict(
    *,
    name: str = "antigravity-a2a-a2ui",
    acli_version: str = "0.5.0",
    agent_directory: str = "app",
    region: str = "us-east1",
    base_template: str = "adk_a2a",
    generated_at: str = "2026-06-20T00:00:00Z",
    language: str = "python",
    deployment_target: str = "cloud_run",
    session_type: str = "none",
    cicd_runner: str = "google-cloud-build",
    include_data_ingestion: bool = False,
    is_a2a: bool = True,
    datastore: str = "none",
    agent_guidance_filename: str = "AGENTS.md",
) -> dict:
    """Return the manifest as a dict, mirroring ``agents-cli-manifest.yaml``.

    Useful for tests and for any tooling that wants the canonical manifest shape
    without parsing YAML.
    """
    return {
        "name": name,
        "acli_version": acli_version,
        "agent_directory": agent_directory,
        "region": region,
        "base_template": base_template,
        "generated_at": generated_at,
        "language": language,
        "create_params": {
            "deployment_target": deployment_target,
            "session_type": session_type,
            "cicd_runner": cicd_runner,
            "include_data_ingestion": include_data_ingestion,
            "is_a2a": is_a2a,
            "datastore": datastore,
            "agent_guidance_filename": agent_guidance_filename,
        },
    }
