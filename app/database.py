from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, func
from datetime import datetime
from typing import AsyncGenerator

from app.config import settings


# Tạo async engine kết nối tới PostgreSQL hoặc SQLite thông qua DATABASE_URL
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,  # Kiểm tra kết nối trước khi sử dụng từ pool
    pool_recycle=3600,   # Tái sử dụng kết nối sau 1 giờ để tránh timeout
)

# Session factory cho async session — expire_on_commit=False để tránh lazy-load sau commit
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Base declarative class cho tất cả SQLAlchemy ORM models."""

    # Các cột audit chung cho tất cả bảng
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function cung cấp async database session cho FastAPI routes.
    Session được tự động đóng sau khi request hoàn thành hoặc xảy ra lỗi.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            # Rollback toàn bộ thay đổi nếu có lỗi xảy ra trong quá trình xử lý request
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Khởi tạo toàn bộ schema database dựa trên các ORM models đã định nghĩa.
    Chỉ sử dụng trong môi trường development; production dùng Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """
    Giải phóng tất cả kết nối trong connection pool khi ứng dụng tắt.
    Được gọi trong lifespan shutdown event của FastAPI.
    """
    await engine.dispose()