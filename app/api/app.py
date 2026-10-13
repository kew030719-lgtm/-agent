import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pymilvus.exceptions import MilvusException

from app.agent.service import AssistantService
from app.api import admin_routes, auth_routes, chat_routes, expense_routes, ticket_routes, leave_routes, knowledge_routes, user_routes, work_center_routes, ingestion_routes
from app.api.database import initialize
from app.auth import connect
from app.config import PROJECT_ROOT, configured_database_path, load_config, model_data_root
from app.errors import ServiceUnavailableError


def create_app(database_path: Path | None = None) -> FastAPI:
    load_config()
    path = configured_database_path(database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        with closing(connect(path)) as db:
            initialize(db)
        try:
            yield
        finally:
            application.state.assistant.close()

    application = FastAPI(title="企业员工助手 API", version="1.0.0", lifespan=lifespan)
    application.state.database_path = path
    application.state.data_root = path.parent
    application.state.assistant = AssistantService(model_data_root(path.parent))
    origins = [item.strip() for item in os.getenv(
        "API_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000",
    ).split(",") if item.strip()]
    application.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Requested-With"],
    )

    @application.middleware("http")
    async def protect_mutations(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path.startswith("/api") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-requested-with") != "employee-assistant" or (origin and origin not in origins):
                return JSONResponse({"detail": "请求来源校验失败"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 不返回 Pydantic 的 input 字段，避免密码等输入被回显。
        return JSONResponse({"detail": "请求参数无效，请检查输入内容"}, status_code=422)

    @application.exception_handler(PermissionError)
    async def permission_error(request: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @application.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "参数或文件内容无效，请检查后重试"}, status_code=400)

    async def service_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": "服务暂时不可用，请检查后端配置后重试"}, status_code=503)

    for error_type in (ServiceUnavailableError, MilvusException, RuntimeError, OSError):
        application.add_exception_handler(error_type, service_error)

    marker = APIKeyHeader(
        name="X-Requested-With", scheme_name="RequestMarker", auto_error=False,
        description="填写 employee-assistant；登录后浏览器会自动携带会话 Cookie。",
    )
    for router in (auth_routes.router, chat_routes.router, expense_routes.router, ticket_routes.router, leave_routes.router, knowledge_routes.router, admin_routes.router, user_routes.router, work_center_routes.router, ingestion_routes.router):
        application.include_router(router, prefix="/api", dependencies=[Depends(marker)])

    @application.get("/api/health", tags=["服务状态"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    frontend = PROJECT_ROOT / "frontend" / "dist"
    if (frontend / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @application.get("/", include_in_schema=False)
    def index() -> Response:
        if not (frontend / "index.html").is_file():
            return JSONResponse({"detail": "前端尚未构建，请在 frontend 目录运行 npm run build"}, status_code=503)
        return FileResponse(frontend / "index.html")

    return application


app = create_app()
