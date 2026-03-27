from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


__all__ = ["User", "UserProfile"]


class User(Base):
    """SQLAlchemy model đại diện cho tài khoản người dùng trong hệ thống."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)

    # Email dùng để đăng nhập, phải là duy nhất trong hệ thống
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    # cognito_sub là unique identifier từ AWS Cognito User Pools
    # Dùng để ánh xạ tài khoản local với Cognito identity
    cognito_sub: Mapped[Optional[str]] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
        index=True,
    )

    # Trạng thái kích hoạt tài khoản sau khi xác minh email
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Cờ xác định tài khoản có được xác minh email hay chưa
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Tài khoản admin hay không — dùng cho các thao tác quản trị nội bộ
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Thời điểm tạo tài khoản, tự động set bởi server
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Thời điểm cập nhật cuối, tự động cập nhật khi có thay đổi
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Quan hệ one-to-one với UserProfile — cascade delete khi xóa user
    profile: Mapped[Optional["UserProfile"]] = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} is_active={self.is_active}>"


class UserProfile(Base):
    """SQLAlchemy model lưu thông tin hiển thị công khai của người dùng.

    Tách biệt khỏi User để dễ mở rộng các trường profile mà không ảnh hưởng
    đến bảng users cốt lõi.
    """

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)

    # Khóa ngoại liên kết với bảng users, xóa profile khi user bị xóa
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Tên hiển thị của người dùng trong ứng dụng (họ tên đầy đủ hoặc nickname)
    display_name: Mapped[Optional[str]] = mapped_column(
        String(150),
        nullable=True,
    )

    # URL ảnh đại diện lưu trên S3, được generate qua pre-signed URL khi upload
    avatar_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Tiểu sử ngắn, tùy chọn điền
    bio: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Múi giờ của người dùng, dùng để hiển thị deadline và nhắc nhở đúng local time
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="UTC",
        nullable=False,
    )

    # Ngôn ngữ giao diện ưu thích của người dùng (vi / en)
    locale: Mapped[str] = mapped_column(
        String(10),
        default="vi",
        nullable=False,
    )

    # Thời điểm tạo profile
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Thời điểm cập nhật profile cuối cùng
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Quan hệ ngược lại với User
    user: Mapped["User"] = relationship(
        "User",
        back_populates="profile",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<UserProfile id={self.id} user_id={self.user_id} "
            f"display_name={self.display_name!r}>"
        )