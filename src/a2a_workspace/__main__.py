"""Entry point: ``python -m a2a_workspace`` / ``a2a-workspace``.

Runs the gateway with uvicorn. Configuration comes from the environment (see
:class:`a2a_workspace.config.Config`).
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "a2a_workspace.gateway.app:create_app",
        host=host,
        port=port,
        factory=True,
    )


if __name__ == "__main__":
    main()
