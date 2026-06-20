"""Gemini Enterprise integration via the Discovery Engine API.

Gemini Enterprise's programmatic surface *is* the Discovery Engine API
(``discoveryengine.googleapis.com``). This package wraps the slice of it an
Antigravity agent needs:

* **Connectors / data stores** — search and grounded answers over the user's
  connected third-party data (SharePoint, Jira, GitHub, Salesforce, …) via the
  assistant ``:streamAssist`` method and ``dataStoreSpecs``.
* **Agent-to-agent** — invoke another *registered* Gemini Enterprise agent by
  passing its resource name in ``agentsSpec``.

The load-bearing rule, consistent with the rest of this project: the
:class:`~a2a_workspace.gemini_enterprise.client.DiscoveryEngineClient` is
constructed with the **end-user's delegated token** (never Application Default
Credentials, never the gateway service account), and it lives only in the
*trusted control plane*. The Antigravity agent reaches it through thin proxy
tools (:mod:`a2a_workspace.gemini_enterprise.tools`) so the agent can *use*
connectors and other agents without ever holding the raw credential.
"""

from a2a_workspace.gemini_enterprise.client import DiscoveryEngineClient
from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.models import (
    AgentInfo,
    AssistResult,
    Citation,
    DataStoreInfo,
)

__all__ = [
    "AgentInfo",
    "AssistResult",
    "Citation",
    "DataStoreInfo",
    "DiscoveryEngineClient",
    "GeminiEnterpriseConfig",
]
