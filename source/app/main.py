"""FastAPI 应用工厂（BM-V1-001）。

约定：
- 导入本模块（甚至调用 create_app）不会自动建库、迁移或访问网络；
- 数据库只通过 Alembic 迁移创建（BM-V1-102），应用启动不调用 create_all()；
- 生产环境关闭 /docs、/redoc 与 OpenAPI JSON（BM-V1-206 强化）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import app.models  # noqa: F401 - 注册模型元数据（应用侧仅用于一致性校验）
from app.config import APP_ROOT, Settings, load_settings
from app.database import create_engine_from_settings, make_session_manager
from app.errors import register_exception_handlers
from app.logging_setup import setup_logging
from app.middleware import RequestContextMiddleware
from app.security_middleware import make_security_middleware
from app.services.session_service import CsrfService, SessionService

logger = logging.getLogger("app.main")
TEMPLATES_DIR = APP_ROOT / "app" / "templates"
STATIC_DIR = APP_ROOT / "app" / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂。settings 未提供时从环境加载（生产缺失密钥会抛 ConfigError）。"""
    if settings is None:
        settings = load_settings()
    setup_logging(settings.log_level, fmt=settings.log_format)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 服务重启后 RUNNING 导入任务标记为 FAILED（BM-V1-601）
        try:
            with engine.begin() as conn:
                from sqlalchemy import text

                conn.execute(
                    text(
                        "UPDATE import_jobs SET status='FAILED', "
                        "error_summary='服务重启导致任务中断，请重新上传。' "
                        "WHERE status='RUNNING'"
                    )
                )
        except Exception:  # noqa: BLE001 - 启动不因清理失败而中断
            pass
        yield

    docs_enabled = settings.app_env != "production" and settings.allow_docs
    app = FastAPI(
        title="签栖",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # 数据库基础设施：Engine 惰性连接（不建库、不迁移）；Session 依赖挂到请求管线
    engine = create_engine_from_settings(settings)
    app.state.engine = engine
    app.state.session_manager = make_session_manager(engine)

    # 会话与 CSRF 服务（SESSION_SECRET 派生；配置缺失已在 Settings.load 拒绝）
    assert settings.session_secret is not None
    session_service = SessionService(settings.session_secret, settings.session_max_age_seconds)
    csrf_service = CsrfService(settings.session_secret, settings.csrf_token_ttl_seconds)
    app.state.session_service = session_service
    app.state.csrf_service = csrf_service

    # 模板与静态资源
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["app_settings"] = lambda: settings
    app.state.templates = templates
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # 中间件与异常处理
    app.add_middleware(RequestContextMiddleware)
    make_security_middleware(app, settings, session_service, csrf_service)
    register_exception_handlers(app)

    # 页面路由
    from app.routers import auth, bookmarks, categories, home, import_export, tags, trash

    app.include_router(home.router)
    app.include_router(auth.router)
    app.include_router(bookmarks.router)
    app.include_router(categories.router)
    app.include_router(tags.router)
    app.include_router(trash.router)
    app.include_router(import_export.router)

    @app.get("/health/live", tags=["ops"], include_in_schema=False)
    async def health_live() -> dict[str, str]:
        """进程存活探针：只证明进程在运行，不泄露配置、路径或数据库细节。"""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["ops"], include_in_schema=False)
    async def health_ready() -> Response:
        """就绪探针：数据库可达/可写/迁移版本一致；失败不泄露详情（BM-V1-804）。"""
        from fastapi.responses import JSONResponse

        from app.services.ops import check_ready

        result = check_ready(settings.database_url)
        if result["status"] != "ok":
            logger.error("readiness failed reason=%s", result["reason"])
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ok"})

    return app


def main() -> None:  # pragma: no cover - 本地开发入口
    import uvicorn

    settings = load_settings()
    setup_logging(settings.log_level, fmt=settings.log_format)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
