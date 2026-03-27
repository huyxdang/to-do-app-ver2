from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User, UserProfile
from app.schemas.user import (
    AvatarUploadURLResponse,
    ProfileUpdateRequest,
    UserProfileResponse,
)

logger = logging.getLogger(__name__)

# ===== Internal helpers =====


def _get_s3_client() -> Any:
    """Tạo S3 client với credentials từ settings."""
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def _get_cognito_client() -> Any:
    """Tạo Cognito IDP client với credentials từ settings."""
    return boto3.client(
        "cognito-idp",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


# ===== Core service functions =====


async def get_user_profile(
    cognito_sub: str,
    db: AsyncSession,
) -> UserProfileResponse:
    """
    Lấy thông tin profile của người dùng dựa trên cognito_sub.
    Kết hợp dữ liệu từ bảng User và UserProfile.
    """
    # Truy vấn User cùng với UserProfile bằng join
    stmt = (
        select(User, UserProfile)
        .outerjoin(UserProfile, User.id == UserProfile.user_id)
        .where(User.cognito_sub == cognito_sub)
    )
    result = await db.execute(stmt)
    row = result.first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with cognito_sub '{cognito_sub}' not found.",
        )

    user: User = row[0]
    profile: UserProfile | None = row[1]

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        cognito_sub=user.cognito_sub,
        display_name=profile.display_name if profile else None,
        avatar_url=profile.avatar_url if profile else None,
        is_email_verified=user.is_email_verified,
        created_at=user.created_at,
        updated_at=profile.updated_at if profile else user.created_at,
    )


async def update_user_profile(
    cognito_sub: str,
    payload: ProfileUpdateRequest,
    db: AsyncSession,
) -> UserProfileResponse:
    """
    Cập nhật profile người dùng (display_name, avatar_url).
    Nếu UserProfile chưa tồn tại thì tạo mới (upsert).
    Đồng bộ display_name lên Cognito nếu có thay đổi.
    """
    # Lấy User từ DB
    user_stmt = select(User).where(User.cognito_sub == cognito_sub)
    user_result = await db.execute(user_stmt)
    user: User | None = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with cognito_sub '{cognito_sub}' not found.",
        )

    # Lấy hoặc tạo mới UserProfile
    profile_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
    profile_result = await db.execute(profile_stmt)
    profile: UserProfile | None = profile_result.scalar_one_or_none()

    if profile is None:
        # Tạo mới UserProfile nếu chưa tồn tại
        profile = UserProfile(
            user_id=user.id,
            display_name=payload.display_name,
            avatar_url=payload.avatar_url,
            updated_at=datetime.utcnow(),
        )
        db.add(profile)
    else:
        # Cập nhật các trường nếu payload có giá trị
        if payload.display_name is not None:
            profile.display_name = payload.display_name
        if payload.avatar_url is not None:
            profile.avatar_url = payload.avatar_url
        profile.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(profile)

    # Đồng bộ display_name lên Cognito User Attributes
    if payload.display_name is not None:
        try:
            await sync_cognito_display_name(
                cognito_sub=cognito_sub,
                display_name=payload.display_name,
            )
        except Exception as exc:
            # Lỗi sync Cognito không nên block response; chỉ log warning
            logger.warning(
                "Failed to sync display_name to Cognito for sub=%s: %s",
                cognito_sub,
                exc,
            )

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        cognito_sub=user.cognito_sub,
        display_name=profile.display_name,
        avatar_url=profile.avatar_url,
        is_email_verified=user.is_email_verified,
        created_at=user.created_at,
        updated_at=profile.updated_at,
    )


async def generate_avatar_upload_url(
    cognito_sub: str,
    content_type: str,
    db: AsyncSession,
) -> AvatarUploadURLResponse:
    """
    Tạo S3 pre-signed URL để client upload avatar trực tiếp lên S3.
    Trả về upload_url (PUT) và public_url của avatar sau khi upload.
    Kiểm tra content_type hợp lệ trước khi tạo URL.
    """
    # Chỉ chấp nhận các định dạng ảnh phổ biến
    allowed_content_types: list[str] = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    ]
    if content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Content type '{content_type}' is not allowed. "
                f"Allowed types: {', '.join(allowed_content_types)}"
            ),
        )

    # Xác minh user tồn tại trong DB
    user_stmt = select(User).where(User.cognito_sub == cognito_sub)
    user_result = await db.execute(user_stmt)
    user: User | None = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with cognito_sub '{cognito_sub}' not found.",
        )

    # Tạo unique key cho avatar trong S3
    extension_map: dict[str, str] = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    extension = extension_map[content_type]
    unique_key = f"avatars/{user.id}/{uuid.uuid4().hex}.{extension}"

    s3_client = _get_s3_client()

    try:
        # Tạo pre-signed URL cho PUT operation, hiệu lực 15 phút
        upload_url: str = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": settings.S3_BUCKET_NAME,
                "Key": unique_key,
                "ContentType": content_type,
            },
            ExpiresIn=900,  # 15 phút
        )
    except ClientError as exc:
        logger.error(
            "Failed to generate S3 pre-signed URL for user=%s: %s",
            user.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not generate avatar upload URL. Please try again later.",
        ) from exc

    # Tạo public URL của avatar sau khi upload thành công
    public_url = (
        f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}"
        f".amazonaws.com/{unique_key}"
    )

    return AvatarUploadURLResponse(
        upload_url=upload_url,
        public_url=public_url,
        key=unique_key,
        expires_in=900,
    )


async def sync_cognito_display_name(
    cognito_sub: str,
    display_name: str,
) -> None:
    """
    Đồng bộ display_name (name attribute) lên AWS Cognito User Pool.
    Sử dụng AdminUpdateUserAttributes để cập nhật attribute phía server.
    """
    cognito_client = _get_cognito_client()

    try:
        cognito_client.admin_update_user_attributes(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=cognito_sub,
            UserAttributes=[
                {
                    "Name": "name",
                    "Value": display_name,
                },
            ],
        )
        logger.info(
            "Successfully synced display_name to Cognito for sub=%s",
            cognito_sub,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            "Cognito AdminUpdateUserAttributes failed for sub=%s, code=%s: %s",
            cognito_sub,
            error_code,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to sync attributes to Cognito: {error_code}",
        ) from exc


async def sync_cognito_avatar_url(
    cognito_sub: str,
    avatar_url: str,
) -> None:
    """
    Đồng bộ avatar_url (picture attribute) lên AWS Cognito User Pool.
    Gọi sau khi client hoàn thành upload ảnh lên S3.
    """
    cognito_client = _get_cognito_client()

    try:
        cognito_client.admin_update_user_attributes(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=cognito_sub,
            UserAttributes=[
                {
                    "Name": "picture",
                    "Value": avatar_url,
                },
            ],
        )
        logger.info(
            "Successfully synced avatar_url to Cognito for sub=%s",
            cognito_sub,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            "Cognito sync avatar_url failed for sub=%s, code=%s: %s",
            cognito_sub,
            error_code,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to sync avatar URL to Cognito: {error_code}",
        ) from exc


async def confirm_avatar_upload(
    cognito_sub: str,
    avatar_key: str,
    db: AsyncSession,
) -> UserProfileResponse:
    """
    Xác nhận client đã upload avatar thành công lên S3.
    Cập nhật avatar_url trong DB và đồng bộ lên Cognito picture attribute.
    Kiểm tra object có tồn tại trên S3 trước khi lưu vào DB.
    """
    # Xác minh user tồn tại
    user_stmt = select(User).where(User.cognito_sub == cognito_sub)
    user_result = await db.execute(user_stmt)
    user: User | None = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with cognito_sub '{cognito_sub}' not found.",
        )

    # Kiểm tra object tồn tại trên S3
    s3_client = _get_s3_client()
    try:
        s3_client.head_object(Bucket=settings.S3_BUCKET_NAME, Key=avatar_key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Avatar file '{avatar_key}' not found on S3. "
                    "Please upload the file before confirming."
                ),
            ) from exc
        logger.error(
            "S3 head_object failed for key=%s, user=%s: %s",
            avatar_key,
            user.id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify avatar upload. Please try again.",
        ) from exc

    # Tạo public URL từ key
    public_url = (
        f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}"
        f".amazonaws.com/{avatar_key}"
    )

    # Cập nhật avatar_url trong profile
    profile_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
    profile_result = await db.execute(profile_stmt)
    profile: UserProfile | None = profile_result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(
            user_id=user.id,
            avatar_url=public_url,
            updated_at=datetime.utcnow(),
        )
        db.add(profile)
    else:
        profile.avatar_url = public_url
        profile.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(profile)

    # Đồng bộ avatar_url lên Cognito picture attribute
    try:
        await sync_cognito_avatar_url(
            cognito_sub=cognito_sub,
            avatar_url=public_url,
        )
    except Exception as exc:
        # Lỗi sync Cognito không block response; chỉ log warning
        logger.warning(
            "Failed to sync avatar_url to Cognito for sub=%s: %s",
            cognito_sub,
            exc,
        )

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        cognito_sub=user.cognito_sub,
        display_name=profile.display_name,
        avatar_url=profile.avatar_url,
        is_email_verified=user.is_email_verified,
        created_at=user.created_at,
        updated_at=profile.updated_at,
    )


async def get_or_create_user_from_cognito(
    cognito_sub: str,
    email: str,
    display_name: str | None,
    db: AsyncSession,
) -> User:
    """
    Lấy User từ DB theo cognito_sub; nếu chưa tồn tại thì tạo mới.
    Dùng trong flow đăng nhập lần đầu sau khi Cognito xác thực thành công.
    """
    stmt = select(User).where(User.cognito_sub == cognito_sub)
    result = await db.execute(stmt)
    user: User | None = result.scalar_one_or_none()

    if user is not None:
        return user

    # Tạo mới User
    user = User(
        cognito_sub=cognito_sub,
        email=email,
        is_email_verified=True,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    await db.flush()  # flush để lấy user.id cho UserProfile

    # Tạo mới UserProfile kèm display_name nếu có
    profile = UserProfile(
        user_id=user.id,
        display_name=display_name,
        avatar_url=None,
        updated_at=datetime.utcnow(),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(user)

    logger.info(
        "Created new user record for cognito_sub=%s, email=%s",
        cognito_sub,
        email,
    )
    return user