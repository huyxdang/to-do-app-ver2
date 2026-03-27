from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Category, Task, TaskPriority, TaskStatus
from app.models.user import User

# Số task tối đa hiển thị trong mỗi section của dashboard
MAX_TASKS_PER_SECTION = 5


async def get_today_tasks(
    db: AsyncSession,
    user_id: int,
    limit: int = MAX_TASKS_PER_SECTION,
) -> dict[str, Any]:
    """
    Lấy danh sách task hôm nay (deadline trong ngày hôm nay) của người dùng.
    Trả về danh sách task và tổng số để hiển thị nút "Xem tất cả" nếu cần.
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today_start + timedelta(days=1)

    # Đếm tổng số task hôm nay
    count_stmt = select(func.count(Task.id)).where(
        and_(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.deadline >= today_start,
            Task.deadline < today_end,
            Task.status != TaskStatus.COMPLETED,
        )
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Lấy task hôm nay, sắp xếp theo deadline tăng dần rồi đến priority
    stmt = (
        select(Task)
        .where(
            and_(
                Task.user_id == user_id,
                Task.deleted_at.is_(None),
                Task.deadline >= today_start,
                Task.deadline < today_end,
                Task.status != TaskStatus.COMPLETED,
            )
        )
        .order_by(Task.deadline.asc(), Task.priority.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return {
        "tasks": [_serialize_task(task) for task in tasks],
        "total": total,
        "has_more": total > limit,
    }


async def get_upcoming_tasks(
    db: AsyncSession,
    user_id: int,
    days: int = 7,
    limit: int = MAX_TASKS_PER_SECTION,
) -> dict[str, Any]:
    """
    Lấy danh sách task sắp tới trong vòng 7 ngày tới (không bao gồm hôm nay).
    Chỉ lấy task có trạng thái PENDING.
    """
    today_end = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    upcoming_end = today_end + timedelta(days=days)

    # Đếm tổng số task sắp tới
    count_stmt = select(func.count(Task.id)).where(
        and_(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.deadline >= today_end,
            Task.deadline < upcoming_end,
            Task.status == TaskStatus.PENDING,
        )
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Lấy task sắp tới, sắp xếp theo deadline tăng dần
    stmt = (
        select(Task)
        .where(
            and_(
                Task.user_id == user_id,
                Task.deleted_at.is_(None),
                Task.deadline >= today_end,
                Task.deadline < upcoming_end,
                Task.status == TaskStatus.PENDING,
            )
        )
        .order_by(Task.deadline.asc(), Task.priority.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return {
        "tasks": [_serialize_task(task) for task in tasks],
        "total": total,
        "has_more": total > limit,
        "days": days,
    }


async def get_overdue_tasks(
    db: AsyncSession,
    user_id: int,
    limit: int = MAX_TASKS_PER_SECTION,
) -> dict[str, Any]:
    """
    Lấy danh sách task quá hạn (trạng thái OVERDUE) của người dùng.
    Sắp xếp theo deadline gần nhất lên đầu để ưu tiên xử lý.
    """
    count_stmt = select(func.count(Task.id)).where(
        and_(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.status == TaskStatus.OVERDUE,
        )
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = (
        select(Task)
        .where(
            and_(
                Task.user_id == user_id,
                Task.deleted_at.is_(None),
                Task.status == TaskStatus.OVERDUE,
            )
        )
        .order_by(Task.deadline.desc(), Task.priority.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return {
        "tasks": [_serialize_task(task) for task in tasks],
        "total": total,
        "has_more": total > limit,
    }


async def get_completion_rate(
    db: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """
    Tính tỷ lệ hoàn thành task = số task COMPLETED / tổng task × 100%.
    Chỉ tính các task chưa bị xóa mềm.
    """
    # Đếm tổng task và task hoàn thành trong một query sử dụng conditional aggregation
    stmt = select(
        func.count(Task.id).label("total"),
        func.sum(
            case(
                (Task.status == TaskStatus.COMPLETED, 1),
                else_=0,
            )
        ).label("completed"),
    ).where(
        and_(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    row = result.one()

    total: int = row.total or 0
    completed: int = int(row.completed or 0)

    # Tránh chia cho 0 khi người dùng chưa có task nào
    completion_rate = round((completed / total * 100), 2) if total > 0 else 0.0

    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "pending_tasks": total - completed,
        "completion_rate": completion_rate,
    }


async def get_task_statistics(
    db: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """
    Thống kê task theo từng trạng thái cho người dùng.
    Trả về số lượng task theo trạng thái PENDING, OVERDUE, COMPLETED.
    """
    stmt = select(
        Task.status,
        func.count(Task.id).label("count"),
    ).where(
        and_(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
        )
    ).group_by(Task.status)

    result = await db.execute(stmt)
    rows = result.all()

    stats: dict[str, int] = {
        TaskStatus.PENDING.value: 0,
        TaskStatus.OVERDUE.value: 0,
        TaskStatus.COMPLETED.value: 0,
    }

    for row in rows:
        stats[row.status.value] = row.count

    total = sum(stats.values())
    completed = stats[TaskStatus.COMPLETED.value]
    completion_rate = round((completed / total * 100), 2) if total > 0 else 0.0

    return {
        "total": total,
        "pending": stats[TaskStatus.PENDING.value],
        "overdue": stats[TaskStatus.OVERDUE.value],
        "completed": stats[TaskStatus.COMPLETED.value],
        "completion_rate": completion_rate,
    }


async def calculate_streak(
    db: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """
    Tính streak - số ngày liên tiếp có ít nhất 1 task hoàn thành.
    Streak được tính từ hôm nay ngược về quá khứ.
    Nếu hôm nay chưa có task hoàn thành thì bắt đầu tính từ hôm qua.
    """
    # Lấy tất cả ngày có task hoàn thành, nhóm theo ngày (theo UTC)
    stmt = select(
        func.date(Task.updated_at).label("completion_date"),
    ).where(
        and_(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            Task.status == TaskStatus.COMPLETED,
            Task.updated_at.isnot(None),
        )
    ).group_by(func.date(Task.updated_at)).order_by(
        func.date(Task.updated_at).desc()
    )

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "last_completion_date": None,
        }

    # Chuyển đổi kết quả thành tập hợp các ngày
    completion_dates: set[date] = set()
    for row in rows:
        if row.completion_date is not None:
            if isinstance(row.completion_date, str):
                completion_dates.add(date.fromisoformat(row.completion_date))
            elif isinstance(row.completion_date, datetime):
                completion_dates.add(row.completion_date.date())
            elif isinstance(row.completion_date, date):
                completion_dates.add(row.completion_date)

    if not completion_dates:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "last_completion_date": None,
        }

    today = datetime.now(timezone.utc).date()
    last_completion = max(completion_dates)

    # Tính current streak: đếm ngược từ hôm nay hoặc hôm qua
    # Nếu hôm nay đã có task hoàn thành thì tính từ hôm nay
    # Nếu hôm qua có task hoàn thành và hôm nay chưa có thì vẫn tính streak
    current_streak = 0
    check_date = today

    # Nếu hôm nay không có task hoàn thành, thử bắt đầu từ hôm qua
    if check_date not in completion_dates:
        check_date = today - timedelta(days=1)

    # Đếm ngược số ngày liên tiếp có task hoàn thành
    while check_date in completion_dates:
        current_streak += 1
        check_date -= timedelta(days=1)

    # Tính longest streak trong toàn bộ lịch sử
    longest_streak = _calculate_longest_streak(completion_dates)

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "last_completion_date": last_completion.isoformat() if last_completion else None,
    }


def _calculate_longest_streak(completion_dates: set[date]) -> int:
    """
    Tính chuỗi ngày dài nhất liên tiếp có task hoàn thành.
    Duyệt qua toàn bộ lịch sử và tìm chuỗi dài nhất.
    """
    if not completion_dates:
        return 0

    sorted_dates = sorted(completion_dates)
    longest = 1
    current = 1

    for i in range(1, len(sorted_dates)):
        # Kiểm tra xem ngày hiện tại có liên tiếp với ngày trước không
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1

    return longest


async def get_dashboard_summary(
    db: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """
    Tổng hợp toàn bộ dữ liệu dashboard cho người dùng:
    - Task hôm nay
    - Task sắp tới 7 ngày
    - Task quá hạn
    - Tỷ lệ hoàn thành
    - Streak hiện tại
    Gọi song song các query để tối ưu hiệu suất.
    """
    # Thực hiện tất cả các query aggregation
    today_data = await get_today_tasks(db, user_id)
    upcoming_data = await get_upcoming_tasks(db, user_id)
    overdue_data = await get_overdue_tasks(db, user_id)
    completion_data = await get_completion_rate(db, user_id)
    streak_data = await calculate_streak(db, user_id)

    return {
        "today": today_data,
        "upcoming": upcoming_data,
        "overdue": overdue_data,
        "completion_rate": completion_data["completion_rate"],
        "total_tasks": completion_data["total_tasks"],
        "completed_tasks": completion_data["completed_tasks"],
        "streak": streak_data,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _serialize_task(task: Task) -> dict[str, Any]:
    """
    Chuyển đổi đối tượng Task thành dictionary để trả về trong response.
    Bao gồm các trường cần thiết cho dashboard.
    """
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value if task.status else None,
        "priority": task.priority.value if task.priority else None,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "category_id": task.category_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }