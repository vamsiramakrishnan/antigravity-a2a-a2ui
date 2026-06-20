"""Workspace provisioning, split from the runtime by service identity.

Provisioning (create the managed folder, set IAM, write the metadata record) is
privileged and runs under its own service account, separate from the gateway's.
The gateway can *read* workspace metadata and trigger idempotent provisioning,
but it cannot grant IAM or read across workspaces. Publication runs under a third
identity. Keeping these apart is the separation-of-duties half of the design.
"""

from a2a_workspace.provisioning.provisioner import (
    ProvisionResult,
    WorkspaceProvisioner,
)

__all__ = ["ProvisionResult", "WorkspaceProvisioner"]
