"""Discovery Engine client — the credentialed call into Gemini Enterprise.

Constructed with the **end-user's delegated OAuth access token**. It never falls
back to Application Default Credentials and never uses the gateway service
account, so a request can only ever reach what the calling user is allowed to
reach. It lives in the trusted control plane; the Antigravity agent talks to it
only through the proxy tools.

Three capabilities, all on the assistant resource:

* :meth:`assist` — grounded answer over the user's connectors (data stores).
* :meth:`invoke_agent` — same call, but routed to a named registered agent.
* :meth:`list_agents` / :meth:`list_data_stores` — discovery for setup/UX.

NOTE on the API surface: ``:streamAssist`` is a ``v1alpha`` method and its
request shape is still evolving (the ``query``/``agentsSpec`` fields in
particular). The request body is built in one place — :meth:`_assist_body` — so
adjusting to a deployment's exact contract is a one-line change. Responses are
server-streamed as a JSON array of chunks; :meth:`_aggregate` folds them into a
single :class:`AssistResult`.
"""

from __future__ import annotations

import json
import uuid

from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.models import (
    AgentInfo,
    AssistResult,
    Citation,
    DataStoreInfo,
)
from a2a_workspace.gemini_enterprise.transport import (
    HttpResponse,
    Transport,
    UrllibTransport,
)


class DiscoveryEngineClient:
    def __init__(
        self,
        *,
        config: GeminiEnterpriseConfig,
        access_token: str,
        transport: Transport | None = None,
    ) -> None:
        if not access_token:
            raise ValueError(
                "DiscoveryEngineClient requires an end-user access token; it must "
                "not run under Application Default Credentials."
            )
        if not config.is_configured():
            raise ValueError("GeminiEnterpriseConfig needs at least project and engine")
        self._config = config
        self._token = access_token
        self._transport = transport or UrllibTransport()

    # -- public API --------------------------------------------------------

    def assist(
        self,
        query: str,
        *,
        session: str | None = None,
        data_store_ids: tuple[str, ...] = (),
        google_search: bool = False,
    ) -> AssistResult:
        """Grounded answer over the user's connected data (and optional web)."""
        body = self._assist_body(
            query,
            session=session,
            data_store_ids=data_store_ids,
            google_search=google_search,
        )
        resp = self._post(f"{self._assistant_url()}:streamAssist", body)
        return self._aggregate(resp)

    def invoke_agent(
        self, agent_id: str, query: str, *, session: str | None = None
    ) -> AssistResult:
        """Route the query to another registered Gemini Enterprise agent."""
        body = self._assist_body(query, session=session, agent_id=agent_id)
        resp = self._post(f"{self._assistant_url()}:streamAssist", body)
        return self._aggregate(resp)

    def list_agents(self) -> list[AgentInfo]:
        resp = self._get(f"{self._assistant_url()}/agents")
        data = resp.json() or {}
        return [
            AgentInfo(
                name=a.get("name", ""),
                display_name=a.get("displayName", ""),
                description=a.get("description", ""),
            )
            for a in data.get("agents", [])
        ]

    def list_data_stores(self) -> list[DataStoreInfo]:
        collection = (
            f"projects/{self._config.project}/locations/{self._config.location}"
            f"/collections/{self._config.collection}"
        )
        resp = self._get(f"{self._config.base_url}/{collection}/dataStores")
        data = resp.json() or {}
        return [
            DataStoreInfo(
                name=d.get("name", ""),
                display_name=d.get("displayName", ""),
                industry_vertical=d.get("industryVertical", ""),
                solution_types=tuple(d.get("solutionTypes", []) or ()),
            )
            for d in data.get("dataStores", [])
        ]

    # -- request building --------------------------------------------------

    def _assist_body(
        self,
        query: str,
        *,
        session: str | None = None,
        agent_id: str | None = None,
        data_store_ids: tuple[str, ...] = (),
        google_search: bool = False,
    ) -> dict:
        body: dict = {
            "query": {"text": query},
            # "-" tells Discovery Engine to auto-allocate a new session.
            "session": session or f"{self._config.assistant_name}/sessions/-",
        }
        if agent_id:
            body["agentsSpec"] = {
                "agentSpecs": [{"agent": self._config.agent_name(agent_id)}]
            }
        if data_store_ids:
            body["dataStoreSpecs"] = [
                {"dataStore": self._config.data_store_name(ds)} for ds in data_store_ids
            ]
        if google_search:
            body["googleSearchGroundingEnabled"] = True
        return body

    # -- response parsing --------------------------------------------------

    def _aggregate(self, resp: HttpResponse) -> AssistResult:
        """Fold the streamed array of StreamAssistResponse chunks into one result."""
        payload = resp.json()
        # The streaming endpoint returns a JSON array; a non-stream fallback may
        # return a single object. Normalise to a list.
        chunks = payload if isinstance(payload, list) else [payload]

        answer_parts: list[str] = []
        citations: list[Citation] = []
        session = ""
        state = ""
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            answer = chunk.get("answer") or {}
            # Answer text may arrive as a top-level string or under answer.replies.
            if isinstance(answer, str):
                answer_parts.append(answer)
            elif isinstance(answer, dict):
                for reply in answer.get("replies", []) or []:
                    text = _extract_text(reply)
                    if text:
                        answer_parts.append(text)
                for ref in answer.get("citations", []) or answer.get("references", []) or []:
                    citations.append(_extract_citation(ref))
                state = answer.get("state", state)
            session = chunk.get("sessionInfo", {}).get("session", session) or chunk.get(
                "session", session
            )
        return AssistResult(
            answer="".join(answer_parts).strip() or "(no answer returned)",
            citations=tuple(citations),
            session=session,
            state=state,
        )

    # -- low-level HTTP ----------------------------------------------------

    def _assistant_url(self) -> str:
        return f"{self._config.base_url}/{self._config.assistant_name}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _post(self, url: str, body: dict) -> HttpResponse:
        return self._transport.request(
            "POST",
            url,
            headers=self._headers(),
            body=json.dumps(body).encode(),
        )

    def _get(self, url: str) -> HttpResponse:
        return self._transport.request("GET", url, headers=self._headers())


def _extract_text(reply: dict) -> str:
    if not isinstance(reply, dict):
        return ""
    # Reply may be {"text": "..."} or {"groundedContent": {"content": {"text": ...}}}.
    if "text" in reply:
        return str(reply["text"])
    content = reply.get("groundedContent", {}).get("content", {})
    return str(content.get("text", "")) if isinstance(content, dict) else ""


def _extract_citation(ref: dict) -> Citation:
    if not isinstance(ref, dict):
        return Citation(title=str(ref))
    return Citation(
        title=ref.get("title", "") or ref.get("displayName", ""),
        uri=ref.get("uri", "") or ref.get("url", ""),
        snippet=ref.get("snippet", "") or ref.get("content", ""),
    )
