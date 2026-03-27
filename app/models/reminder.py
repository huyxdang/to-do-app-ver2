from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.task import Task

__all__ = ["Reminder", "ReminderType", "ReminderChannel", "ReminderStatus"]


# Loại nhắc nhở: một lần hoặc lặp lại định kỳ
class ReminderType(str, PyEnum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"


# Kênh gửi thông báo: push notification, email, hoặc cả hai
class ReminderChannel(str, PyEnum):
    PUSH = "push"
    EMAIL = "email"
    BOTH = "both"


# Trạng thái nhắc nhở trong vòng đời
class ReminderStatus(str, PyEnum):
    ACTIVE = "active"       # Đang chờ gửi
    SENT = "sent"           # Đã gửi thành công
    FAILED = "failed"       # Gửi thất bại
    CANCELLED = "cancelled" # Đã hủy (task hoàn thành hoặc xóa)
    SNOOZED = "snoozed"     # Người dùng đã hoãn


class Reminder(Base):
    """
    Model nhắc nhở cho task.

    Hỗ trợ hai loại:
    - ONE_TIME: Nhắc một lần tại scheduled_at (bắt buộc có deadline trong task)
    - RECURRING: Nhắc định kỳ theo cron_expression (không cần deadline)

    Kênh gửi: push notification (SNS/FCM/APNs), email (SES), hoặc cả hai.
    Mỗi task chỉ được tạo tối đa 3 nhắc nhở (enforced ở service layer).
    """

    __tablename__ = "reminders"

    # --- Khóa chính ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

    # --- Liên kết task ---
    # Nhắc nhở thuộc về task nào; cascade xóa khi task bị hard delete
    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Phân loại nhắc nhở ---
    # Loại: one_time hoặc recurring
    reminder_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReminderType.ONE_TIME.value,
    )

    # Kênh gửi: push, email, hoặc both
    channel: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=ReminderChannel.PUSH.value,
    )

    # --- Thời gian cho nhắc nhở ONE_TIME ---
    # Thời điểm cụ thể khi cần gửi nhắc nhở (UTC); bắt buộc với one_time
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Số phút trước deadline để nhắc (ví dụ: 60 = 1 giờ trước, 1440 = 1 ngày trước)
    # Lưu để tái tính lại khi deadline task thay đổi
    minutes_before_deadline: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # --- Thông tin lịch lặp cho RECURRING ---
    # Biểu thức cron AWS EventBridge (ví dụ: "cron(0 9 * * ? *)" = 9h sáng mỗi ngày)
    cron_expression: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Mô tả lịch lặp dễ đọc (ví dụ: "Hàng ngày lúc 09:00", "Thứ Hai hàng tuần lúc 08:30")
    schedule_description: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # Giờ trong ngày để gửi nhắc (0-23), dùng cho recurring daily/weekly/monthly
    hour_of_day: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Phút trong giờ để gửi nhắc (0-59), dùng cho recurring
    minute_of_hour: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Ngày trong tuần để gửi (0=Chủ nhật, 1=Thứ Hai, ..., 6=Thứ Bảy), cho weekly recurring
    day_of_week: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # Ngày trong tháng để gửi (1-31), cho monthly recurring
    day_of_month: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )

    # --- Tích hợp AWS EventBridge Scheduler ---
    # Tên schedule trên EventBridge (unique per reminder) để quản lý vòng đời
    eventbridge_schedule_name: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
        unique=True,
    )

    # ARN của EventBridge schedule (dùng để xóa/cập nhật)
    eventbridge_schedule_arn: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )

    # --- Trạng thái nhắc nhở ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReminderStatus.ACTIVE.value,
        index=True,
    )

    # --- Thông tin gửi thông báo ---
    # Thời điểm thực tế nhắc nhở được gửi
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Số lần thử gửi (dùng cho retry logic)
    delivery_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Log lỗi cuối cùng nếu gửi thất bại
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Delivery status từ SNS/SES (success, failed, pending)
    delivery_status: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Message ID trả về từ SNS hoặc SES để tracking
    provider_message_id: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )

    # --- Cờ tuỳ chọn ---
    # Nhắc nhở tùy chỉnh (không phải preset 1h/1 ngày trước)
    is_custom: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Cho phép fallback sang email nếu push không gửi được
    email_fallback_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Đánh dấu soft-delete (nhắc nhở bị hủy nhưng giữ record để audit)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # --- Relationships ---
    task: Mapped["Task"] = relationship(
        "Task",
        back_populates="reminders",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Reminder id={self.id} task_id={self.task_id} "
            f"type={self.reminder_type} channel={self.channel} "
            f"status={self.status}>"
        )

    @property
    def is_recurring(self) -> bool:
        """Kiểm tra nhắc nhở có phải loại lặp lại không."""
        return self.reminder_type == ReminderType.RECURRING.value

    @property
    def is_one_time(self) -> bool:
        """Kiểm tra nhắc nhở có phải loại một lần không."""
        return self.reminder_type == ReminderType.ONE_TIME.value

    @property
    def is_active(self) -> bool:
        """Kiểm tra nhắc nhở có đang hoạt động không."""
        return self.status == ReminderStatus.ACTIVE.value and not self.is_deleted

    @property
    def sends_push(self) -> bool:
        """Kiểm tra nhắc nhở có gửi push notification không."""
        return self.channel in (ReminderChannel.PUSH.value, ReminderChannel.BOTH.value)

    @property
    def sends_email(self) -> bool:
        """Kiểm tra nhắc nhở có gửi email không."""
        return self.channel in (ReminderChannel.EMAIL.value, ReminderChannel.BOTH.value)