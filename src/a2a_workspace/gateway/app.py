"""FastAPI application factory.

Stateless by construction: the only process-local state is the in-memory
registry/conversation store used in dev. Swap the backends via ``Config`` and the
same app becomes a horizontally-scalable Cloud Run service.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from a2a_workspace.config import Config
from a2a_workspace.container import Container, build_container
from a2a_workspace.errors import (
    AuthorizationError,
    ConflictError,
    IntegrityError,
    IsolationError,
    NotFoundError,
    ValidationError,
    WorkspaceError,
)
from a2a_workspace.gateway import a2a, enterprise_api, registry_api
from a2a_workspace.gateway.agents_cli_compat import create_agents_cli_compat_router

# Domain error -> HTTP status. IntegrityError is a 500: it means stored bytes did
# not verify, which is a server-side trust failure, not a client mistake.
_STATUS_BY_ERROR = {
    AuthorizationError: 401,
    IsolationError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    IntegrityError: 500,
}


def create_app(config: Config | None = None, *, container: Container | None = None) -> FastAPI:
    cfg = config or Config.from_env()
    ctr = container or build_container(cfg)

    app = FastAPI(
        title="Antigravity A2A/A2UI Workspace Gateway",
        version="0.1.0",
        summary="Shared, stateless control plane for per-user Antigravity skill workspaces.",
    )
    app.state.container = ctr

    @app.exception_handler(WorkspaceError)
    async def _handle_workspace_error(_request: Request, exc: WorkspaceError):
        status = next(
            (code for typ, code in _STATUS_BY_ERROR.items() if isinstance(exc, typ)),
            400,
        )
        # Never echo the exception message for integrity failures; it could carry
        # internal digests. Everything else returns its message to aid the client.
        detail = "integrity verification failed" if status == 500 else str(exc)
        return JSONResponse(status_code=status, content={"detail": detail})

    app.include_router(a2a.router)
    app.include_router(registry_api.router)
    app.include_router(enterprise_api.router)
    # agents-cli / A2A compatibility: re-serve the same agent card at the path
    # `agents-cli publish gemini-enterprise --registration-type a2a` fetches.
    # `build_card` is the single source of card content (see gateway/a2a.py).
    app.include_router(
        create_agents_cli_compat_router(
            card_provider=lambda: a2a.build_card(ctr),
            base_url=cfg.public_url,
        )
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "org": cfg.organization, "env": cfg.environment}

    return app
