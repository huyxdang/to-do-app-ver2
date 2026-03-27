from typing import AsyncGenerator, Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal

# HTTP Bearer scheme để trích xuất JWT token từ header Authorization
bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency cung cấp async database session cho mỗi request.
    Session được đóng tự động sau khi request hoàn thành.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Type alias cho dependency injection gọn hơn
DBSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DBSession,
) -> dict:
    """
    Dependency xác thực JWT token và trả về thông tin user hiện tại.
    Hỗ trợ cả token từ AWS Cognito và token nội bộ.

    Returns:
        dict chứa các claims từ JWT payload (sub, email, cognito_sub, v.v.)

    Raises:
        HTTPException 401 nếu token không hợp lệ, hết hạn, hoặc thiếu
    """
    # Kiểm tra token có được cung cấp không
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Không tìm thấy token xác thực. Vui lòng đăng nhập.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        # Giải mã và xác thực JWT token
        # Sử dụng SECRET_KEY và algorithm từ settings
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},  # Cognito token có thể có audience khác nhau
        )

        # Trích xuất subject (user identifier) từ payload
        user_sub: str | None = payload.get("sub")
        if user_sub is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token không hợp lệ: thiếu thông tin định danh người dùng.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Kiểm tra token chưa hết hạn (jose tự động verify exp claim)
        # Trả về toàn bộ payload để các route có thể truy cập thêm thông tin
        return payload

    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token không hợp lệ hoặc đã hết hạn: {str(exc)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user_from_db(
    token_payload: Annotated[dict, Depends(get_current_user)],
    db: DBSession,
) -> "UserModel":  # type: ignore[name-defined]  # noqa: F821
    """
    Dependency mở rộng: sau khi xác thực JWT, load User record từ database.
    Dùng trong các route cần thông tin đầy đủ của user (profile, task ownership, v.v.)

    Returns:
        User ORM object từ database

    Raises:
        HTTPException 401 nếu user không tồn tại trong DB (đã bị xóa hoặc chưa sync)
        HTTPException 403 nếu tài khoản chưa được kích hoạt
    """
    from app.models.user import User  # import muộn để tránh circular imports

    cognito_sub: str = token_payload.get("sub", "")

    from sqlalchemy import select

    # Tìm user theo cognito_sub — đây là unique identifier từ Cognito
    result = await db.execute(
        select(User).where(User.cognito_sub == cognito_sub)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản không tồn tại trong hệ thống. Vui lòng đăng ký hoặc liên hệ hỗ trợ.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Kiểm tra trạng thái tài khoản — chỉ cho phép user đã xác minh email
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản chưa được kích hoạt. Vui lòng xác minh email của bạn.",
        )

    return user


# Type alias cho dependency get_current_user (payload chỉ)
CurrentUserPayload = Annotated[dict, Depends(get_current_user)]

# Type alias cho dependency get_current_user_from_db (ORM object)
# Sử dụng Any để tránh circular import tại module level
CurrentUser = Annotated[object, Depends(get_current_user_from_db)]


class PaginationParams:
    """
    Class chứa các tham số phân trang chuẩn cho list endpoints.
    Hỗ trợ cursor-based và offset-based pagination.
    """

    def __init__(
        self,
        page: int = Query(default=1, ge=1, description="Số trang hiện tại, bắt đầu từ 1"),
        page_size: int = Query(
            default=20,
            ge=1,
            le=100,
            alias="pageSize",
            description="Số lượng item mỗi trang, tối đa 100",
        ),
    ) -> None:
        self.page = page
        self.page_size = page_size
        # Tính offset từ page và page_size để dùng trong SQL OFFSET clause
        self.offset = (page - 1) * page_size
        self.limit = page_size

    def __repr__(self) -> str:
        return (
            f"PaginationParams(page={self.page}, page_size={self.page_size}, "
            f"offset={self.offset})"
        )


# Type alias cho pagination dependency
Pagination = Annotated[PaginationParams, Depends(PaginationParams)]


def require_pagination(
    page: int = Query(default=1, ge=1, description="Số trang hiện tại"),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        alias="pageSize",
        description="Số lượng bản ghi mỗi trang",
    ),
) -> PaginationParams:
    """
    Function-based dependency để lấy pagination params.
    Dùng khi cần inject thêm logic validation tùy chỉnh.
    """
    return PaginationParams(page=page, page_size=page_size)


def build_pagination_meta(
    total: int,
    pagination: PaginationParams,
) -> dict:
    """
    Tạo metadata phân trang cho response.
    Tính toán tổng số trang, trang tiếp theo, trang trước.

    Args:
        total: Tổng số bản ghi (không áp dụng limit/offset)
        pagination: PaginationParams instance

    Returns:
        dict chứa thông tin phân trang để đưa vào response body
    """
    import math

    total_pages = math.ceil(total / pagination.page_size) if total > 0 else 0
    has_next = pagination.page < total_pages
    has_previous = pagination.page > 1

    return {
        "total": total,
        "page": pagination.page,
        "pageSize": pagination.page_size,
        "totalPages": total_pages,
        "hasNext": has_next,
        "hasPrevious": has_previous,
    }


__all__ = [
    "get_db",
    "get_current_user",
    "get_current_user_from_db",
    "PaginationParams",
    "require_pagination",
    "build_pagination_meta",
    "DBSession",
    "CurrentUserPayload",
    "CurrentUser",
    "Pagination",
    "bearer_scheme",
]