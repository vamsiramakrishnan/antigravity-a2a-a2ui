"""Runtime configuration for the control plane.

Configuration is environment-first (it runs on Cloud Run) but plain-dataclass so
it is trivial to construct in tests. Nothing security-relevant is defaulted to an
insecure value: the dev identity verifier and local storage backends must be
selected explicitly.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig

# Stable namespace for deriving workspace UUIDs from principals. Changing this
# would re-map every principal to a new workspace, so it is a constant, not a
# config knob.
WORKSPACE_NAMESPACE = uuid.UUID("6f1d2c4e-9a3b-5c7d-8e1f-2a3b4c5d6e7f")


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Where immutable revisions live.

    One bucket per (organization x environment x data-residency boundary). The
    workspace prefix is *inside* the bucket; the bucket itself is shared, but the
    shared Cloud Run service account holds no bucket-wide object grant.
    """

    backend: str = "local"  # "local" | "gcs"
    bucket: str = "skills-local"
    # Used only by the local backend: a directory that stands in for the bucket.
    local_root: str = ".local-storage"
    # Logical residency tag, surfaced in workspace metadata for auditing.
    region: str = "local"


@dataclass(frozen=True, slots=True)
class IdentityConfig:
    backend: str = "dev"  # "dev" | "jwt"
    issuer: str = ""
    audience: str = ""
    jwks_uri: str = ""
    allow_insecure_dev: bool = False


@dataclass(frozen=True, slots=True)
class RegistryConfig:
    backend: str = "memory"  # "memory" | "firestore"
    firestore_project: str = ""
    firestore_database: str = "(default)"


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    """Credential-broker settings.

    ``enabled`` selects the downscoped-credential path; when disabled the gateway
    expects a delegated user-OAuth token on each request instead.
    """

    enabled: bool = False
    target_service_account: str = ""
    default_ttl_seconds: int = 600


@dataclass(frozen=True, slots=True)
class SessionConfig:
    # Root for per-session materialized directories. On Cloud Run this is a
    # tmpfs / local disk that never outlives the instance.
    materialization_root: str = "/tmp/a2a-sessions"
    # Read-only global catalog of org-approved skills (a GCS FUSE mount in prod).
    # Optional; absence simply means no shared catalog is layered in.
    global_catalog_path: str | None = None


@dataclass(frozen=True, slots=True)
class Config:
    organization: str = "acme"
    environment: str = "dev"
    storage: StorageConfig = field(default_factory=StorageConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    gemini: GeminiEnterpriseConfig = field(
        default_factory=lambda: GeminiEnterpriseConfig(project="")
    )
    # Public base URL the agent's proxy tools call back to (this gateway).
    public_url: str = "http://localhost:8080"
    # HMAC secret for session proxy tokens. Empty -> a process-ephemeral secret is
    # generated at startup (fine for a single dev instance; set explicitly in prod
    # so tokens survive across instances).
    session_token_secret: str = ""
    workspace_namespace: uuid.UUID = WORKSPACE_NAMESPACE

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = env if env is not None else dict(os.environ)

        def get(key: str, default: str) -> str:
            return e.get(key, default)

        return cls(
            organization=get("A2A_ORG", "acme"),
            environment=get("A2A_ENV", "dev"),
            storage=StorageConfig(
                backend=get("A2A_STORAGE_BACKEND", "local"),
                bucket=get("A2A_STORAGE_BUCKET", "skills-local"),
                local_root=get("A2A_STORAGE_LOCAL_ROOT", ".local-storage"),
                region=get("A2A_STORAGE_REGION", "local"),
            ),
            identity=IdentityConfig(
                backend=get("A2A_IDENTITY_BACKEND", "dev"),
                issuer=get("A2A_OIDC_ISSUER", ""),
                audience=get("A2A_OIDC_AUDIENCE", ""),
                jwks_uri=get("A2A_OIDC_JWKS_URI", ""),
                allow_insecure_dev=get("A2A_ALLOW_INSECURE_DEV", "false").lower()
                == "true",
            ),
            registry=RegistryConfig(
                backend=get("A2A_REGISTRY_BACKEND", "memory"),
                firestore_project=get("A2A_FIRESTORE_PROJECT", ""),
                firestore_database=get("A2A_FIRESTORE_DATABASE", "(default)"),
            ),
            broker=BrokerConfig(
                enabled=get("A2A_BROKER_ENABLED", "false").lower() == "true",
                target_service_account=get("A2A_BROKER_TARGET_SA", ""),
                default_ttl_seconds=int(get("A2A_BROKER_TTL", "600")),
            ),
            session=SessionConfig(
                materialization_root=get(
                    "A2A_SESSION_ROOT", "/tmp/a2a-sessions"
                ),
                global_catalog_path=e.get("A2A_GLOBAL_CATALOG_PATH") or None,
            ),
            gemini=GeminiEnterpriseConfig.from_env(e),
            public_url=get("A2A_PUBLIC_URL", "http://localhost:8080"),
            session_token_secret=get("A2A_SESSION_TOKEN_SECRET", ""),
        )
