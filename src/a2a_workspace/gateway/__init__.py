"""The shared, stateless A2A/A2UI gateway (Cloud Run).

This is the only network-facing component. It holds no broad storage credential.
For each request it verifies the agent-authorization token into a Principal,
optionally accepts a tool-authorization credential, and dispatches to the
lifecycle / registry. Concurrency across tenants is fine precisely because no
tenant's data is reachable without that tenant's own per-request credential.
"""

from a2a_workspace.gateway.app import create_app

__all__ = ["create_app"]
