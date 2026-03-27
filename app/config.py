from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---------------------------------------------------------------------------
    # Ứng dụng chung
    # ---------------------------------------------------------------------------
    APP_NAME: str = Field(default="TaskFlow", description="Tên ứng dụng")
    APP_ENV: str = Field(default="development", description="Môi trường chạy: development | staging | production")
    DEBUG: bool = Field(default=False, description="Bật chế độ debug")
    SECRET_KEY: str = Field(description="Secret key dùng để ký JWT nội bộ và các token bảo mật")

    # ---------------------------------------------------------------------------
    # Database (PostgreSQL / SQLite)
    # ---------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./taskflow.db",
        description="Async SQLAlchemy connection string. Ví dụ: postgresql+asyncpg://user:pass@host/db",
    )
    DB_POOL_SIZE: int = Field(default=10, description="Số connection tối đa trong pool")
    DB_MAX_OVERFLOW: int = Field(default=20, description="Số connection tạm thời vượt pool size")
    DB_POOL_RECYCLE: int = Field(default=3600, description="Thời gian (giây) trước khi recycle connection")

    # ---------------------------------------------------------------------------
    # JWT (token nội bộ — ký và verify phía FastAPI)
    # ---------------------------------------------------------------------------
    JWT_ALGORITHM: str = Field(default="HS256", description="Thuật toán ký JWT")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, description="Thời gian hết hạn access token (phút)")
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=30, description="Thời gian hết hạn refresh token (ngày)")

    # ---------------------------------------------------------------------------
    # AWS Chung
    # ---------------------------------------------------------------------------
    AWS_REGION: str = Field(default="ap-southeast-1", description="AWS Region mặc định")
    AWS_ACCESS_KEY_ID: str = Field(description="AWS IAM access key ID")
    AWS_SECRET_ACCESS_KEY: str = Field(description="AWS IAM secret access key")

    # ---------------------------------------------------------------------------
    # AWS Cognito
    # ---------------------------------------------------------------------------
    COGNITO_USER_POOL_ID: str = Field(description="Cognito User Pool ID (vd: ap-southeast-1_XYZ)")
    COGNITO_APP_CLIENT_ID: str = Field(description="Cognito App Client ID không có secret")
    COGNITO_APP_CLIENT_SECRET: str = Field(default="", description="Cognito App Client Secret (nếu có)")
    COGNITO_DOMAIN: str = Field(description="Cognito hosted UI domain (vd: taskflow.auth.ap-southeast-1.amazoncognito.com)")
    COGNITO_JWKS_URL: str = Field(
        default="",
        description="URL lấy JWKS để verify Cognito JWT. Tự động sinh nếu để trống.",
    )

    def get_cognito_jwks_url(self) -> str:
        """Trả về JWKS URL; tự sinh từ region + pool ID nếu chưa cấu hình."""
        if self.COGNITO_JWKS_URL:
            return self.COGNITO_JWKS_URL
        return (
            f"https://cognito-idp.{self.AWS_REGION}.amazonaws.com"
            f"/{self.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
        )

    # ---------------------------------------------------------------------------
    # AWS S3 (lưu avatar và file đính kèm)
    # ---------------------------------------------------------------------------
    S3_BUCKET_NAME: str = Field(description="Tên S3 bucket lưu avatar và media")
    S3_AVATAR_PREFIX: str = Field(default="avatars/", description="Prefix thư mục lưu avatar trong S3")
    S3_PRESIGNED_URL_EXPIRE_SECONDS: int = Field(
        default=3600,
        description="Thời gian hết hạn pre-signed URL (giây)",
    )
    S3_MAX_AVATAR_SIZE_MB: float = Field(default=5.0, description="Giới hạn dung lượng file avatar (MB)")
    S3_ALLOWED_AVATAR_TYPES: List[str] = Field(
        default=["image/jpeg", "image/png", "image/webp"],
        description="MIME types cho phép khi upload avatar",
    )

    # ---------------------------------------------------------------------------
    # AWS SES (gửi email giao dịch)
    # ---------------------------------------------------------------------------
    SES_SENDER_EMAIL: str = Field(description="Địa chỉ email gửi đi qua SES (đã xác minh)")
    SES_SENDER_NAME: str = Field(default="TaskFlow", description="Tên hiển thị trong trường From của email")
    SES_REPLY_TO_EMAIL: str = Field(default="", description="Địa chỉ reply-to (tùy chọn)")
    SES_CONFIGURATION_SET: str = Field(default="", description="SES Configuration Set để tracking (tùy chọn)")
    SES_MAX_SEND_RATE: int = Field(default=14, description="Giới hạn số email/giây theo SES quota")

    # ---------------------------------------------------------------------------
    # AWS SNS (điều phối push notification)
    # ---------------------------------------------------------------------------
    SNS_PLATFORM_APP_ARN_FCM: str = Field(description="ARN của SNS Platform Application cho FCM (Android)")
    SNS_PLATFORM_APP_ARN_APNS: str = Field(description="ARN của SNS Platform Application cho APNs (iOS)")
    SNS_NOTIFICATION_TOPIC_ARN: str = Field(default="", description="ARN SNS Topic dùng cho fan-out notification (tùy chọn)")

    # ---------------------------------------------------------------------------
    # AWS SQS (buffer notification burst)
    # ---------------------------------------------------------------------------
    SQS_NOTIFICATION_QUEUE_URL: str = Field(default="", description="URL SQS queue nhận notification job")
    SQS_DEAD_LETTER_QUEUE_URL: str = Field(default="", description="URL SQS dead-letter queue cho failed notification")
    SQS_VISIBILITY_TIMEOUT: int = Field(default=60, description="SQS visibility timeout (giây)")

    # ---------------------------------------------------------------------------
    # AWS EventBridge Scheduler (lên lịch reminder)
    # ---------------------------------------------------------------------------
    EVENTBRIDGE_SCHEDULER_ROLE_ARN: str = Field(
        description="IAM Role ARN mà EventBridge Scheduler assume để invoke Lambda"
    )
    EVENTBRIDGE_REMINDER_LAMBDA_ARN: str = Field(description="ARN của Lambda function xử lý reminder")
    EVENTBRIDGE_SCHEDULER_GROUP: str = Field(
        default="taskflow-reminders",
        description="Tên schedule group trong EventBridge Scheduler",
    )

    # ---------------------------------------------------------------------------
    # FCM (Firebase Cloud Messaging — Android push notification)
    # ---------------------------------------------------------------------------
    FCM_SERVER_KEY: str = Field(default="", description="FCM Legacy Server Key (nếu dùng trực tiếp FCM HTTP API)")
    FCM_PROJECT_ID: str = Field(default="", description="Firebase Project ID (dùng cho FCM v1 API)")
    FCM_SERVICE_ACCOUNT_JSON: str = Field(
        default="",
        description="Nội dung JSON của Firebase Service Account (base64 hoặc raw JSON string)",
    )

    # ---------------------------------------------------------------------------
    # APNs (Apple Push Notification service — iOS push notification)
    # ---------------------------------------------------------------------------
    APNS_KEY_ID: str = Field(default="", description="APNs Key ID từ Apple Developer Account")
    APNS_TEAM_ID: str = Field(default="", description="Apple Developer Team ID")
    APNS_BUNDLE_ID: str = Field(default="com.taskflow.app", description="Bundle ID của iOS app")
    APNS_PRIVATE_KEY: str = Field(
        default="",
        description="Nội dung file .p8 APNs private key (giữ nguyên ký tự xuống dòng)",
    )
    APNS_USE_SANDBOX: bool = Field(default=False, description="True để dùng APNs sandbox endpoint (dev/staging)")

    # ---------------------------------------------------------------------------
    # CORS
    # ---------------------------------------------------------------------------
    CORS_ALLOWED_ORIGINS: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        description="Danh sách origin được phép gọi API (CORS)",
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, description="Cho phép gửi cookie/auth header qua CORS")

    # ---------------------------------------------------------------------------
    # Pagination mặc định
    # ---------------------------------------------------------------------------
    DEFAULT_PAGE_SIZE: int = Field(default=20, description="Số item mặc định mỗi trang")
    MAX_PAGE_SIZE: int = Field(default=100, description="Số item tối đa cho phép trong một trang")

    # ---------------------------------------------------------------------------
    # Business rules
    # ---------------------------------------------------------------------------
    MAX_CATEGORIES_PER_USER: int = Field(default=50, description="Số danh mục tối đa mỗi người dùng")
    MAX_REMINDERS_PER_TASK: int = Field(default=3, description="Số nhắc nhở tối đa mỗi task")
    TASK_SOFT_DELETE_UNDO_SECONDS: int = Field(default=5, description="Khoảng thời gian (giây) được phép hoàn tác xóa task")
    VERIFICATION_EMAIL_EXPIRE_HOURS: int = Field(default=24, description="Thời gian hết hạn link xác minh email (giờ)")
    PASSWORD_RESET_EXPIRE_HOURS: int = Field(default=1, description="Thời gian hết hạn link reset mật khẩu (giờ)")
    OVERDUE_CHECK_INTERVAL_MINUTES: int = Field(default=15, description="Tần suất batch job kiểm tra task OVERDUE (phút)")
    DASHBOARD_UPCOMING_DAYS: int = Field(default=7, description="Số ngày sắp tới hiển thị trên dashboard")
    DASHBOARD_MAX_TASKS_PER_SECTION: int = Field(default=5, description="Số task tối đa mỗi section trên dashboard")
    SEARCH_DEBOUNCE_MS: int = Field(default=300, description="Debounce tìm kiếm phía client (ms, tham khảo)")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Trả về singleton Settings; kết quả được cache để tránh đọc file .env nhiều lần."""
    return Settings()  # type: ignore[call-arg]


# Singleton dùng trực tiếp trong các module khác: `from app.config import settings`
settings: Settings = get_settings()

__all__ = ["Settings", "settings", "get_settings"]