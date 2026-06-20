"""A2A surface: the agent card and the invocation endpoint.

The agent card advertises the two authorization planes the way Gemini Enterprise
expects: an ``agentAuthorization`` scheme (who may invoke) and a separate
``toolAuthorizations`` entry (the downstream storage credential). Keeping them
distinct in the card is what lets the platform supply a storage token that is
independent of the invocation token.

The invoke endpoint runs the session lifecycle and returns a *reference* to the
materialized session — never bytes, never a credential.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from a2a_workspace.antigravity.config_builder import write_session_file
from a2a_workspace.container import Container
from a2a_workspace.errors import IsolationError, NotFoundError
from a2a_workspace.gateway.dependencies import get_container, request_context
from a2a_workspace.identity.authorization import RequestContext
from a2a_workspace.messaging import materialize_inputs, parse_invocation

router = APIRouter()


@router.get("/.well-known/agent.json")
def agent_card(container: Container = Depends(get_container)) -> dict:
    org = container.config.organization
    return {
        "name": f"{org}-antigravity-workspace",
        "description": "Per-user Antigravity skill workspace over A2A/A2UI.",
        "version": "0.1.0",
        "capabilities": {"a2ui": True, "streaming": False},
        # Two planes, advertised separately.
        "agentAuthorization": {
            "type": "oauth2",
            "description": "Identifies the invoking Gemini Enterprise user.",
        },
        "toolAuthorizations": [
            {
                "id": "workspace-storage",
                "type": "oauth2",
                "description": (
                    "Delegated Cloud Storage access to the user's own workspace "
                    "folder. Supplied independently of the invocation token; "
                    "never exposed to the model."
                ),
                "optional": True,
            },
            {
                "id": "gemini-enterprise",
                "type": "oauth2",
                "description": (
                    "Delegated Discovery Engine access for the user's connectors "
                    "and registered agents. Held at the gateway; the agent reaches "
                    "it only through credential-free proxy tools."
                ),
                "optional": True,
            },
        ],
        "skills": [
            {
                "id": "open-workspace",
                "name": "Open workspace",
                "description": "Materialize and open the user's skill workspace.",
            },
            {
                "id": "enterprise-search",
                "name": "Search enterprise data",
                "description": (
                    "Answer from the user's Gemini Enterprise connectors "
                    "(SharePoint, Jira, GitHub, Salesforce, …) with citations."
                ),
            },
            {
                "id": "invoke-agent",
                "name": "Invoke another agent",
                "description": "Delegate a task to another registered Gemini Enterprise agent.",
            },
        ],
    }


@router.post("/a2a/invoke")
def invoke(
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
    body: dict | None = Body(default=None),
) -> dict:
    """Start (or resume) a session for the verified principal.

    ``body`` is the A2A invocation Gemini Enterprise sends: a ``message`` whose
    ``parts`` carry the user's prompt and uploaded files, plus the Discovery
    Engine ``session``. We parse it, materialize uploaded files into the session's
    ``inputs/`` directory (so the agent can operate on them as files), and thread
    the session through so connector calls share its context.

    Idempotent provisioning happens first, then the lifecycle materializes the
    active generation and pins a conversation to it.
    """
    invocation = parse_invocation(body)

    container.provisioner.ensure_provisioned(ctx.principal)
    try:
        started = container.lifecycle.start(ctx, ge_session=invocation.ge_session)
    except NotFoundError as exc:
        # No active generation yet: the workspace exists but has no published
        # revision. This is a normal first-run state, not an error condition.
        raise HTTPException(
            status_code=409,
            detail=f"workspace has no active skill generation yet: {exc}",
        ) from exc
    except IsolationError as exc:  # pragma: no cover - defense in depth
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    conv = started.conversation

    # Materialize uploaded files into the session's inputs/ directory so the agent
    # can open and process them as real files (not conversation text).
    input_manifest = materialize_inputs(
        started.materialized.inputs_dir, invocation.files
    )

    response = {
        "conversation_id": conv.conversation_id,
        "workspace_id": conv.workspace_id,
        "generation": conv.generation,
        "content_digest": conv.content_digest,
        "prompt": invocation.prompt,
        # Filesystem paths only — no bytes, no tokens.
        "connection": {
            "skills_paths": list(started.connection.skills_paths),
            "app_data_dir": started.connection.app_data_dir,
            "inputs_dir": str(started.materialized.inputs_dir),
            "work_dir": str(started.materialized.work_dir),
            "read_only_skills": started.connection.read_only_skills,
        },
        "inputs": [{"name": e["name"], "mime_type": e["mime_type"]} for e in input_manifest],
    }

    # If Gemini Enterprise is configured and the request carried the user's
    # delegated token, provision the session so the agent's proxy tools can reach
    # connectors/agents. The user token is held server-side (decrypt boundary);
    # the agent gets only a short-lived session proxy token, written to a file in
    # app_data_dir rather than into the model-visible connection env.
    if container.config.gemini.is_configured() and ctx.tool_credential is not None:
        session_token = container.session_tokens.mint(
            principal_key=ctx.principal.key, conversation_id=conv.conversation_id
        )
        container.session_credentials.put(
            conv.conversation_id,
            ctx.tool_credential.secret,
            ge_session=invocation.ge_session,
        )
        write_session_file(
            app_data_dir=started.materialized.app_data_dir,
            gateway_url=container.config.public_url,
            session_token=session_token,
            conversation_id=conv.conversation_id,
            inputs_dir=started.materialized.inputs_dir,
            work_dir=started.materialized.work_dir,
        )
        response["enterprise"] = {
            "enabled": True,
            "gateway_url": container.config.public_url,
            "session_file": str(
                started.materialized.app_data_dir / ".a2a" / "session.json"
            ),
        }

    return response
