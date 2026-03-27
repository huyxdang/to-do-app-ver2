import contextlib
import logging
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine

# Import tất cả models để Alembic/SQLAlchemy nhận diện schema
from app.models import task, reminder, user  # noqa: F401

# Import routers
from app.auth.router import router as auth_router
from app.api.user import router as user_router
from app.api.task import router as task_router
from app.api.category import router as category_router
from app.api.reminder import router as reminder_router
from app.api.dashboard import router as dashboard_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager: khởi tạo tài nguyên khi app start,
    dọn dẹp khi app shutdown.
    """
    logger.info("TaskFlow API đang khởi động...")

    # Tạo tất cả bảng trong DB nếu chưa tồn tại (dùng cho dev/SQLite)
    # Trong production, nên dùng Alembic migrations thay thế
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema đã được khởi tạo.")

    yield

    # Đóng kết nối database khi shutdown
    await engine.dispose()
    logger.info("TaskFlow API đã tắt. Tài nguyên đã được giải phóng.")


def create_application() -> FastAPI:
    """Khởi tạo và cấu hình FastAPI application."""

    application = FastAPI(
        title="TaskFlow API",
        description=(
            "Ứng dụng Quản lý Công việc Cá nhân — TaskFlow.\n\n"
            "Hỗ trợ quản lý task, danh mục, nhắc nhở, thông báo và thống kê cá nhân."
        ),
        version="1.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── CORS Middleware ──────────────────────────────────────────────────────
    # Cho phép frontend (web/mobile) gọi API từ các origin được cấu hình
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ──────────────────────────────────────────────────────────────
    # Auth: đăng ký, xác minh email, đăng nhập, reset mật khẩu
    application.include_router(
        auth_router,
        prefix="/api/v1/auth",
        tags=["Authentication"],
    )

    # User: xem/cập nhật profile, upload avatar
    application.include_router(
        user_router,
        prefix="/api/v1/users",
        tags=["User Profile"],
    )

    # Task: CRUD task, lọc/sắp xếp, tìm kiếm, đánh dấu hoàn thành, xóa mềm
    application.include_router(
        task_router,
        prefix="/api/v1/tasks",
        tags=["Tasks"],
    )

    # Category: quản lý danh mục, xem số lượng task, xóa với reassign
    application.include_router(
        category_router,
        prefix="/api/v1/categories",
        tags=["Categories"],
    )

    # Reminder: cài đặt nhắc nhở (một lần / lặp lại) per task
    application.include_router(
        reminder_router,
        prefix="/api/v1/tasks",
        tags=["Reminders"],
    )

    # Dashboard: tổng quan, thống kê cá nhân, streak
    application.include_router(
        dashboard_router,
        prefix="/api/v1",
        tags=["Dashboard & Statistics"],
    )

    return application


app: FastAPI = create_application()


@app.get("/", tags=["Health Check"])
async def root() -> dict[str, str]:
    """Health check endpoint — kiểm tra API đang hoạt động."""
    return {
        "service": "TaskFlow API",
        "version": "1.1.0",
        "status": "healthy",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health Check"])
async def health_check() -> dict[str, str]:
    """Health check chi tiết cho load balancer / container orchestration."""
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
    }