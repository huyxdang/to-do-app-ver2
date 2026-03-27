from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class TaskStatus(str, Enum):
    """Task status enumeration — Trạng thái công việc"""
    PENDING = "PENDING"
    OVERDUE = "OVERDUE"
    COMPLETED = "COMPLETED"


class TaskPriority(str, Enum):
    """Task priority enumeration — Mức độ ưu tiên công việc"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskCreateRequest(BaseModel):
    """Schema for creating a new task — Schema tạo công việc mới"""
    title: str = Field(..., min_length=1, max_length=255, description="Task title")
    description: Optional[str] = Field(None, max_length=5000, description="Task description")
    deadline: Optional[datetime] = Field(None, description="Task deadline (must be in future)")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="Task priority level")
    category_id: Optional[int] = Field(None, description="Category ID for this task")

    model_config = ConfigDict(from_attributes=True)


class TaskUpdateRequest(BaseModel):
    """Schema for updating a task — Schema cập nhật công việc"""
    title: Optional[str] = Field(None, min_length=1, max_length=255, description="Task title")
    description: Optional[str] = Field(None, max_length=5000, description="Task description")
    deadline: Optional[datetime] = Field(None, description="Task deadline (must be in future if set)")
    priority: Optional[TaskPriority] = Field(None, description="Task priority level")
    category_id: Optional[int] = Field(None, description="Category ID for this task")

    model_config = ConfigDict(from_attributes=True)


class TaskStatusToggleRequest(BaseModel):
    """Schema for toggling task status — Schema chuyển đổi trạng thái công việc"""
    status: TaskStatus = Field(..., description="New task status")

    model_config = ConfigDict(from_attributes=True)


class TaskFilterParams(BaseModel):
    """Schema for filtering and sorting tasks — Schema lọc và sắp xếp công việc"""
    status: Optional[TaskStatus] = Field(None, description="Filter by task status")
    priority: Optional[TaskPriority] = Field(None, description="Filter by priority level")
    category_id: Optional[int] = Field(None, description="Filter by category ID")
    deadline_from: Optional[datetime] = Field(None, description="Filter tasks with deadline from this date")
    deadline_to: Optional[datetime] = Field(None, description="Filter tasks with deadline until this date")
    sort_by: Optional[str] = Field(
        default="deadline",
        description="Sort field: deadline, priority, created_at, title"
    )
    sort_order: Optional[str] = Field(
        default="asc",
        description="Sort order: asc or desc"
    )
    skip: int = Field(default=0, ge=0, description="Number of records to skip")
    limit: int = Field(default=20, ge=1, le=100, description="Number of records to return")

    model_config = ConfigDict(from_attributes=True)


class TaskResponse(BaseModel):
    """Schema for task response — Schema trả về thông tin công việc"""
    id: int
    user_id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    priority: TaskPriority
    deadline: Optional[datetime]
    category_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskDetailResponse(BaseModel):
    """Schema for detailed task response with related data — Schema chi tiết công việc"""
    id: int
    user_id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    priority: TaskPriority
    deadline: Optional[datetime]
    category_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    reminders: list = Field(default_factory=list, description="List of reminders for this task")

    model_config = ConfigDict(from_attributes=True)


class TaskListResponse(BaseModel):
    """Schema for paginated task list response — Schema danh sách công việc"""
    total: int = Field(..., description="Total number of tasks matching the filter")
    skip: int = Field(..., description="Number of records skipped")
    limit: int = Field(..., description="Limit applied")
    items: list[TaskResponse] = Field(..., description="List of task items")

    model_config = ConfigDict(from_attributes=True)


class TaskGroupedListResponse(BaseModel):
    """Schema for tasks grouped by status — Schema công việc nhóm theo trạng thái"""
    pending: list[TaskResponse] = Field(default_factory=list, description="Tasks with PENDING status")
    overdue: list[TaskResponse] = Field(default_factory=list, description="Tasks with OVERDUE status")
    completed: list[TaskResponse] = Field(default_factory=list, description="Tasks with COMPLETED status")

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "TaskStatus",
    "TaskPriority",
    "TaskCreateRequest",
    "TaskUpdateRequest",
    "TaskStatusToggleRequest",
    "TaskFilterParams",
    "TaskResponse",
    "TaskDetailResponse",
    "TaskListResponse",
    "TaskGroupedListResponse",
]