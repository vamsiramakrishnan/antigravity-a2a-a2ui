"""Client for Google's Managed Agents platform (aiplatform v1beta1).

Two surfaces, one credentialed client:

* **Agents API** — CRUD over ``projects/.../locations/.../agents``. An agent is
  defined by an :class:`~a2a_workspace.gemini_enterprise.agent_models.AgentSpec`
  (base agent, instruction, tools, base environment) and lives as a server-side
  resource.
* **Interactions API** — runs a turn against an agent and streams back events.

Constructed with the **caller's access token** (the end-user's delegated
credential, or a service identity with ``aiplatform`` scope). It mirrors
:class:`~a2a_workspace.gemini_enterprise.client.DiscoveryEngineClient`: never
Application Default Credentials, transport injectable for testing.

On the Interactions API specifically, the **supported surface is the
``google-genai`` SDK**, not raw REST. So :meth:`create_interaction` prefers
``google.genai`` when it is importable (behind an import guard, so its absence
never breaks import or tests) and falls back to a best-effort REST call
otherwise. The Agents CRUD methods use plain REST via the injected transport.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from a2a_workspace.gemini_enterprise.agent_models import AgentSpec, InteractionEvent
from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.transport import (
    HttpResponse,
    Transport,
    UrllibTransport,
)


class AgentPlatformClient:
    def __init__(
        self,
        config: GeminiEnterpriseConfig,
        access_token: str,
        transport: Transport | None = None,
    ) -> None:
        if not access_token:
            raise ValueError(
                "AgentPlatformClient requires a caller access token; it must not "
                "run under Application Default Credentials."
            )
        if not config.project:
            raise ValueError("GeminiEnterpriseConfig needs a project")
        self._config = config
        self._token = access_token
        self._transport = transport or UrllibTransport()

    # -- agents CRUD -------------------------------------------------------

    def create_agent(self, spec: AgentSpec) -> dict:
        """Create an agent resource. ``agent_id`` is set from ``spec.id``.

        The id is sent both as the ``agent_id`` query param (the resource id the
        server should assign) and as ``name`` in the body, matching the sample.
        """
        url = f"{self._agents_url()}?agent_id={quote(spec.id)}"
        resp = self._post(url, spec.to_api())
        return resp.json() or {}

    def get_agent(self, agent_id: str) -> dict:
        resp = self._get(self._agent_url(agent_id))
        return resp.json() or {}

    def patch_agent(
        self,
        agent_id: str,
        *,
        base_environment=None,
        tools=None,
        system_instruction: str | None = None,
    ) -> dict:
        """Partial-update an agent.

        The ``update_mask`` is built from exactly the kwargs that were provided
        (non-``None``), so an unset field is never blanked. ``base_environment``
        and ``tools`` accept either model objects (with ``to_api``) or already
        wire-shaped values.
        """
        body: dict = {}
        mask: list[str] = []
        if base_environment is not None:
            body["base_environment"] = _to_api(base_environment)
            mask.append("base_environment")
        if tools is not None:
            body["tools"] = [_to_api(t) for t in tools]
            mask.append("tools")
        if system_instruction is not None:
            body["system_instruction"] = system_instruction
            mask.append("system_instruction")
        url = f"{self._agent_url(agent_id)}?update_mask={','.join(mask)}"
        resp = self._transport.request(
            "PATCH", url, headers=self._headers(), body=json.dumps(body).encode()
        )
        return resp.json() or {}

    def delete_agent(self, agent_id: str) -> None:
        self._transport.request(
            "DELETE", self._agent_url(agent_id), headers=self._headers()
        )

    def list_agents(self) -> list[dict]:
        resp = self._get(self._agents_url())
        data = resp.json() or {}
        return data.get("agents", []) or []

    # -- interactions ------------------------------------------------------

    def create_interaction(
        self,
        *,
        agent: str,
        input: str,
        environment_type: str = "remote",
        stream: bool = True,
        store: bool = True,
        background: bool = False,
    ) -> list[InteractionEvent]:
        """Run a turn against ``agent`` and collect the streamed events.

        Prefers the supported ``google-genai`` SDK when it is importable; falls
        back to a best-effort REST call otherwise (see module docstring).
        """
        request = {
            "agent": agent,
            "input": input,
            "environment": {"type": environment_type},
            "stream": stream,
            "store": store,
            "background": background,
        }

        sdk_events = self._interact_via_genai(request)
        if sdk_events is not None:
            return sdk_events
        return self._interact_via_rest(agent, request)

    def _interact_via_genai(self, request: dict) -> list[InteractionEvent] | None:
        """Supported path: drive the Interactions API through google-genai.

        Returns ``None`` (not an empty list) when the SDK is unavailable, so the
        caller can distinguish "SDK absent" from "SDK ran, produced no events".
        Import-guarded so the dependency is optional.
        """
        try:
            from google import genai  # type: ignore
        except Exception:
            return None

        client = genai.Client(
            vertexai=True,
            project=self._config.project,
            location=self._config.location,
        )
        events: list[InteractionEvent] = []
        for event in client.interactions.create(request):
            events.append(InteractionEvent.from_api(_as_dict(event)))
        return events

    def _interact_via_rest(
        self, agent: str, request: dict
    ) -> list[InteractionEvent]:
        # NOTE: the REST path for interactions is best-effort and UNVERIFIED.
        # The supported surface is the google-genai SDK (see above); this exists
        # so the client is testable and degrades gracefully without the SDK.
        agent_id = agent.rsplit("/", 1)[-1]
        url = f"{self._agent_url(agent_id)}:interact"
        body = {
            "input": request["input"],
            "environment": request["environment"],
            "stream": request["stream"],
            "store": request["store"],
            "background": request["background"],
        }
        resp = self._post(url, body)
        payload = resp.json()
        # Streamed as a JSON array of events; a non-stream reply may be a single
        # object. Normalise to a list of event dicts.
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("events") or [payload]
        else:
            items = []
        return [
            InteractionEvent.from_api(e) for e in items if isinstance(e, dict)
        ]

    # -- URLs / HTTP -------------------------------------------------------

    @property
    def base_url(self) -> str:
        """v1beta1 endpoint. Regional unless the location is ``global``."""
        if self._config.location == "global":
            host = "aiplatform.googleapis.com"
        else:
            host = f"{self._config.location}-aiplatform.googleapis.com"
        return f"https://{host}/v1beta1"

    @property
    def parent(self) -> str:
        return (
            f"projects/{self._config.project}"
            f"/locations/{self._config.location}"
        )

    def _agents_url(self) -> str:
        return f"{self.base_url}/{self.parent}/agents"

    def _agent_url(self, agent_id: str) -> str:
        if "/" in agent_id:  # already a full resource name
            return f"{self.base_url}/{agent_id}"
        return f"{self._agents_url()}/{agent_id}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _post(self, url: str, body: dict) -> HttpResponse:
        return self._transport.request(
            "POST", url, headers=self._headers(), body=json.dumps(body).encode()
        )

    def _get(self, url: str) -> HttpResponse:
        return self._transport.request("GET", url, headers=self._headers())


def _to_api(value):
    """Accept a model object (has ``to_api``) or an already wire-shaped value."""
    to_api = getattr(value, "to_api", None)
    return to_api() if callable(to_api) else value


def _as_dict(event) -> dict:
    """Coerce an SDK event object into a plain dict for InteractionEvent."""
    if isinstance(event, dict):
        return event
    for attr in ("to_dict", "model_dump", "dict"):
        fn = getattr(event, attr, None)
        if callable(fn):
            try:
                result = fn()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
    if hasattr(event, "__dict__"):
        return dict(vars(event))
    return {"text": str(event)}
