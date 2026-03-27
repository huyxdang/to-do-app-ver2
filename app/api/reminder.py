from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.reminder import (
    ReminderCreate,
    ReminderResponse,
    ReminderUpdate,
    TriggerNotificationRequest,
    TriggerNotificationResponse,
)
from app.services.reminder import ReminderService
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["router"]

router = APIRouter(prefix="/tasks/{task_id}/reminders", tags=["reminders"])

# ------------------------------------------------------------------ #
#  Helper: khởi tạo ReminderService với DB session                   #
# ------------------------------------------------------------------ #


def get_reminder_service(db: AsyncSession = Depends(get_db)) -> ReminderService:
    return ReminderService(db)


# ------------------------------------------------------------------ #
#  GET /tasks/{task_id}/reminders                                     #
# ------------------------------------------------------------------ #


@router.get(
    "",
    response_model=list[ReminderResponse],
    status_code=status.HTTP_200_OK,
    summary="Liệt kê tất cả nhắc nhở của một task",
)
async def list_reminders(
    task_id: Annotated[int, Path(description="ID của task cần xem nhắc nhở")],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ReminderService, Depends(get_reminder_service)],
) -> list[ReminderResponse]:
    """
    Trả về danh sách tất cả nhắc nhở thuộc task_id.
    Chỉ owner của task mới có quyền xem.
    """
    reminders = await service.list_reminders(
        task_id=task_id,
        user_id=current_user.id,
    )
    return reminders


# ------------------------------------------------------------------ #
#  POST /tasks/{task_id}/reminders                                    #
# ------------------------------------------------------------------ #


@router.post(
    "",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo nhắc nhở mới cho task",
)
async def create_reminder(
    task_id: Annotated[int, Path(description="ID của task cần thêm nhắc nhở")],
    payload: ReminderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ReminderService, Depends(get_reminder_service)],
) -> ReminderResponse:
    """
    Tạo một nhắc nhở mới (one-time hoặc recurring) cho task.
    - Task phải thuộc về current_user.
    - Một task có tối đa 3 nhắc nhở.
    - Nhắc nhở one-time yêu cầu task phải có deadline.
    - Sau khi tạo thành công, lên lịch EventBridge Scheduler.
    """
    reminder = await service.create_reminder(
        task_id=task_id,
        user_id=current_user.id,
        payload=payload,
    )
    return reminder


# ------------------------------------------------------------------ #
#  PUT /tasks/{task_id}/reminders/{reminder_id}                      #
# ------------------------------------------------------------------ #


@router.put(
    "/{reminder_id}",
    response_model=ReminderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cập nhật nhắc nhở",
)
async def update_reminder(
    task_id: Annotated[int, Path(description="ID của task chứa nhắc nhở")],
    reminder_id: Annotated[int, Path(description="ID của nhắc nhở cần cập nhật")],
    payload: ReminderUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ReminderService, Depends(get_reminder_service)],
) -> ReminderResponse:
    """
    Cập nhật cấu hình của một nhắc nhở hiện có.
    - Chỉ owner của task mới được phép.
    - Nếu thời gian/lịch thay đổi, EventBridge Scheduler được cập nhật tương ứng.
    """
    reminder = await service.update_reminder(
        task_id=task_id,
        reminder_id=reminder_id,
        user_id=current_user.id,
        payload=payload,
    )
    return reminder


# ------------------------------------------------------------------ #
#  DELETE /tasks/{task_id}/reminders/{reminder_id}                   #
# ------------------------------------------------------------------ #


@router.delete(
    "/{reminder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xóa nhắc nhở",
)
async def delete_reminder(
    task_id: Annotated[int, Path(description="ID của task chứa nhắc nhở")],
    reminder_id: Annotated[int, Path(description="ID của nhắc nhở cần xóa")],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ReminderService, Depends(get_reminder_service)],
) -> None:
    """
    Xóa nhắc nhở và hủy lịch tương ứng trên EventBridge Scheduler.
    - Chỉ owner của task mới được phép.
    """
    await service.delete_reminder(
        task_id=task_id,
        reminder_id=reminder_id,
        user_id=current_user.id,
    )


# ------------------------------------------------------------------ #
#  POST /tasks/{task_id}/reminders/{reminder_id}/trigger             #
# ------------------------------------------------------------------ #


@router.post(
    "/{reminder_id}/trigger",
    response_model=TriggerNotificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Kích hoạt gửi thông báo ngay lập tức cho nhắc nhở",
)
async def trigger_notification(
    task_id: Annotated[int, Path(description="ID của task chứa nhắc nhở")],
    reminder_id: Annotated[int, Path(description="ID của nhắc nhở cần kích hoạt")],
    payload: TriggerNotificationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ReminderService, Depends(get_reminder_service)],
) -> TriggerNotificationResponse:
    """
    Endpoint dùng để kiểm thử hoặc kích hoạt thủ công việc gửi thông báo
    cho một nhắc nhở cụ thể ngay lập tức.

    - Kiểm tra task chưa COMPLETED trước khi gửi.
    - Gọi NotificationService để gửi push và/hoặc email theo cấu hình kênh.
    - Ghi log kết quả delivery.
    - Trả về trạng thái gửi thành công hay thất bại cùng chi tiết.
    """
    result = await service.trigger_notification(
        task_id=task_id,
        reminder_id=reminder_id,
        user_id=current_user.id,
        payload=payload,
    )
    return result