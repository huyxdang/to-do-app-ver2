import logging
import json
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.reminder import Reminder, DeliveryLog, NotificationChannel
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

__all__ = [
    "NotificationService",
    "send_push_notification",
    "send_email_notification",
]


class NotificationService:
    """Dịch vụ gửi thông báo: push qua SNS (FCM/APNs) và email qua SES."""

    def __init__(self) -> None:
        # Khởi tạo boto3 clients với region cấu hình
        self._sns_client: Any = boto3.client(
            "sns",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        self._ses_client: Any = boto3.client(
            "ses",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        db: AsyncSession,
        reminder_id: int,
    ) -> dict[str, Any]:
        """
        Điều phối gửi thông báo cho một reminder.
        Kiểm tra trạng thái task trước khi gửi.
        Thực hiện fallback logic nếu push thất bại.
        Ghi log delivery status.
        """
        # Lấy reminder từ DB kèm task liên quan
        reminder = await self._get_reminder_with_task(db, reminder_id)
        if reminder is None:
            logger.warning("Reminder %s không tồn tại.", reminder_id)
            return {"success": False, "reason": "reminder_not_found"}

        task = reminder.task  # type: ignore[attr-defined]

        # Không gửi nếu task đã hoàn thành
        if task.status == TaskStatus.COMPLETED:
            logger.info(
                "Bỏ qua gửi thông báo vì task %s đã COMPLETED.", task.id
            )
            await self._log_delivery(
                db,
                reminder_id=reminder_id,
                channel=reminder.channel,
                status="skipped",
                detail="Task đã COMPLETED",
            )
            return {"success": False, "reason": "task_completed"}

        results: dict[str, Any] = {}

        # Gửi theo kênh đã đăng ký
        if reminder.channel in (
            NotificationChannel.PUSH,
            NotificationChannel.BOTH,
        ):
            push_result = await self._handle_push(db, reminder, task)
            results["push"] = push_result

        if reminder.channel in (
            NotificationChannel.EMAIL,
            NotificationChannel.BOTH,
        ):
            email_result = await self._handle_email(db, reminder, task)
            results["email"] = email_result

        # Fallback: nếu push thất bại thì gửi email
        if (
            reminder.channel == NotificationChannel.PUSH
            and results.get("push", {}).get("success") is False
        ):
            logger.info(
                "Push thất bại cho reminder %s — fallback sang email.", reminder_id
            )
            fallback_result = await self._handle_email(
                db, reminder, task, is_fallback=True
            )
            results["fallback_email"] = fallback_result

        overall_success = any(
            v.get("success") for v in results.values() if isinstance(v, dict)
        )
        return {"success": overall_success, "channels": results}

    # ------------------------------------------------------------------
    # Push notification (SNS → FCM / APNs)
    # ------------------------------------------------------------------

    async def _handle_push(
        self,
        db: AsyncSession,
        reminder: "Reminder",
        task: "Task",
    ) -> dict[str, Any]:
        """Gửi push notification qua SNS endpoint của user."""
        user_push_arn: str | None = getattr(
            reminder.user, "sns_endpoint_arn", None  # type: ignore[attr-defined]
        )

        if not user_push_arn:
            # User chưa đăng ký push token → không gửi được
            logger.info(
                "User %s chưa có SNS endpoint ARN, bỏ qua push.",
                task.user_id,
            )
            await self._log_delivery(
                db,
                reminder_id=reminder.id,
                channel=NotificationChannel.PUSH,
                status="skipped",
                detail="Không có SNS endpoint ARN",
            )
            return {"success": False, "reason": "no_endpoint_arn"}

        title = f"Nhắc nhở: {task.title}"
        body = _build_push_body(task)
        deep_link = f"taskflow://tasks/{task.id}"

        # Cấu trúc message cho SNS multi-platform
        message_structure = _build_sns_message(title, body, deep_link)

        try:
            response = self._sns_client.publish(
                TargetArn=user_push_arn,
                Message=json.dumps(message_structure),
                MessageStructure="json",
                Subject=title,
            )
            message_id: str = response.get("MessageId", "")
            logger.info(
                "Push gửi thành công cho reminder %s, MessageId=%s",
                reminder.id,
                message_id,
            )
            await self._log_delivery(
                db,
                reminder_id=reminder.id,
                channel=NotificationChannel.PUSH,
                status="sent",
                detail=f"SNS MessageId={message_id}",
            )
            return {"success": True, "message_id": message_id}

        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            error_msg = exc.response["Error"]["Message"]
            logger.error(
                "Lỗi SNS khi gửi push cho reminder %s: [%s] %s",
                reminder.id,
                error_code,
                error_msg,
            )
            await self._log_delivery(
                db,
                reminder_id=reminder.id,
                channel=NotificationChannel.PUSH,
                status="failed",
                detail=f"SNS error {error_code}: {error_msg}",
            )
            return {"success": False, "reason": error_msg, "code": error_code}

    # ------------------------------------------------------------------
    # Email (SES)
    # ------------------------------------------------------------------

    async def _handle_email(
        self,
        db: AsyncSession,
        reminder: "Reminder",
        task: "Task",
        is_fallback: bool = False,
    ) -> dict[str, Any]:
        """Gửi email nhắc nhở qua Amazon SES."""
        user_email: str | None = getattr(
            reminder.user, "email", None  # type: ignore[attr-defined]
        )
        if not user_email:
            logger.warning(
                "Không tìm thấy email của user cho reminder %s.", reminder.id
            )
            await self._log_delivery(
                db,
                reminder_id=reminder.id,
                channel=NotificationChannel.EMAIL,
                status="skipped",
                detail="Không có địa chỉ email",
            )
            return {"success": False, "reason": "no_email_address"}

        subject, html_body, text_body = _build_email_content(task, is_fallback)

        try:
            response = self._ses_client.send_email(
                Source=settings.SES_SENDER_EMAIL,
                Destination={"ToAddresses": [user_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                    },
                },
            )
            message_id: str = response.get("MessageId", "")
            label = "fallback_email" if is_fallback else "email"
            logger.info(
                "Email (%s) gửi thành công cho reminder %s, MessageId=%s",
                label,
                reminder.id,
                message_id,
            )
            await self._log_delivery(
                db,
                reminder_id=reminder.id,
                channel=NotificationChannel.EMAIL,
                status="sent",
                detail=f"SES MessageId={message_id}; fallback={is_fallback}",
            )
            return {"success": True, "message_id": message_id}

        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            error_msg = exc.response["Error"]["Message"]
            logger.error(
                "Lỗi SES khi gửi email cho reminder %s: [%s] %s",
                reminder.id,
                error_code,
                error_msg,
            )
            await self._log_delivery(
                db,
                reminder_id=reminder.id,
                channel=NotificationChannel.EMAIL,
                status="failed",
                detail=f"SES error {error_code}: {error_msg}",
            )
            return {"success": False, "reason": error_msg, "code": error_code}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_reminder_with_task(
        self,
        db: AsyncSession,
        reminder_id: int,
    ) -> "Reminder | None":
        """Tải reminder cùng task và user liên quan."""
        from sqlalchemy.orm import selectinload

        stmt = (
            select(Reminder)
            .options(
                selectinload(Reminder.task),  # type: ignore[attr-defined]
                selectinload(Reminder.user),  # type: ignore[attr-defined]
            )
            .where(Reminder.id == reminder_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _log_delivery(
        self,
        db: AsyncSession,
        *,
        reminder_id: int,
        channel: "NotificationChannel",
        status: str,
        detail: str = "",
    ) -> None:
        """Ghi log trạng thái delivery vào bảng delivery_logs."""
        try:
            log_entry = DeliveryLog(
                reminder_id=reminder_id,
                channel=channel,
                status=status,
                detail=detail,
                sent_at=datetime.now(timezone.utc),
            )
            db.add(log_entry)
            await db.commit()
        except Exception as exc:
            # Không để lỗi log làm crash toàn bộ flow
            logger.error("Không thể ghi delivery log: %s", exc)
            await db.rollback()


# ------------------------------------------------------------------
# Module-level convenience functions
# ------------------------------------------------------------------


async def send_push_notification(
    db: AsyncSession,
    reminder_id: int,
    endpoint_arn: str,
    task_title: str,
    task_id: int,
    deadline: datetime | None,
) -> dict[str, Any]:
    """
    Gửi push notification độc lập không qua service instance.
    Dùng khi cần gọi nhanh từ Lambda handler hoặc background task.
    """
    sns_client: Any = boto3.client(
        "sns",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    title = f"Nhắc nhở: {task_title}"
    deadline_str = (
        deadline.strftime("%d/%m/%Y %H:%M") if deadline else "Không có deadline"
    )
    body = f"Deadline: {deadline_str}"
    deep_link = f"taskflow://tasks/{task_id}"
    message_structure = _build_sns_message(title, body, deep_link)

    try:
        response = sns_client.publish(
            TargetArn=endpoint_arn,
            Message=json.dumps(message_structure),
            MessageStructure="json",
            Subject=title,
        )
        return {"success": True, "message_id": response.get("MessageId", "")}
    except ClientError as exc:
        error_msg = exc.response["Error"]["Message"]
        logger.error("send_push_notification thất bại: %s", error_msg)
        return {"success": False, "reason": error_msg}


async def send_email_notification(
    db: AsyncSession,
    to_email: str,
    task_title: str,
    task_id: int,
    deadline: datetime | None,
    is_fallback: bool = False,
) -> dict[str, Any]:
    """
    Gửi email nhắc nhở độc lập không qua service instance.
    Dùng cho background job hoặc Lambda handler.
    """
    ses_client: Any = boto3.client(
        "ses",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    deadline_str = (
        deadline.strftime("%d/%m/%Y %H:%M") if deadline else "Không có deadline"
    )

    prefix = "[Thông báo dự phòng] " if is_fallback else ""
    subject = f"{prefix}Nhắc nhở task: {task_title}"
    text_body = (
        f"Bạn có một công việc cần hoàn thành.\n\n"
        f"Tiêu đề: {task_title}\n"
        f"Deadline: {deadline_str}\n\n"
        f"Mở ứng dụng TaskFlow để xem chi tiết: taskflow://tasks/{task_id}"
    )
    html_body = (
        f"<html><body>"
        f"<h2>Nhắc nhở công việc</h2>"
        f"<p><strong>Tiêu đề:</strong> {task_title}</p>"
        f"<p><strong>Deadline:</strong> {deadline_str}</p>"
        f"<p><a href='taskflow://tasks/{task_id}'>Mở trong TaskFlow</a></p>"
        f"</body></html>"
    )

    try:
        response = ses_client.send_email(
            Source=settings.SES_SENDER_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
        return {"success": True, "message_id": response.get("MessageId", "")}
    except ClientError as exc:
        error_msg = exc.response["Error"]["Message"]
        logger.error("send_email_notification thất bại: %s", error_msg)
        return {"success": False, "reason": error_msg}


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _build_push_body(task: "Task") -> str:
    """Tạo nội dung body cho push notification dựa trên task."""
    if task.deadline:
        deadline_str = task.deadline.strftime("%d/%m/%Y %H:%M")
        return f"Deadline: {deadline_str}"
    return "Hãy hoàn thành công việc của bạn!"


def _build_sns_message(
    title: str,
    body: str,
    deep_link: str,
) -> dict[str, Any]:
    """
    Tạo message structure cho SNS publish với cấu trúc
    phù hợp FCM (GCM) và APNs.
    """
    # Payload cho FCM (Android)
    fcm_payload = json.dumps(
        {
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "deep_link": deep_link,
            },
        }
    )

    # Payload cho APNs (iOS)
    apns_payload = json.dumps(
        {
            "aps": {
                "alert": {
                    "title": title,
                    "body": body,
                },
                "sound": "default",
                "badge": 1,
            },
            "deep_link": deep_link,
        }
    )

    # Default fallback (SMS hoặc các platform khác)
    default_payload = f"{title}: {body}"

    return {
        "default": default_payload,
        "GCM": fcm_payload,
        "APNS": apns_payload,
        "APNS_SANDBOX": apns_payload,
    }


def _build_email_content(
    task: "Task",
    is_fallback: bool = False,
) -> tuple[str, str, str]:
    """
    Tạo nội dung email: subject, html_body, text_body.
    Trả về tuple (subject, html_body, text_body).
    """
    deadline_str = (
        task.deadline.strftime("%d/%m/%Y %H:%M")
        if task.deadline
        else "Không có deadline"
    )
    priority_label = task.priority.value if task.priority else "Medium"
    deep_link = f"taskflow://tasks/{task.id}"

    prefix = "[Thông báo dự phòng] " if is_fallback else ""
    subject = f"{prefix}Nhắc nhở task: {task.title}"

    text_body = (
        f"Xin chào,\n\n"
        f"Đây là nhắc nhở cho công việc của bạn.\n\n"
        f"Tiêu đề   : {task.title}\n"
        f"Deadline  : {deadline_str}\n"
        f"Ưu tiên   : {priority_label}\n\n"
        f"Mở ứng dụng TaskFlow: {deep_link}\n\n"
        f"Trân trọng,\nĐội ngũ TaskFlow"
    )

    html_body = f"""
<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><title>Nhắc nhở Task</title></head>
<body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
  <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
    <h2 style="color:#2563eb;">📋 Nhắc nhở công việc</h2>
    {"<p style='color:#dc2626;font-weight:bold;'>⚠️ Thông báo dự phòng (push notification không khả dụng)</p>" if is_fallback else ""}
    <table style="width:100%;border-collapse:collapse;margin-top:16px;">
      <tr>
        <td style="padding:8px;color:#6b7280;width:120px;">Tiêu đề</td>
        <td style="padding:8px;font-weight:bold;">{task.title}</td>
      </tr>
      <tr style="background:#f9fafb;">
        <td style="padding:8px;color:#6b7280;">Deadline</td>
        <td style="padding:8px;color:#dc2626;">{deadline_str}</td>
      </tr>
      <tr>
        <td style="padding:8px;color:#6b7280;">Ưu tiên</td>
        <td style="padding:8px;">{priority_label}</td>
      </tr>
    </table>
    <div style="text-align:center;margin-top:24px;">
      <a href="{deep_link}"
         style="background:#2563eb;color:white;padding:12px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;">
        Mở trong TaskFlow
      </a>
    </div>
    <p style="color:#9ca3af;font-size:12px;margin-top:24px;text-align:center;">
      Bạn nhận được email này vì đã cài đặt nhắc nhở trong TaskFlow.
    </p>
  </div>
</body>
</html>
""".strip()

    return subject, html_body, text_body