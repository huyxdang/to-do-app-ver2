from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator


class ReminderChannel(str, Enum):
    """Kênh gửi thông báo nhắc nhở"""
    PUSH = "push"
    EMAIL = "email"


class ReminderType(str, Enum):
    """Loại nhắc nhở: một lần hoặc lặp lại"""
    ONE_TIME = "one_time"
    RECURRING = "recurring"


class RecurringPattern(str, Enum):
    """Mô hình lặp lại cho nhắc nhở định kỳ"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReminderChannelSelection(BaseModel):
    """Lựa chọn kênh gửi thông báo cho một nhắc nhở"""
    push: bool = False
    email: bool = False

    @field_validator("push", "email", mode="before")
    @classmethod
    def ensure_bool(cls, v: object) -> bool:
        """Đảm bảo các giá trị là boolean"""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    model_config = ConfigDict(from_attributes=True)


class ReminderCreateOneTime(BaseModel):
    """Schema tạo nhắc nhở một lần"""
    reminder_time: datetime = Field(
        ...,
        description="Thời điểm gửi nhắc nhở (phải trong tương lai)"
    )
    channels: ReminderChannelSelection = Field(
        default_factory=lambda: ReminderChannelSelection(push=True, email=False),
        description="Kênh gửi thông báo"
    )

    @field_validator("reminder_time")
    @classmethod
    def validate_reminder_time(cls, v: datetime) -> datetime:
        """Kiểm tra thời điểm nhắc nhở phải trong tương lai"""
        now = datetime.utcnow()
        if v <= now:
            raise ValueError("reminder_time must be in the future")
        return v

    model_config = ConfigDict(from_attributes=True)


class ReminderCreateRecurring(BaseModel):
    """Schema tạo nhắc nhở lặp lại"""
    pattern: RecurringPattern = Field(
        ...,
        description="Mô hình lặp lại: daily, weekly, hoặc monthly"
    )
    hour: int = Field(
        default=9,
        ge=0,
        le=23,
        description="Giờ trong ngày để gửi nhắc nhở (0-23)"
    )
    minute: int = Field(
        default=0,
        ge=0,
        le=59,
        description="Phút trong giờ để gửi nhắc nhở (0-59)"
    )
    day_of_week: Optional[int] = Field(
        default=None,
        ge=0,
        le=6,
        description="Ngày trong tuần (0=Thứ 2, 6=Chủ nhật), bắt buộc nếu pattern là weekly"
    )
    day_of_month: Optional[int] = Field(
        default=None,
        ge=1,
        le=31,
        description="Ngày trong tháng (1-31), bắt buộc nếu pattern là monthly"
    )
    channels: ReminderChannelSelection = Field(
        default_factory=lambda: ReminderChannelSelection(push=True, email=False),
        description="Kênh gửi thông báo"
    )

    @field_validator("day_of_week", mode="before")
    @classmethod
    def validate_day_of_week_required_for_weekly(cls, v: object, info) -> Optional[int]:
        """Kiểm tra day_of_week bắt buộc khi pattern là weekly"""
        if info.data.get("pattern") == RecurringPattern.WEEKLY and v is None:
            raise ValueError("day_of_week is required when pattern is weekly")
        return v

    @field_validator("day_of_month", mode="before")
    @classmethod
    def validate_day_of_month_required_for_monthly(cls, v: object, info) -> Optional[int]:
        """Kiểm tra day_of_month bắt buộc khi pattern là monthly"""
        if info.data.get("pattern") == RecurringPattern.MONTHLY and v is None:
            raise ValueError("day_of_month is required when pattern is monthly")
        return v

    model_config = ConfigDict(from_attributes=True)


class ReminderUpdate(BaseModel):
    """Schema cập nhật nhắc nhở"""
    reminder_time: Optional[datetime] = Field(
        default=None,
        description="Thời điểm nhắc nhở mới (chỉ cho loại one-time)"
    )
    pattern: Optional[RecurringPattern] = Field(
        default=None,
        description="Mô hình lặp lại mới (chỉ cho loại recurring)"
    )
    hour: Optional[int] = Field(
        default=None,
        ge=0,
        le=23,
        description="Giờ mới (chỉ cho loại recurring)"
    )
    minute: Optional[int] = Field(
        default=None,
        ge=0,
        le=59,
        description="Phút mới (chỉ cho loại recurring)"
    )
    day_of_week: Optional[int] = Field(
        default=None,
        ge=0,
        le=6,
        description="Ngày trong tuần mới (chỉ cho loại recurring với pattern weekly)"
    )
    day_of_month: Optional[int] = Field(
        default=None,
        ge=1,
        le=31,
        description="Ngày trong tháng mới (chỉ cho loại recurring với pattern monthly)"
    )
    channels: Optional[ReminderChannelSelection] = Field(
        default=None,
        description="Kênh gửi thông báo mới"
    )

    @field_validator("reminder_time")
    @classmethod
    def validate_reminder_time_update(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Kiểm tra thời điểm nhắc nhở cập nhật phải trong tương lai nếu được cung cấp"""
        if v is not None:
            now = datetime.utcnow()
            if v <= now:
                raise ValueError("reminder_time must be in the future")
        return v

    model_config = ConfigDict(from_attributes=True)


class ReminderResponse(BaseModel):
    """Schema phản hồi chi tiết nhắc nhở"""
    id: int = Field(..., description="ID nhắc nhở")
    task_id: int = Field(..., description="ID của task liên quan")
    reminder_type: ReminderType = Field(..., description="Loại nhắc nhở")
    reminder_time: Optional[datetime] = Field(
        default=None,
        description="Thời điểm gửi (chỉ cho one-time)"
    )
    pattern: Optional[RecurringPattern] = Field(
        default=None,
        description="Mô hình lặp lại (chỉ cho recurring)"
    )
    hour: Optional[int] = Field(
        default=None,
        description="Giờ trong ngày (chỉ cho recurring)"
    )
    minute: Optional[int] = Field(
        default=None,
        description="Phút trong giờ (chỉ cho recurring)"
    )
    day_of_week: Optional[int] = Field(
        default=None,
        description="Ngày trong tuần (chỉ cho recurring weekly)"
    )
    day_of_month: Optional[int] = Field(
        default=None,
        description="Ngày trong tháng (chỉ cho recurring monthly)"
    )
    cron_expression: Optional[str] = Field(
        default=None,
        description="Biểu thức cron cho EventBridge Scheduler"
    )
    channels: ReminderChannelSelection = Field(
        ...,
        description="Kênh gửi thông báo"
    )
    is_active: bool = Field(
        default=True,
        description="Nhắc nhở có đang hoạt động không"
    )
    created_at: datetime = Field(..., description="Thời điểm tạo")
    updated_at: datetime = Field(..., description="Thời điểm cập nhật cuối cùng")
    last_sent_at: Optional[datetime] = Field(
        default=None,
        description="Thời điểm gửi lần cuối (cho recurring)"
    )

    model_config = ConfigDict(from_attributes=True)


class ReminderListResponse(BaseModel):
    """Schema danh sách nhắc nhở cho một task"""
    task_id: int = Field(..., description="ID của task")
    reminders: list[ReminderResponse] = Field(
        default_factory=list,
        description="Danh sách nhắc nhở"
    )
    count: int = Field(
        default=0,
        description="Số lượng nhắc nhở"
    )

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "ReminderChannel",
    "ReminderType",
    "RecurringPattern",
    "ReminderChannelSelection",
    "ReminderCreateOneTime",
    "ReminderCreateRecurring",
    "ReminderUpdate",
    "ReminderResponse",
    "ReminderListResponse",
]