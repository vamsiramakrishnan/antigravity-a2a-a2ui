"""The session lifecycle orchestrator.

This is the executable form of the sequence in the architecture doc:

    principal   = identity.verify(request)
    workspace   = registry.ensure_workspace(principal)
    generation  = registry.resolve_active_generation(workspace.id)
    credential  = broker.issue(principal, workspace.id)          # tool plane
    storage     = storage_factory(layout, credential)            # trusted only
    session     = materializer.materialize(storage, generation)  # download+verify
    connection  = LocalConnectionStrategy.for_session(...)        # NO credential
    conversation= conversations.create(principal, ..., generation)

The credential exists only inside this function and the storage adapter it
builds. It is never returned, never logged, and never placed on the connection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from a2a_workspace.broker.broker import CredentialBroker
from a2a_workspace.identity.authorization import RequestContext, ToolCredential
from a2a_workspace.materializer.materializer import (
    MaterializedSession,
    SessionMaterializer,
)
from a2a_workspace.registry.registry import WorkspaceRegistry
from a2a_workspace.session.connection import LocalConnectionStrategy
from a2a_workspace.session.conversations import Conversation, ConversationStore
from a2a_workspace.storage.adapter import StorageAdapter
from a2a_workspace.storage.layout import WorkspaceLayout

# (layout, credential) -> StorageAdapter. The composition root supplies this so
# the lifecycle never names a concrete backend or holds a broad client.
StorageAdapterFactory = Callable[[WorkspaceLayout, ToolCredential], StorageAdapter]


@dataclass(frozen=True, slots=True)
class StartedSession:
    conversation: Conversation
    connection: LocalConnectionStrategy
    materialized: MaterializedSession


class SessionLifecycle:
    def __init__(
        self,
        *,
        registry: WorkspaceRegistry,
        broker: CredentialBroker,
        storage_factory: StorageAdapterFactory,
        materializer: SessionMaterializer,
        conversations: ConversationStore,
        organization: str,
        environment: str,
        region: str,
    ) -> None:
        self._registry = registry
        self._broker = broker
        self._storage_factory = storage_factory
        self._materializer = materializer
        self._conversations = conversations
        self._org = organization
        self._env = environment
        self._region = region

    def start(self, ctx: RequestContext) -> StartedSession:
        principal = ctx.principal

        workspace = self._registry.ensure_workspace(
            principal,
            organization=self._org,
            environment=self._env,
            region=self._region,
        )
        generation = self._registry.resolve_active_generation(workspace.workspace_id)

        # Tool-authorization plane: obtain a workspace-scoped read credential.
        # If the request already carries a delegated token we honour it; else we
        # ask the broker to mint one. Either way it is read-only for a session.
        credential = ctx.tool_credential or self._broker.issue(
            principal=principal,
            workspace_id=workspace.workspace_id,
            permissions=("storage.objects.get", "storage.objects.list"),
        )

        layout = WorkspaceLayout(workspace.workspace_id)
        storage = self._storage_factory(layout, credential)

        materialized = self._materializer.materialize(
            storage=storage, generation=generation
        )

        # The connection is built from filesystem paths only. The credential is
        # intentionally absent; assert_no_credentials() guards against regressions.
        connection = LocalConnectionStrategy.for_session(
            skills_dir=materialized.skills_dir,
            app_data_dir=materialized.app_data_dir,
        )

        conversation = self._conversations.create(
            principal=principal,
            workspace_id=workspace.workspace_id,
            generation=generation.number,
            content_digest=generation.content_digest,
        )

        return StartedSession(
            conversation=conversation,
            connection=connection,
            materialized=materialized,
        )
