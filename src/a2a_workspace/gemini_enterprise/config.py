"""Configuration for the Discovery Engine / Gemini Enterprise integration.

Everything needed to address an assistant and build resource names. The defaults
match a standard Gemini Enterprise app (``default_collection`` /
``default_assistant``), so in the common case a user only supplies the project
and engine (app) id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeminiEnterpriseConfig:
    project: str
    # "global" uses the global endpoint; a region (e.g. "us", "eu") uses the
    # regional host {location}-discoveryengine.googleapis.com.
    location: str = "global"
    collection: str = "default_collection"
    engine: str = ""  # the Gemini Enterprise app id
    assistant: str = "default_assistant"
    api_version: str = "v1alpha"

    @property
    def host(self) -> str:
        if self.location == "global":
            return "discoveryengine.googleapis.com"
        return f"{self.location}-discoveryengine.googleapis.com"

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/{self.api_version}"

    @property
    def engine_name(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}"
            f"/collections/{self.collection}/engines/{self.engine}"
        )

    @property
    def assistant_name(self) -> str:
        return f"{self.engine_name}/assistants/{self.assistant}"

    def agent_name(self, agent_id: str) -> str:
        """Full resource name for a registered agent under this assistant."""
        if "/" in agent_id:
            # Already a full resource name; trust it as-is.
            return agent_id
        return f"{self.assistant_name}/agents/{agent_id}"

    def data_store_name(self, data_store_id: str) -> str:
        if "/" in data_store_id:
            return data_store_id
        return (
            f"projects/{self.project}/locations/{self.location}"
            f"/collections/{self.collection}/dataStores/{data_store_id}"
        )

    def is_configured(self) -> bool:
        return bool(self.project and self.engine)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "GeminiEnterpriseConfig":
        e = env if env is not None else dict(os.environ)
        return cls(
            project=e.get("A2A_GE_PROJECT", ""),
            location=e.get("A2A_GE_LOCATION", "global"),
            collection=e.get("A2A_GE_COLLECTION", "default_collection"),
            engine=e.get("A2A_GE_ENGINE", ""),
            assistant=e.get("A2A_GE_ASSISTANT", "default_assistant"),
            api_version=e.get("A2A_GE_API_VERSION", "v1alpha"),
        )
