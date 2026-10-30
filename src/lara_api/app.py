from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.metadata import version as package_version
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from lara_api.container import ApiContext
from lara_api.routers import (
    automations,
    chat,
    health,
    projects,
    runtime,
    sessions,
    settings,
    terminal,
    tool_results,
    traces,
    uploads,
    workspace,
)
from lara_api.services.chat_runner import ChatRunRegistry
from lara_api.services.request_timing import RequestTimingMiddleware


def create_app(
    *,
    workspace_root: str | None = None,
    agent_alias: str | None = None,
    max_steps: int | None = None,
    web_dist: str | Path | None = None,
) -> FastAPI:
    context = ApiContext(
        workspace_root=workspace_root,
        agent_alias=agent_alias,
        max_steps=max_steps,
    )
    context.attach_chat_registry(ChatRunRegistry(context.session_coordinator))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        lease = None
        try:
            lease = await context.acquire_runtime()
            await lease.release()
            lease = None
            context.start_dispatch_scheduler()
            yield
        finally:
            if lease is not None:
                await lease.release()
            await context.aclose()

    app = FastAPI(
        title="Lara Local API", version=package_version("lara"), lifespan=lifespan
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestTimingMiddleware)
    app.state.api_context = context
    app.include_router(health.router)
    app.include_router(automations.router)
    app.include_router(projects.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(runtime.router)
    app.include_router(tool_results.router)
    app.include_router(traces.router)
    app.include_router(uploads.router)
    app.include_router(settings.router)
    app.include_router(workspace.router)
    app.include_router(terminal.router)
    if web_dist is not None:
        # 注册在所有 API 路由之后，同源托管构建好的 renderer（远程浏览器访问）；
        # API 路径优先匹配，不受静态挂载影响。
        dist_path = Path(web_dist)
        if not dist_path.is_dir():
            raise RuntimeError(f"web_dist 目录不存在: {dist_path}")
        app.mount("/", StaticFiles(directory=dist_path, html=True), name="web")
    return app
