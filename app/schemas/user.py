from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.config import settings

__all__ = [
    "UserRegisterRequest",
    "UserRegisterResponse",
    "UserProfileResponse",
    "UserProfileUpdate",
    "AvatarUploadUrlRequest",
    "AvatarUploadUrlResponse",
]


class UserRegisterRequest(BaseModel):
    """
    Schema for user registration request.
    Validates email format and password strength.
    """
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Password must be at least 8 characters"
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User display name"
    )

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """
        Validate password contains uppercase, lowercase, digit, and special character.
        Mật khẩu phải chứa chữ hoa, chữ thường, số và ký tự đặc biệt.
        """
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v)

        if not (has_upper and has_lower and has_digit and has_special):
            raise ValueError(
                "Password must contain uppercase letter, lowercase letter, digit, and special character"
            )
        return v

    model_config = {"json_schema_extra": {
        "example": {
            "email": "user@example.com",
            "password": "SecurePass123!",
            "display_name": "John Doe"
        }
    }}


class UserRegisterResponse(BaseModel):
    """Response after successful registration."""
    user_id: str = Field(..., description="Unique user identifier from Cognito")
    email: str = Field(..., description="User email address")
    display_name: str = Field(..., description="User display name")
    message: str = Field(
        default="Registration successful. Please check your email to verify your account.",
        description="Confirmation message"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "user_id": "cognito-sub-xxx",
            "email": "user@example.com",
            "display_name": "John Doe",
            "message": "Registration successful. Please check your email to verify your account."
        }
    }}


class UserProfileResponse(BaseModel):
    """User profile information response."""
    user_id: str = Field(..., description="Unique user identifier")
    email: str = Field(..., description="User email address (read-only)")
    display_name: str = Field(..., description="User display name")
    avatar_url: Optional[str] = Field(
        default=None,
        description="S3 URL to user avatar image"
    )
    created_at: str = Field(..., description="Account creation timestamp in ISO format")
    updated_at: str = Field(..., description="Last profile update timestamp in ISO format")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "user_id": "cognito-sub-xxx",
                "email": "user@example.com",
                "display_name": "John Doe",
                "avatar_url": "https://s3.amazonaws.com/bucket/avatars/user123.jpg",
                "created_at": "2025-01-24T10:00:00Z",
                "updated_at": "2025-01-24T10:00:00Z"
            }
        }
    )


from pydantic import ConfigDict


class UserProfileResponse(BaseModel):
    """User profile information response."""
    user_id: str = Field(..., description="Unique user identifier")
    email: str = Field(..., description="User email address (read-only)")
    display_name: str = Field(..., description="User display name")
    avatar_url: Optional[str] = Field(
        default=None,
        description="S3 URL to user avatar image"
    )
    created_at: str = Field(..., description="Account creation timestamp in ISO format")
    updated_at: str = Field(..., description="Last profile update timestamp in ISO format")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "user_id": "cognito-sub-xxx",
                "email": "user@example.com",
                "display_name": "John Doe",
                "avatar_url": "https://s3.amazonaws.com/bucket/avatars/user123.jpg",
                "created_at": "2025-01-24T10:00:00Z",
                "updated_at": "2025-01-24T10:00:00Z"
            }
        }
    )


class UserProfileUpdate(BaseModel):
    """
    Schema for updating user profile.
    Only display_name can be updated; email is read-only.
    """
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User display name"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "display_name": "Jane Doe Updated"
        }
    }}


class AvatarUploadUrlRequest(BaseModel):
    """
    Request for generating S3 pre-signed URL to upload avatar.
    Contains file metadata for security validation.
    """
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original filename with extension (e.g., 'avatar.jpg')"
    )
    content_type: str = Field(
        ...,
        description="MIME type of the file (e.g., 'image/jpeg', 'image/png')"
    )
    file_size: int = Field(
        ...,
        gt=0,
        le=10485760,
        description="File size in bytes (maximum 10 MB)"
    )

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        """
        Validate that content type is an allowed image format.
        Chỉ cho phép các định dạng ảnh phổ biến.
        """
        allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        if v not in allowed_types:
            raise ValueError(
                f"Content type must be one of: {', '.join(allowed_types)}"
            )
        return v

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        """
        Validate filename contains only safe characters.
        Tên tệp không được chứa ký tự đặc biệt nguy hiểm.
        """
        import re
        if not re.match(r"^[\w\-. ]+$", v):
            raise ValueError(
                "Filename can only contain alphanumeric characters, hyphens, underscores, dots, and spaces"
            )
        return v

    model_config = {"json_schema_extra": {
        "example": {
            "filename": "avatar.jpg",
            "content_type": "image/jpeg",
            "file_size": 2097152
        }
    }}


class AvatarUploadUrlResponse(BaseModel):
    """
    Response containing pre-signed URL for direct S3 upload.
    Client can use this URL to upload file directly to S3.
    """
    upload_url: str = Field(
        ...,
        description="Pre-signed S3 URL for uploading avatar file (valid for 15 minutes)"
    )
    avatar_url: str = Field(
        ...,
        description="Final S3 URL where avatar will be accessible after upload"
    )
    expires_in_seconds: int = Field(
        default=900,
        description="Pre-signed URL expiration time in seconds (900 = 15 minutes)"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "upload_url": "https://s3.amazonaws.com/bucket/avatars/user123.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&...",
            "avatar_url": "https://s3.amazonaws.com/bucket/avatars/user123.jpg",
            "expires_in_seconds": 900
        }
    }}