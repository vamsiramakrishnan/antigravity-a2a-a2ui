"""Idempotent, first-touch workspace provisioning.

``ensure_provisioned`` is safe to call on every authenticated invocation: it
creates the workspace record and (in production) the managed folder + IAM
binding only if they do not already exist. There is no need for a Cloud Run
service per user; the shared control plane calls this and moves on.

The IAM step is represented by an injected ``folder_iam`` port so the privileged
GCP calls live behind a seam that only the provisioner identity can satisfy. In
local mode it is a no-op recorder.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from a2a_workspace.identity.principal import Principal
from a2a_workspace.registry.models import Workspace
from a2a_workspace.registry.registry import WorkspaceRegistry
from a2a_workspace.storage.layout import WorkspaceLayout

# (workspace_id, principal_key, member_email|None) -> None. In production this
# creates the managed folder and binds the user's IAM to that folder only.
FolderIamBinder = Callable[[str, str, str | None], None]


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    workspace: Workspace
    created: bool
    managed_folder_prefix: str


class WorkspaceProvisioner:
    def __init__(
        self,
        *,
        registry: WorkspaceRegistry,
        organization: str,
        environment: str,
        region: str,
        folder_iam: FolderIamBinder | None = None,
    ) -> None:
        self._registry = registry
        self._org = organization
        self._env = environment
        self._region = region
        self._folder_iam = folder_iam or _noop_iam

    def ensure_provisioned(self, principal: Principal) -> ProvisionResult:
        # ensure_workspace is itself idempotent; detect creation by comparing the
        # owner before/after is overkill, so we treat "no active generation and
        # freshly seen" as created. Simpler: ask the registry.
        existing_owner = None
        derived_id = None
        try:
            derived = self._registry.ensure_workspace(
                principal,
                organization=self._org,
                environment=self._env,
                region=self._region,
            )
        finally:
            pass
        workspace = derived
        layout = WorkspaceLayout(workspace.workspace_id)

        # Bind the user's IAM to their managed folder. Idempotent in production
        # (set-IAM is declarative); here it records intent.
        self._folder_iam(workspace.workspace_id, principal.key, principal.email)

        return ProvisionResult(
            workspace=workspace,
            created=workspace.active_generation is None,
            managed_folder_prefix=layout.prefix,
        )


def _noop_iam(workspace_id: str, principal_key: str, member_email: str | None) -> None:
    # Local/dev: storage isolation is not enforced by IAM here. The shape exists
    # so production wiring drops in without touching call sites.
    return None
