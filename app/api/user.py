from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.user import (
    AvatarUploadURLRequest,
    AvatarUploadURLResponse,
    UserProfileResponse,
    UserProfileUpdate,
)
from app.services.user import UserService

router = APIRouter(prefix="/profile", tags=["User Profile"])


@router.get(
    "",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Lấy thông tin profile của người dùng hiện tại",
)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    """
    Trả về thông tin profile đầy đủ của người dùng đang đăng nhập,
    bao gồm display_name, email, avatar_url và các thuộc tính Cognito.
    """
    service = UserService(db)
    profile = await service.get_profile(user_id=current_user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông tin profile của người dùng.",
        )
    return profile


@router.put(
    "",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Cập nhật thông tin profile của người dùng hiện tại",
)
async def update_profile(
    payload: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    """
    Cho phép người dùng cập nhật display_name và avatar_url.
    Email ở chế độ readonly với tài khoản OAuth — không thể thay đổi qua endpoint này.
    Thay đổi được đồng bộ ngay lập tức với Cognito User Pools.
    """
    service = UserService(db)

    # Kiểm tra người dùng có tồn tại trước khi cập nhật
    existing_profile = await service.get_profile(user_id=current_user.id)
    if existing_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy thông tin profile của người dùng.",
        )

    # Thực hiện cập nhật profile và đồng bộ với Cognito
    updated_profile = await service.update_profile(
        user_id=current_user.id,
        cognito_sub=current_user.cognito_sub,
        payload=payload,
    )
    return updated_profile


@router.post(
    "/avatar-upload-url",
    response_model=AvatarUploadURLResponse,
    status_code=status.HTTP_200_OK,
    summary="Tạo pre-signed URL để upload avatar lên S3",
)
async def get_avatar_upload_url(
    payload: AvatarUploadURLRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AvatarUploadURLResponse:
    """
    Tạo S3 pre-signed URL cho phép client upload file avatar trực tiếp lên S3
    mà không cần đi qua server. URL có hiệu lực trong thời gian giới hạn.
    Sau khi upload thành công, client cần gọi PUT /profile để cập nhật avatar_url.
    """
    service = UserService(db)

    # Validate content_type — chỉ cho phép các định dạng ảnh hợp lệ
    allowed_content_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if payload.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Định dạng file không được hỗ trợ: '{payload.content_type}'. "
                f"Chỉ chấp nhận: {', '.join(sorted(allowed_content_types))}."
            ),
        )

    # Giới hạn kích thước file tối đa 5MB (5 * 1024 * 1024 bytes)
    max_file_size_bytes = 5 * 1024 * 1024
    if payload.file_size > max_file_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Kích thước file vượt quá giới hạn cho phép. "
                f"Tối đa {max_file_size_bytes // (1024 * 1024)}MB, "
                f"file của bạn: {payload.file_size / (1024 * 1024):.2f}MB."
            ),
        )

    # Tạo pre-signed URL từ S3 service
    result = await service.generate_avatar_upload_url(
        user_id=current_user.id,
        content_type=payload.content_type,
        file_size=payload.file_size,
        file_extension=payload.file_extension,
    )
    return result