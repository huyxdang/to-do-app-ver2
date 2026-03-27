import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.reminder import Reminder
    from app.models.user import User


__all__ = ["TaskStatus", "TaskPriority", "Task", "Category"]


# Enum trạng thái công việc
class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    OVERDUE = "OVERDUE"
    COMPLETED = "COMPLETED"


# Enum mức độ ưu tiên công việc
class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Category(Base):
    """Danh mục công việc do người dùng tạo."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

    # Mã người dùng sở hữu danh mục
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Tên danh mục, tối đa 100 ký tự, không trùng lặp trong cùng user
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Màu sắc đại diện cho danh mục (hex color code, ví dụ: #FF5733)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#6B7280")

    # Cờ đánh dấu danh mục mặc định "Uncategorized" không thể xóa
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Thời điểm tạo danh mục
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Thời điểm cập nhật danh mục lần cuối
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Soft-delete: đánh dấu đã xóa thay vì xóa vật lý
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Thời điểm xóa mềm
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Quan hệ ngược với User
    owner: Mapped["User"] = relationship("User", back_populates="categories")

    # Danh sách task thuộc danh mục này
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="category", foreign_keys="Task.category_id"
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r} user_id={self.user_id}>"


class Task(Base):
    """Công việc cá nhân của người dùng."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

    # Mã người dùng sở hữu task
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Tiêu đề task, bắt buộc, tối đa 255 ký tự
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # Mô tả chi tiết task, tuỳ chọn, tối đa 5000 ký tự
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Trạng thái task: PENDING / OVERDUE / COMPLETED
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", create_type=True),
        nullable=False,
        default=TaskStatus.PENDING,
        index=True,
    )

    # Mức độ ưu tiên: LOW / MEDIUM / HIGH, mặc định MEDIUM
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority", create_type=True),
        nullable=False,
        default=TaskPriority.MEDIUM,
        index=True,
    )

    # Deadline của task, phải là thời điểm trong tương lai khi tạo
    deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Khoá ngoại trỏ đến danh mục; NULL nghĩa là chưa gán danh mục
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Thời điểm đánh dấu hoàn thành
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Soft-delete: đánh dấu đã xóa thay vì xóa vật lý, hỗ trợ tính năng hoàn tác
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    # Thời điểm xóa mềm, dùng để tính TTL cho tính năng hoàn tác 5 giây
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Thời điểm tạo task
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Thời điểm cập nhật task lần cuối
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Quan hệ ngược với User
    owner: Mapped["User"] = relationship("User", back_populates="tasks")

    # Quan hệ với danh mục
    category: Mapped[Optional["Category"]] = relationship(
        "Category", back_populates="tasks", foreign_keys=[category_id]
    )

    # Danh sách nhắc nhở được gắn với task này
    reminders: Mapped[List["Reminder"]] = relationship(
        "Reminder",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Task id={self.id} title={self.title!r} "
            f"status={self.status} user_id={self.user_id}>"
        )