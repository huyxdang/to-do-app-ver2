from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import AsyncSession
from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)
from app.services.category import CategoryService

router = APIRouter(prefix="/categories", tags=["categories"])

# Dependency type alias
DBDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get(
    "/",
    response_model=list[CategoryResponse],
    summary="Lấy danh sách tất cả danh mục của người dùng hiện tại cùng số task",
)
async def list_categories(
    db: DBDep,
    current_user: CurrentUserDep,
) -> list[CategoryResponse]:
    """
    Trả về toàn bộ danh mục thuộc về người dùng, mỗi danh mục kèm số lượng task.
    Danh mục 'Uncategorized' luôn hiển thị đầu tiên.
    """
    service = CategoryService(db)
    # Lấy danh sách danh mục kèm task_count được tổng hợp từ bảng tasks
    categories = await service.list_categories_with_task_count(user_id=current_user.id)
    return categories


@router.post(
    "/",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo danh mục mới cho người dùng hiện tại",
)
async def create_category(
    payload: CategoryCreate,
    db: DBDep,
    current_user: CurrentUserDep,
) -> CategoryResponse:
    """
    Tạo danh mục mới. Giới hạn tối đa 50 danh mục mỗi người dùng.
    Tên danh mục không được trùng lặp (case-insensitive) trong cùng người dùng.
    """
    service = CategoryService(db)

    # Kiểm tra giới hạn 50 danh mục / người dùng
    count = await service.count_user_categories(user_id=current_user.id)
    if count >= 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bạn đã đạt giới hạn tối đa 50 danh mục. Vui lòng xóa danh mục cũ trước khi tạo mới.",
        )

    # Kiểm tra tên danh mục đã tồn tại chưa
    existing = await service.get_category_by_name(
        user_id=current_user.id, name=payload.name
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Danh mục với tên '{payload.name}' đã tồn tại. Vui lòng chọn tên khác.",
        )

    category = await service.create_category(user_id=current_user.id, payload=payload)
    return category


@router.get(
    "/{category_id}",
    response_model=CategoryResponse,
    summary="Lấy thông tin chi tiết một danh mục kèm số lượng task",
)
async def get_category(
    category_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
) -> CategoryResponse:
    """
    Lấy chi tiết một danh mục theo ID. Chỉ cho phép truy cập danh mục thuộc về người dùng.
    """
    service = CategoryService(db)
    category = await service.get_category_with_task_count(
        category_id=category_id, user_id=current_user.id
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy danh mục với ID {category_id}.",
        )
    return category


@router.put(
    "/{category_id}",
    response_model=CategoryResponse,
    summary="Cập nhật tên hoặc màu sắc của danh mục",
)
async def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
) -> CategoryResponse:
    """
    Cập nhật danh mục. Danh mục 'Uncategorized' mặc định không thể chỉnh sửa tên.
    Tên mới không được trùng với danh mục khác của cùng người dùng.
    """
    service = CategoryService(db)

    # Kiểm tra danh mục có tồn tại và thuộc về người dùng
    category = await service.get_category_by_id(
        category_id=category_id, user_id=current_user.id
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy danh mục với ID {category_id}.",
        )

    # Danh mục mặc định 'Uncategorized' không được phép đổi tên
    if category.is_default and payload.name is not None and payload.name != category.name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Không thể đổi tên danh mục mặc định 'Uncategorized'.",
        )

    # Kiểm tra tên mới có trùng với danh mục khác không
    if payload.name is not None and payload.name != category.name:
        existing = await service.get_category_by_name(
            user_id=current_user.id, name=payload.name
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Danh mục với tên '{payload.name}' đã tồn tại. Vui lòng chọn tên khác.",
            )

    updated_category = await service.update_category(
        category=category, payload=payload
    )
    return updated_category


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xóa danh mục và chuyển task sang Uncategorized",
)
async def delete_category(
    category_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
) -> None:
    """
    Xóa danh mục theo ID. Toàn bộ task thuộc danh mục này sẽ được chuyển sang
    danh mục 'Uncategorized' mặc định thay vì bị xóa theo.
    Danh mục 'Uncategorized' mặc định không thể xóa.
    """
    service = CategoryService(db)

    # Kiểm tra danh mục có tồn tại và thuộc về người dùng
    category = await service.get_category_by_id(
        category_id=category_id, user_id=current_user.id
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy danh mục với ID {category_id}.",
        )

    # Không cho phép xóa danh mục mặc định 'Uncategorized'
    if category.is_default:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Không thể xóa danh mục mặc định 'Uncategorized'.",
        )

    # Chuyển tất cả task sang Uncategorized trước khi xóa
    await service.delete_category_and_reassign_tasks(
        category=category, user_id=current_user.id
    )