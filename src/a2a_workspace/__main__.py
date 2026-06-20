"""Entry point: ``python -m a2a_workspace`` / ``a2a-workspace``.

Subcommands:

* ``serve`` (default) — run the gateway with uvicorn. Config from the environment
  (see :class:`a2a_workspace.config.Config`).
* ``gen-enterprise-skill <dir>`` — write the Gemini Enterprise connectors skill
  bundle to a directory, ready to drop into ``skills_paths`` or publish.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _serve() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "a2a_workspace.gateway.app:create_app",
        host=host,
        port=port,
        factory=True,
    )


def _gen_enterprise_skill(dest: str) -> None:
    from a2a_workspace.gemini_enterprise.skill import generate_skill_bundle

    root = Path(dest)
    for rel, data in generate_skill_bundle().items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"wrote {target}")
    print(f"\nSkill bundle ready in {root}. Point skills_paths at it, or publish it")
    print("through the Workspace API (POST /workspaces/me/drafts ...).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="a2a-workspace")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the A2A/A2UI gateway (default)")
    gen = sub.add_parser(
        "gen-enterprise-skill", help="emit the Gemini Enterprise connectors skill"
    )
    gen.add_argument("dest", help="directory to write the skill bundle into")

    args = parser.parse_args()
    if args.command == "gen-enterprise-skill":
        _gen_enterprise_skill(args.dest)
    else:
        _serve()


if __name__ == "__main__":
    sys.exit(main())
