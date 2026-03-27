from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "",
    summary="Lấy tổng quan dashboard",
    response_model=dict,
)
async def get_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Trả về dashboard tổng quan gồm:
    - Danh sách task hôm nay (tối đa 5)
    - Danh sách task sắp tới 7 ngày (tối đa 5)
    - Danh sách task quá hạn (tối đa 5)
    - Tỷ lệ hoàn thành tổng thể
    """
    service = DashboardService(db)
    try:
        summary = await service.get_dashboard_summary(user_id=current_user.id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Không thể tải dữ liệu dashboard: {exc}",
        ) from exc

    return summary


@router.get(
    "/stats",
    summary="Lấy thống kê cá nhân",
    response_model=dict,
)
async def get_personal_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Trả về thống kê cá nhân gồm:
    - Tổng số task theo từng trạng thái (PENDING / OVERDUE / COMPLETED)
    - Tỷ lệ hoàn thành = COMPLETED / tổng task × 100%
    - Streak: số ngày liên tiếp có ít nhất 1 task hoàn thành
    """
    service = DashboardService(db)
    try:
        stats = await service.get_personal_stats(user_id=current_user.id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Không thể tải thống kê cá nhân: {exc}",
        ) from exc

    return stats