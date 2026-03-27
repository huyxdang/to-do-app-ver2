from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base  # noqa: F401 — ensure models are registered
from app.models.task import Category, Task, TaskPriority, TaskStatus
from app.schemas.task import (
    TaskCreate,
    TaskFilterParams,
    TaskUpdate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    """Trả về thời điểm hiện tại theo UTC (aware)."""
    return datetime.now(timezone.utc)


def _strip_tz(dt: datetime) -> datetime:
    """Chuyển datetime aware → naive UTC để so sánh với cột DB không có tz."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def get_task_by_id(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    *,
    include_deleted: bool = False,
) -> Task | None:
    """Lấy task theo ID và chủ sở hữu; mặc định bỏ qua các task đã xóa mềm."""
    stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id)
    if not include_deleted:
        stmt = stmt.where(Task.deleted_at.is_(None))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_tasks(
    db: AsyncSession,
    user_id: int,
    filters: TaskFilterParams,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Task], int]:
    """
    Lấy danh sách task của người dùng với lọc, sắp xếp và phân trang.
    Trả về (danh_sách_task, tổng_số_bản_ghi).
    """
    base_where = [Task.user_id == user_id, Task.deleted_at.is_(None)]

    # --- lọc theo trạng thái ---
    if filters.status:
        base_where.append(Task.status == filters.status)

    # --- lọc theo độ ưu tiên ---
    if filters.priority:
        base_where.append(Task.priority == filters.priority)

    # --- lọc theo danh mục ---
    if filters.category_id is not None:
        base_where.append(Task.category_id == filters.category_id)

    # --- lọc theo khoảng deadline ---
    if filters.deadline_from is not None:
        base_where.append(Task.deadline >= _strip_tz(filters.deadline_from))
    if filters.deadline_to is not None:
        base_where.append(Task.deadline <= _strip_tz(filters.deadline_to))

    # --- full-text search trên tiêu đề + mô tả ---
    if filters.search:
        pattern = f"%{filters.search}%"
        base_where.append(
            or_(
                Task.title.ilike(pattern),
                Task.description.ilike(pattern),
            )
        )

    # --- thứ tự sắp xếp ---
    # Ánh xạ tên cột hợp lệ để chống SQL injection
    _sort_column_map: dict[str, Any] = {
        "deadline": Task.deadline,
        "priority": case(
            (Task.priority == TaskPriority.HIGH, 1),
            (Task.priority == TaskPriority.MEDIUM, 2),
            (Task.priority == TaskPriority.LOW, 3),
            else_=4,
        ),
        "created_at": Task.created_at,
        "title": Task.title,
        "updated_at": Task.updated_at,
    }

    sort_col_expr = _sort_column_map.get(
        filters.sort_by if filters.sort_by else "created_at",
        Task.created_at,
    )

    if filters.sort_order and filters.sort_order.lower() == "asc":
        order_expr = sort_col_expr.asc()
    else:
        order_expr = sort_col_expr.desc()

    # --- đếm tổng ---
    count_stmt = select(func.count()).select_from(Task).where(and_(*base_where))
    total: int = (await db.execute(count_stmt)).scalar_one()

    # --- lấy dữ liệu ---
    data_stmt = (
        select(Task).where(and_(*base_where)).order_by(order_expr).offset(skip).limit(limit)
    )
    rows = (await db.execute(data_stmt)).scalars().all()

    return rows, total


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_task(
    db: AsyncSession,
    user_id: int,
    payload: TaskCreate,
) -> Task:
    """Tạo task mới cho người dùng; trạng thái ban đầu là PENDING."""
    # Validate category thuộc về user nếu được cung cấp
    if payload.category_id is not None:
        cat = await db.get(Category, payload.category_id)
        if cat is None or cat.user_id != user_id:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Danh mục không tồn tại hoặc không thuộc về bạn.",
            )

    task = Task(
        user_id=user_id,
        title=payload.title,
        description=payload.description,
        deadline=_strip_tz(payload.deadline) if payload.deadline else None,
        priority=payload.priority if payload.priority else TaskPriority.MEDIUM,
        category_id=payload.category_id,
        status=TaskStatus.PENDING,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def update_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    payload: TaskUpdate,
) -> Task:
    """Cập nhật các trường của task; updated_at tự động được cập nhật."""
    task = await get_task_by_id(db, task_id, user_id)
    if task is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task không tồn tại.",
        )

    update_data = payload.model_dump(exclude_unset=True)

    # Validate category mới nếu có
    if "category_id" in update_data and update_data["category_id"] is not None:
        cat = await db.get(Category, update_data["category_id"])
        if cat is None or cat.user_id != user_id:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Danh mục không tồn tại hoặc không thuộc về bạn.",
            )

    for field, value in update_data.items():
        if field == "deadline" and value is not None:
            value = _strip_tz(value)
        setattr(task, field, value)

    task.updated_at = _now_utc().replace(tzinfo=None)
    await db.commit()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Soft-delete & undo
# ---------------------------------------------------------------------------


async def soft_delete_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> Task:
    """
    Xóa mềm task: đặt deleted_at = now().
    Frontend hiển thị snackbar "Hoàn tác" trong 5 giây; backend cho phép undo
    miễn là deleted_at chưa vượt quá UNDO_WINDOW_SECONDS.
    """
    task = await get_task_by_id(db, task_id, user_id, include_deleted=True)
    if task is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task không tồn tại.",
        )

    if task.deleted_at is not None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task đã bị xóa trước đó.",
        )

    task.deleted_at = _now_utc().replace(tzinfo=None)
    task.updated_at = _now_utc().replace(tzinfo=None)
    await db.commit()
    await db.refresh(task)
    return task


async def undo_delete_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    *,
    undo_window_seconds: int = 30,
) -> Task:
    """
    Hoàn tác xóa mềm nếu vẫn còn trong cửa sổ thời gian cho phép.
    Sau khi khôi phục, kiểm tra lại deadline để gán trạng thái OVERDUE nếu cần.
    """
    from fastapi import HTTPException, status

    task = await get_task_by_id(db, task_id, user_id, include_deleted=True)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task không tồn tại.",
        )

    if task.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task chưa bị xóa, không cần hoàn tác.",
        )

    # Kiểm tra cửa sổ hoàn tác
    now_naive = _now_utc().replace(tzinfo=None)
    elapsed = (now_naive - task.deleted_at).total_seconds()
    if elapsed > undo_window_seconds:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Đã quá thời gian hoàn tác ({undo_window_seconds} giây).",
        )

    # Khôi phục task
    task.deleted_at = None

    # Tái xác định trạng thái: nếu deadline đã qua và chưa hoàn thành → OVERDUE
    if (
        task.status != TaskStatus.COMPLETED
        and task.deadline is not None
        and task.deadline < now_naive
    ):
        task.status = TaskStatus.OVERDUE
    elif task.status == TaskStatus.OVERDUE and (
        task.deadline is None or task.deadline >= now_naive
    ):
        # Deadline đã được dời ra tương lai trước khi xóa (trường hợp hiếm)
        task.status = TaskStatus.PENDING

    task.updated_at = now_naive
    await db.commit()
    await db.refresh(task)
    return task


async def hard_delete_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> None:
    """Xóa vĩnh viễn task khỏi DB (thường được gọi bởi batch job sau khi hết undo window)."""
    from fastapi import HTTPException, status

    task = await get_task_by_id(db, task_id, user_id, include_deleted=True)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task không tồn tại.",
        )

    await db.delete(task)
    await db.commit()


# ---------------------------------------------------------------------------
# Status toggle
# ---------------------------------------------------------------------------


async def toggle_task_status(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> Task:
    """
    Đánh dấu hoàn thành / bỏ đánh dấu hoàn thành task.
    - Nếu task đang PENDING/OVERDUE → chuyển sang COMPLETED.
    - Nếu task đang COMPLETED → chuyển về PENDING hoặc OVERDUE tùy deadline.
    Hỗ trợ undo trong 24 giờ theo spec BRS.
    """
    from fastapi import HTTPException, status

    task = await get_task_by_id(db, task_id, user_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task không tồn tại.",
        )

    now_naive = _now_utc().replace(tzinfo=None)

    if task.status == TaskStatus.COMPLETED:
        # Undo hoàn thành: tái xác định trạng thái dựa vào deadline
        if task.deadline is not None and task.deadline < now_naive:
            task.status = TaskStatus.OVERDUE
        else:
            task.status = TaskStatus.PENDING
    else:
        # Đánh dấu hoàn thành
        task.status = TaskStatus.COMPLETED

    task.updated_at = now_naive
    await db.commit()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Overdue batch update
# ---------------------------------------------------------------------------


async def batch_update_overdue(db: AsyncSession) -> int:
    """
    Chuyển tất cả task PENDING có deadline đã qua sang OVERDUE.
    Được gọi theo batch job mỗi 15 phút (EventBridge Scheduler → Lambda).
    Trả về số lượng bản ghi được cập nhật.
    """
    now_naive = _now_utc().replace(tzinfo=None)

    stmt = (
        update(Task)
        .where(
            and_(
                Task.status == TaskStatus.PENDING,
                Task.deadline.isnot(None),
                Task.deadline < now_naive,
                Task.deleted_at.is_(None),
            )
        )
        .values(status=TaskStatus.OVERDUE, updated_at=now_naive)
        .execution_options(synchronize_session="fetch")
    )
    result = await db.execute(stmt)
    await db.commit()
    # rowcount có thể là None với một số driver; trả về 0 thay vì None
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


async def search_tasks(
    db: AsyncSession,
    user_id: int,
    query: str,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Task], int]:
    """
    Tìm kiếm task theo từ khóa trong tiêu đề và mô tả (case-insensitive ILIKE).
    Sử dụng ILIKE để hoạt động trên cả SQLite lẫn PostgreSQL.
    Từ khóa được sanitize để loại bỏ ký tự đặc biệt của LIKE pattern.
    Trả về (danh_sách_task, tổng_số_bản_ghi).
    """
    # Sanitize: escape % và _ trong chuỗi tìm kiếm
    sanitized = re.sub(r"([%_\\])", r"\\\1", query.strip())
    pattern = f"%{sanitized}%"

    conditions = [
        Task.user_id == user_id,
        Task.deleted_at.is_(None),
        or_(
            Task.title.ilike(pattern),
            Task.description.ilike(pattern),
        ),
    ]

    count_stmt = select(func.count()).select_from(Task).where(and_(*conditions))
    total: int = (await db.execute(count_stmt)).scalar_one()

    data_stmt = (
        select(Task)
        .where(and_(*conditions))
        .order_by(Task.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(data_stmt)).scalars().all()

    return rows, total


# ---------------------------------------------------------------------------
# Statistics helpers (dùng bởi dashboard / stats API)
# ---------------------------------------------------------------------------


async def get_task_stats(
    db: AsyncSession,
    user_id: int,
) -> dict[str, int]:
    """
    Trả về số lượng task theo từng trạng thái và tổng số task của người dùng.
    Kết quả dùng cho trang thống kê cá nhân và dashboard.
    """
    stmt = (
        select(Task.status, func.count(Task.id).label("cnt"))
        .where(Task.user_id == user_id, Task.deleted_at.is_(None))
        .group_by(Task.status)
    )
    rows = (await db.execute(stmt)).all()

    stats: dict[str, int] = {s.value: 0 for s in TaskStatus}
    for row in rows:
        stats[row.status.value] = row.cnt

    stats["total"] = sum(stats.values())
    return stats


async def get_completion_rate(
    db: AsyncSession,
    user_id: int,
) -> float:
    """
    Tính tỷ lệ hoàn thành = COMPLETED / tổng task × 100.
    Trả về 0.0 nếu chưa có task nào.
    """
    stmt = select(func.count(Task.id)).where(
        Task.user_id == user_id,
        Task.deleted_at.is_(None),
    )
    total: int = (await db.execute(stmt)).scalar_one()
    if total == 0:
        return 0.0

    stmt_done = select(func.count(Task.id)).where(
        Task.user_id == user_id,
        Task.status == TaskStatus.COMPLETED,
        Task.deleted_at.is_(None),
    )
    done: int = (await db.execute(stmt_done)).scalar_one()
    return round(done / total * 100, 2)


async def get_today_tasks(
    db: AsyncSession,
    user_id: int,
    limit: int = 5,
) -> Sequence[Task]:
    """
    Lấy tối đa `limit` task có deadline trong ngày hôm nay (UTC).
    Dùng cho section "Hôm nay" trên dashboard.
    """
    now = _now_utc().replace(tzinfo=None)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    stmt = (
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.deadline >= start_of_day,
            Task.deadline <= end_of_day,
            Task.status != TaskStatus.COMPLETED,
        )
        .order_by(Task.deadline.asc())
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def get_upcoming_tasks(
    db: AsyncSession,
    user_id: int,
    days: int = 7,
    limit: int = 5,
) -> Sequence[Task]:
    """
    Lấy tối đa `limit` task sắp đến hạn trong `days` ngày tiếp theo (bắt đầu từ ngày mai).
    Dùng cho section "Sắp tới" trên dashboard.
    """
    now = _now_utc().replace(tzinfo=None)
    tomorrow_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Bắt đầu từ đầu ngày mai để tránh trùng lặp với "hôm nay"
    from datetime import timedelta

    tomorrow_start = tomorrow_start + timedelta(days=1)
    window_end = tomorrow_start + timedelta(days=days)

    stmt = (
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.deadline >= tomorrow_start,
            Task.deadline < window_end,
            Task.status != TaskStatus.COMPLETED,
        )
        .order_by(Task.deadline.asc())
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


async def get_overdue_tasks(
    db: AsyncSession,
    user_id: int,
    limit: int = 5,
) -> Sequence[Task]:
    """
    Lấy tối đa `limit` task đang OVERDUE của người dùng.
    Dùng cho section "Quá hạn" trên dashboard.
    """
    stmt = (
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.status == TaskStatus.OVERDUE,
        )
        .order_by(Task.deadline.asc())
        .limit(limit)
    )
    return (await db.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# Streak calculation
# ---------------------------------------------------------------------------


async def calculate_streak(
    db: AsyncSession,
    user_id: int,
) -> int:
    """
    Tính số ngày liên tiếp (streak) mà người dùng hoàn thành ít nhất 1 task.
    Streak được tính ngược từ hôm nay cho đến khi gặp ngày đầu tiên không có task nào hoàn thành.
    """
    from datetime import date, timedelta

    # Lấy tất cả ngày (UTC, naive) mà có task được hoàn thành
    # updated_at được dùng như proxy cho "ngày hoàn thành"
    stmt = (
        select(func.date(Task.updated_at).label("done_date"))
        .where(
            Task.user_id == user_id,
            Task.status == TaskStatus.COMPLETED,
            Task.deleted_at.is_(None),
        )
        .group_by(func.date(Task.updated_at))
        .order_by(func.date(Task.updated_at).desc())
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        return 0

    done_dates: set[date] = set()
    for row in rows:
        raw = row.done_date
        # func.date() có thể trả về string ("YYYY-MM-DD") hoặc date tùy driver
        if isinstance(raw, str):
            done_dates.add(date.fromisoformat(raw))
        elif isinstance(raw, datetime):
            done_dates.add(raw.date())
        else:
            done_dates.add(raw)  # type: ignore[arg-type]

    today = _now_utc().date()
    streak = 0
    current = today

    while current in done_dates:
        streak += 1
        current -= timedelta(days=1)

    return streak