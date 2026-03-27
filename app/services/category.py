from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from typing import Optional

from app.models.task import Category, Task
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse

# Tên danh mục mặc định không thể xóa
UNCATEGORIZED_NAME = "Uncategorized"

# Giới hạn tối đa số danh mục mỗi người dùng được tạo
MAX_CATEGORIES_PER_USER = 50


async def get_or_create_uncategorized(
    db: AsyncSession,
    user_id: int,
) -> Category:
    """
    Lấy danh mục 'Uncategorized' của người dùng.
    Nếu chưa tồn tại, tự động tạo mới.
    """
    result = await db.execute(
        select(Category).where(
            Category.user_id == user_id,
            Category.name == UNCATEGORIZED_NAME,
        )
    )
    uncategorized = result.scalar_one_or_none()

    if uncategorized is None:
        uncategorized = Category(
            user_id=user_id,
            name=UNCATEGORIZED_NAME,
            # Màu mặc định cho danh mục Uncategorized
            color="#9E9E9E",
        )
        db.add(uncategorized)
        await db.flush()

    return uncategorized


async def get_category_by_id(
    db: AsyncSession,
    category_id: int,
    user_id: int,
) -> Category:
    """
    Lấy danh mục theo ID, kiểm tra quyền sở hữu của người dùng.
    """
    result = await db.execute(
        select(Category).where(
            Category.id == category_id,
            Category.user_id == user_id,
        )
    )
    category = result.scalar_one_or_none()

    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id={category_id} not found.",
        )

    return category


async def list_categories(
    db: AsyncSession,
    user_id: int,
) -> list[CategoryResponse]:
    """
    Lấy danh sách tất cả danh mục của người dùng kèm theo số lượng task trong từng danh mục.
    """
    # Đảm bảo danh mục Uncategorized luôn tồn tại
    await get_or_create_uncategorized(db, user_id)
    await db.flush()

    # Query danh sách danh mục cùng số lượng task (không đếm task đã soft-delete)
    task_count_subq = (
        select(Task.category_id, func.count(Task.id).label("task_count"))
        .where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
        )
        .group_by(Task.category_id)
        .subquery()
    )

    result = await db.execute(
        select(Category, func.coalesce(task_count_subq.c.task_count, 0).label("task_count"))
        .outerjoin(task_count_subq, Category.id == task_count_subq.c.category_id)
        .where(Category.user_id == user_id)
        .order_by(Category.name)
    )

    rows = result.all()

    categories: list[CategoryResponse] = []
    for row in rows:
        cat: Category = row[0]
        count: int = row[1]
        categories.append(
            CategoryResponse(
                id=cat.id,
                user_id=cat.user_id,
                name=cat.name,
                color=cat.color,
                task_count=count,
                created_at=cat.created_at,
                updated_at=cat.updated_at,
            )
        )

    return categories


async def create_category(
    db: AsyncSession,
    user_id: int,
    payload: CategoryCreate,
) -> CategoryResponse:
    """
    Tạo danh mục mới cho người dùng.
    Kiểm tra giới hạn 50 danh mục và tên không được trùng lặp.
    """
    # Kiểm tra giới hạn 50 danh mục
    count_result = await db.execute(
        select(func.count(Category.id)).where(Category.user_id == user_id)
    )
    current_count: int = count_result.scalar_one()

    if current_count >= MAX_CATEGORIES_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum number of categories ({MAX_CATEGORIES_PER_USER}) reached. Please delete an existing category before creating a new one.",
        )

    # Kiểm tra tên danh mục không được trùng lặp (case-insensitive)
    duplicate_result = await db.execute(
        select(Category).where(
            Category.user_id == user_id,
            func.lower(Category.name) == func.lower(payload.name),
        )
    )
    existing = duplicate_result.scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A category with the name '{payload.name}' already exists.",
        )

    # Người dùng không được tạo danh mục tên "Uncategorized" vì đây là danh mục hệ thống
    if payload.name.strip().lower() == UNCATEGORIZED_NAME.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{UNCATEGORIZED_NAME}' is a reserved category name and cannot be created manually.",
        )

    new_category = Category(
        user_id=user_id,
        name=payload.name.strip(),
        color=payload.color,
    )
    db.add(new_category)
    await db.flush()
    await db.refresh(new_category)

    return CategoryResponse(
        id=new_category.id,
        user_id=new_category.user_id,
        name=new_category.name,
        color=new_category.color,
        task_count=0,
        created_at=new_category.created_at,
        updated_at=new_category.updated_at,
    )


async def update_category(
    db: AsyncSession,
    category_id: int,
    user_id: int,
    payload: CategoryUpdate,
) -> CategoryResponse:
    """
    Cập nhật tên và/hoặc màu sắc của danh mục.
    Kiểm tra tên mới không trùng với danh mục khác.
    Danh mục 'Uncategorized' không được đổi tên.
    """
    category = await get_category_by_id(db, category_id, user_id)

    # Kiểm tra không cho phép chỉnh sửa tên danh mục Uncategorized
    if category.name == UNCATEGORIZED_NAME and payload.name is not None:
        if payload.name.strip().lower() != UNCATEGORIZED_NAME.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The '{UNCATEGORIZED_NAME}' category name cannot be changed.",
            )

    # Nếu có cập nhật tên mới thì kiểm tra trùng lặp
    if payload.name is not None and payload.name.strip().lower() != category.name.lower():
        # Không cho đặt tên thành "Uncategorized" nếu đây không phải danh mục đó
        if payload.name.strip().lower() == UNCATEGORIZED_NAME.lower() and category.name != UNCATEGORIZED_NAME:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{UNCATEGORIZED_NAME}' is a reserved category name.",
            )

        duplicate_result = await db.execute(
            select(Category).where(
                Category.user_id == user_id,
                func.lower(Category.name) == func.lower(payload.name.strip()),
                Category.id != category_id,
            )
        )
        existing = duplicate_result.scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A category with the name '{payload.name}' already exists.",
            )

        category.name = payload.name.strip()

    if payload.color is not None:
        category.color = payload.color

    await db.flush()
    await db.refresh(category)

    # Đếm số task thuộc danh mục này (không đếm task đã soft-delete)
    task_count_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.category_id == category_id,
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
        )
    )
    task_count: int = task_count_result.scalar_one()

    return CategoryResponse(
        id=category.id,
        user_id=category.user_id,
        name=category.name,
        color=category.color,
        task_count=task_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


async def delete_category(
    db: AsyncSession,
    category_id: int,
    user_id: int,
) -> dict[str, str]:
    """
    Xóa danh mục theo ID.
    Tất cả task thuộc danh mục này sẽ được chuyển sang danh mục 'Uncategorized'.
    Danh mục 'Uncategorized' không được phép xóa.
    """
    category = await get_category_by_id(db, category_id, user_id)

    # Không cho phép xóa danh mục hệ thống Uncategorized
    if category.name == UNCATEGORIZED_NAME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The '{UNCATEGORIZED_NAME}' category cannot be deleted.",
        )

    # Lấy hoặc tạo danh mục Uncategorized để chuyển task sang
    uncategorized = await get_or_create_uncategorized(db, user_id)

    # Chuyển tất cả task (kể cả đã soft-delete) từ danh mục bị xóa sang Uncategorized
    await db.execute(
        update(Task)
        .where(
            Task.category_id == category_id,
            Task.user_id == user_id,
        )
        .values(category_id=uncategorized.id)
    )

    # Xóa danh mục
    await db.delete(category)
    await db.flush()

    return {"detail": f"Category '{category.name}' has been deleted. All associated tasks have been moved to '{UNCATEGORIZED_NAME}'."}


async def get_category_with_task_count(
    db: AsyncSession,
    category_id: int,
    user_id: int,
) -> CategoryResponse:
    """
    Lấy thông tin chi tiết của một danh mục kèm số lượng task.
    """
    category = await get_category_by_id(db, category_id, user_id)

    # Đếm số task thuộc danh mục này (không đếm task đã soft-delete)
    task_count_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.category_id == category_id,
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
        )
    )
    task_count: int = task_count_result.scalar_one()

    return CategoryResponse(
        id=category.id,
        user_id=category.user_id,
        name=category.name,
        color=category.color,
        task_count=task_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )