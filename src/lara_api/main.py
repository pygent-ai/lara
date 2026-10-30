from __future__ import annotations

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lara-api", description="Run the Lara local FastAPI service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--agent", dest="agent_alias", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--web-dist",
        default=None,
        help="构建产物目录（如 apps/desktop/dist），提供后同源托管前端页面供浏览器访问。",
    )
    args = parser.parse_args(argv)

    import uvicorn

    from lara_api.app import create_app

    app = create_app(
        workspace_root=args.workspace_root,
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
        web_dist=args.web_dist,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
