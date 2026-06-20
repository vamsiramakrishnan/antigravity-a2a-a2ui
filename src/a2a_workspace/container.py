"""Composition root.

This is the *only* place that names concrete backends and wires identities
together. Everything else depends on ports. Building the container from a
:class:`Config` keeps the security-relevant choices (which identity verifier,
whether the broker is on, which storage backend) in one auditable spot.

Note the storage factory it builds: it closes over the bucket name but produces
an adapter only when handed a per-request credential. There is no broad,
long-lived storage client anywhere in the container.
"""

from __future__ import annotations

from dataclasses import dataclass

import os
from collections.abc import Callable

from a2a_workspace.broker.broker import (
    CredentialBroker,
    DelegatedOAuthBroker,
    DownscopedCredentialBroker,
)
from a2a_workspace.config import Config
from a2a_workspace.gemini_enterprise.client import DiscoveryEngineClient
from a2a_workspace.gemini_enterprise.transport import Transport
from a2a_workspace.identity.authorization import ToolCredential
from a2a_workspace.identity.session_token import (
    SessionCredentialStore,
    SessionTokenService,
)
from a2a_workspace.identity.verifier import (
    DevIdentityVerifier,
    IdentityVerifier,
    JwtIdentityVerifier,
)
from a2a_workspace.materializer.materializer import SessionMaterializer
from a2a_workspace.provisioning.provisioner import WorkspaceProvisioner
from a2a_workspace.registry.drafts import DraftService
from a2a_workspace.registry.memory import InMemoryRegistry
from a2a_workspace.registry.registry import WorkspaceRegistry
from a2a_workspace.session.conversations import ConversationStore
from a2a_workspace.session.lifecycle import SessionLifecycle, StorageAdapterFactory
from a2a_workspace.storage.adapter import StorageAdapter
from a2a_workspace.storage.layout import WorkspaceLayout
from a2a_workspace.storage.local import LocalStorageAdapter


# (access_token) -> DiscoveryEngineClient. Built per request from the user's
# delegated token; there is no long-lived, ambient Discovery Engine client.
DiscoveryClientFactory = Callable[[str], DiscoveryEngineClient]


@dataclass
class Container:
    config: Config
    identity: IdentityVerifier
    registry: WorkspaceRegistry
    broker: CredentialBroker
    storage_factory: StorageAdapterFactory
    materializer: SessionMaterializer
    conversations: ConversationStore
    drafts: DraftService
    provisioner: WorkspaceProvisioner
    lifecycle: SessionLifecycle
    session_tokens: SessionTokenService
    session_credentials: SessionCredentialStore
    discovery_client_factory: DiscoveryClientFactory


def build_container(
    config: Config, *, discovery_transport: Transport | None = None
) -> Container:
    identity = _build_identity(config)
    registry = _build_registry(config)
    storage_factory = _build_storage_factory(config)
    broker = _build_broker(config, registry)

    materializer = SessionMaterializer(
        root=config.session.materialization_root,
        global_catalog_path=config.session.global_catalog_path,
    )
    conversations = ConversationStore()
    drafts = DraftService(registry=registry)
    provisioner = WorkspaceProvisioner(
        registry=registry,
        organization=config.organization,
        environment=config.environment,
        region=config.storage.region,
    )
    lifecycle = SessionLifecycle(
        registry=registry,
        broker=broker,
        storage_factory=storage_factory,
        materializer=materializer,
        conversations=conversations,
        organization=config.organization,
        environment=config.environment,
        region=config.storage.region,
    )

    secret = (config.session_token_secret or os.urandom(32).hex()).encode()
    session_tokens = SessionTokenService(secret=secret)
    session_credentials = SessionCredentialStore()

    def discovery_client_factory(access_token: str) -> DiscoveryEngineClient:
        return DiscoveryEngineClient(
            config=config.gemini,
            access_token=access_token,
            transport=discovery_transport,
        )

    return Container(
        config=config,
        identity=identity,
        registry=registry,
        broker=broker,
        storage_factory=storage_factory,
        materializer=materializer,
        conversations=conversations,
        drafts=drafts,
        provisioner=provisioner,
        lifecycle=lifecycle,
        session_tokens=session_tokens,
        session_credentials=session_credentials,
        discovery_client_factory=discovery_client_factory,
    )


def _build_identity(config: Config) -> IdentityVerifier:
    if config.identity.backend == "jwt":
        return JwtIdentityVerifier(
            issuer=config.identity.issuer,
            audience=config.identity.audience,
            jwks_uri=config.identity.jwks_uri,
        )
    if config.identity.backend == "dev":
        return DevIdentityVerifier(allow_insecure=config.identity.allow_insecure_dev)
    raise ValueError(f"unknown identity backend: {config.identity.backend}")


def _build_registry(config: Config) -> WorkspaceRegistry:
    if config.registry.backend == "memory":
        return InMemoryRegistry(namespace=config.workspace_namespace)
    if config.registry.backend == "firestore":
        from a2a_workspace.registry.firestore import FirestoreRegistry

        return FirestoreRegistry(
            project=config.registry.firestore_project,
            database=config.registry.firestore_database,
            namespace=config.workspace_namespace,
        )
    raise ValueError(f"unknown registry backend: {config.registry.backend}")


def _build_storage_factory(config: Config) -> StorageAdapterFactory:
    if config.storage.backend == "local":
        root = config.storage.local_root

        def factory(layout: WorkspaceLayout, credential: ToolCredential) -> StorageAdapter:
            return LocalStorageAdapter(root=root, layout=layout, credential=credential)

        return factory

    if config.storage.backend == "gcs":
        bucket = config.storage.bucket

        def factory(layout: WorkspaceLayout, credential: ToolCredential) -> StorageAdapter:
            from a2a_workspace.storage.gcs import GcsStorageAdapter

            return GcsStorageAdapter(
                bucket=bucket, layout=layout, credential=credential
            )

        return factory

    raise ValueError(f"unknown storage backend: {config.storage.backend}")


def _build_broker(config: Config, registry: WorkspaceRegistry) -> CredentialBroker:
    if config.broker.enabled:
        # Production: STS downscoping against the broker's privileged SA. The
        # minter is left unconfigured here because it must be supplied by the
        # separately-deployed broker process, not the gateway.
        raise NotImplementedError(
            "downscoped broker must be deployed as a separate service and "
            "injected; build_container does not mint privileged credentials in "
            "the gateway process by design"
        )

    # Default/dev: delegated user-OAuth. In local mode the 'token' is a synthetic
    # marker; the local storage adapter does not check it, but the scope_prefix
    # binding and adapter guards are still exercised.
    def token_provider(principal, workspace_id):
        return (f"delegated:{principal.key}:{workspace_id}", None)

    return DelegatedOAuthBroker(token_provider=token_provider)
