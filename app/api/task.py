from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, PaginationParams
from app.models.user import User
from app.schemas.task import (
    TaskCreate,
    TaskUpdate,
    TaskResponse,
    TaskListResponse,
    TaskFilterParams,
    TaskStatusToggleResponse,
)
from app.services.task import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(db: AsyncSession = Depends(get_db)) -> TaskService:
    """Khởi tạo TaskService với database session."""
    return TaskService(db)


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo task mới",
)
async def create_task(
    payload: TaskCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    """
    Tạo một task mới cho người dùng hiện tại.
    - Tiêu đề bắt buộc, tối đa 255 ký tự
    - Mô tả tùy chọn, tối đa 5000 ký tự
    - Deadline phải là thời điểm trong tương lai
    - Ưu tiên mặc định là MEDIUM nếu không truyền
    - Task mới luôn có trạng thái PENDING
    """
    task = await service.create_task(user_id=current_user.id, payload=payload)
    return task


@router.get(
    "",
    response_model=TaskListResponse,
    summary="Lấy danh sách task với filter và sort",
)
async def list_tasks(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
    pagination: Annotated[PaginationParams, Depends()],
    # Filter params
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Lọc theo trạng thái: PENDING, OVERDUE, COMPLETED",
    ),
    priority: str | None = Query(
        default=None,
        description="Lọc theo ưu tiên: LOW, MEDIUM, HIGH",
    ),
    category_id: int | None = Query(
        default=None,
        description="Lọc theo ID danh mục",
    ),
    deadline_from: datetime | None = Query(
        default=None,
        description="Deadline từ ngày (ISO 8601)",
    ),
    deadline_to: datetime | None = Query(
        default=None,
        description="Deadline đến ngày (ISO 8601)",
    ),
    # Sort params
    sort_by: str = Query(
        default="created_at",
        description="Sắp xếp theo: deadline, priority, created_at, title",
    ),
    sort_order: str = Query(
        default="desc",
        description="Thứ tự sắp xếp: asc, desc",
    ),
) -> TaskListResponse:
    """
    Lấy danh sách task của người dùng hiện tại với filter và sort.
    Task mặc định phân nhóm theo: PENDING → OVERDUE → COMPLETED.
    Hỗ trợ filter theo status, priority, category, khoảng deadline.
    """
    filter_params = TaskFilterParams(
        status=status_filter,
        priority=priority,
        category_id=category_id,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    result = await service.list_tasks(
        user_id=current_user.id,
        filter_params=filter_params,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return result


@router.get(
    "/search",
    response_model=TaskListResponse,
    summary="Tìm kiếm task theo từ khóa",
)
async def search_tasks(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
    pagination: Annotated[PaginationParams, Depends()],
    q: str = Query(
        ...,
        min_length=1,
        max_length=255,
        description="Từ khóa tìm kiếm trong tiêu đề và mô tả",
    ),
) -> TaskListResponse:
    """
    Tìm kiếm task theo từ khóa trong tiêu đề và mô tả.
    Sử dụng PostgreSQL full-text search để đảm bảo tốc độ < 500ms.
    Kết quả không bao gồm task đã xóa mềm.
    """
    result = await service.search_tasks(
        user_id=current_user.id,
        keyword=q,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return result


@router.get(
    "/overdue/batch-trigger",
    status_code=status.HTTP_200_OK,
    summary="Kích hoạt batch job chuyển task sang OVERDUE",
)
async def trigger_overdue_batch(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> dict[str, int | str]:
    """
    Kích hoạt thủ công batch job chuyển tất cả task PENDING đã quá deadline
    sang trạng thái OVERDUE cho người dùng hiện tại.
    Thông thường được EventBridge Scheduler gọi tự động mỗi 15 phút.
    Chỉ task PENDING bị ảnh hưởng, task COMPLETED không thay đổi.
    """
    updated_count = await service.mark_overdue_tasks(user_id=current_user.id)
    return {
        "message": f"Đã chuyển {updated_count} task sang trạng thái OVERDUE",
        "updated_count": updated_count,
    }


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Lấy chi tiết một task",
)
async def get_task(
    task_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    """
    Lấy toàn bộ thông tin chi tiết của một task theo ID.
    Bao gồm: tiêu đề, mô tả, deadline, priority, danh mục,
    trạng thái, danh sách nhắc nhở, ngày tạo, ngày cập nhật cuối.
    Trả 404 nếu task không tồn tại hoặc không thuộc người dùng này.
    """
    task = await service.get_task_by_id(task_id=task_id, user_id=current_user.id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task với ID {task_id} không tồn tại hoặc bạn không có quyền truy cập",
        )
    return task


@router.put(
    "/{task_id}",
    response_model=TaskResponse,
    summary="Cập nhật toàn bộ thông tin task",
)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    """
    Cập nhật thông tin task. Có thể chỉnh sửa bất kỳ trường nào.
    - updated_at được tự động cập nhật
    - Nếu deadline thay đổi, các nhắc nhở liên quan được cập nhật theo
    - Deadline mới phải là thời điểm trong tương lai
    - Trả 404 nếu task không tồn tại hoặc không thuộc người dùng này
    """
    task = await service.update_task(
        task_id=task_id,
        user_id=current_user.id,
        payload=payload,
    )
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task với ID {task_id} không tồn tại hoặc bạn không có quyền chỉnh sửa",
        )
    return task


@router.patch(
    "/{task_id}/toggle-status",
    response_model=TaskStatusToggleResponse,
    summary="Đánh dấu hoàn thành hoặc bỏ đánh dấu task",
)
async def toggle_task_status(
    task_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskStatusToggleResponse:
    """
    Toggle trạng thái task giữa COMPLETED và PENDING/OVERDUE.
    - Nếu task đang PENDING hoặc OVERDUE → chuyển sang COMPLETED
    - Nếu task đang COMPLETED → chuyển về PENDING (hoặc OVERDUE nếu deadline đã qua)
    - Khi COMPLETED: các nhắc nhở chưa gửi bị hủy
    - Hỗ trợ undo trong 24 giờ
    - Thống kê dashboard được cập nhật ngay
    """
    result = await service.toggle_task_status(
        task_id=task_id,
        user_id=current_user.id,
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task với ID {task_id} không tồn tại hoặc bạn không có quyền thay đổi",
        )
    return result


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_200_OK,
    summary="Xóa mềm task (có thể hoàn tác trong 5 giây)",
)
async def soft_delete_task(
    task_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> dict[str, str | int]:
    """
    Thực hiện xóa mềm task — đánh dấu deleted_at và is_deleted = True.
    Task và toàn bộ nhắc nhở liên quan bị ẩn khỏi danh sách ngay lập tức.
    Frontend hiển thị Snackbar 'Hoàn tác' trong 5 giây.
    Người dùng có thể gọi endpoint undo để khôi phục trong thời gian này.
    Trả 404 nếu task không tồn tại hoặc không thuộc người dùng.
    """
    success = await service.soft_delete_task(
        task_id=task_id,
        user_id=current_user.id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task với ID {task_id} không tồn tại hoặc bạn không có quyền xóa",
        )
    return {
        "message": "Task đã được xóa. Bạn có thể hoàn tác trong vòng 5 giây.",
        "task_id": task_id,
    }


@router.post(
    "/{task_id}/undo-delete",
    response_model=TaskResponse,
    summary="Hoàn tác xóa task",
)
async def undo_delete_task(
    task_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskResponse:
    """
    Khôi phục task đã bị xóa mềm về trạng thái ban đầu.
    Task được khôi phục nguyên vẹn với toàn bộ dữ liệu.
    Chỉ hoạt động khi task chưa bị hard-delete (trong thời gian undo ~5 giây).
    Trả 404 nếu task không tìm thấy hoặc đã bị xóa vĩnh viễn.
    Trả 409 nếu task chưa bị xóa mềm (không cần undo).
    """
    task = await service.undo_delete_task(
        task_id=task_id,
        user_id=current_user.id,
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Task với ID {task_id} không tồn tại, đã bị xóa vĩnh viễn, "
                "hoặc bạn không có quyền khôi phục"
            ),
        )
    return task