from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.reminder import Reminder, ReminderChannel, ReminderType
from app.models.task import Task, TaskStatus
from app.schemas.reminder import ReminderCreate, ReminderUpdate

logger = logging.getLogger(__name__)

# Giới hạn tối đa số nhắc nhở cho mỗi task
MAX_REMINDERS_PER_TASK = 3

# Prefix cho tên schedule trên EventBridge Scheduler
SCHEDULE_NAME_PREFIX = "taskflow-reminder-"

# Target ARN của Lambda function xử lý gửi notification
NOTIFICATION_LAMBDA_ARN = settings.NOTIFICATION_LAMBDA_ARN

# Role ARN để EventBridge Scheduler invoke Lambda
SCHEDULER_ROLE_ARN = settings.SCHEDULER_ROLE_ARN

# Scheduler group name
SCHEDULER_GROUP_NAME = settings.SCHEDULER_GROUP_NAME


def _get_scheduler_client() -> Any:
    """Tạo boto3 client cho EventBridge Scheduler."""
    return boto3.client(
        "scheduler",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def _build_schedule_name(reminder_id: int) -> str:
    """Tạo tên schedule duy nhất cho EventBridge dựa trên reminder_id."""
    return f"{SCHEDULE_NAME_PREFIX}{reminder_id}"


def _build_at_expression(scheduled_at: datetime) -> str:
    """
    Chuyển đổi datetime thành biểu thức 'at(...)' cho EventBridge Scheduler.
    Đảm bảo datetime ở UTC trước khi format.
    """
    if scheduled_at.tzinfo is None:
        # Nếu không có timezone, coi là UTC
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    else:
        scheduled_at = scheduled_at.astimezone(timezone.utc)
    return f"at({scheduled_at.strftime('%Y-%m-%dT%H:%M:%S')})"


def _build_cron_expression(cron_expr: str) -> str:
    """
    Đóng gói cron expression theo định dạng EventBridge Scheduler.
    Ví dụ: 'cron(0 9 * * ? *)' cho hàng ngày lúc 9h UTC.
    """
    # Nếu người dùng đã truyền đúng định dạng 'cron(...)', giữ nguyên
    if cron_expr.startswith("cron(") and cron_expr.endswith(")"):
        return cron_expr
    # Ngược lại, bọc trong cron(...)
    return f"cron({cron_expr})"


def _build_target_input(reminder_id: int, task_id: int, user_id: int) -> str:
    """
    Tạo payload JSON để truyền vào Lambda khi EventBridge trigger.
    Lambda sẽ dựa trên reminder_id để gửi notification đúng.
    """
    payload = {
        "reminder_id": reminder_id,
        "task_id": task_id,
        "user_id": user_id,
        "source": "eventbridge-scheduler",
    }
    return json.dumps(payload)


async def _count_reminders_for_task(db: AsyncSession, task_id: int) -> int:
    """Đếm số nhắc nhở hiện tại của một task (chỉ đếm các reminder chưa bị xóa)."""
    stmt = select(func.count(Reminder.id)).where(
        Reminder.task_id == task_id,
        Reminder.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    count = result.scalar_one()
    return count


async def _get_task_or_raise(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> Task:
    """
    Lấy task theo task_id và user_id, raise 404 nếu không tìm thấy
    hoặc task không thuộc về user hiện tại.
    """
    from fastapi import HTTPException, status

    stmt = select(Task).where(
        Task.id == task_id,
        Task.user_id == user_id,
        Task.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} không tồn tại hoặc không thuộc quyền truy cập của bạn.",
        )
    return task


async def _get_reminder_or_raise(
    db: AsyncSession,
    reminder_id: int,
    user_id: int,
) -> Reminder:
    """
    Lấy reminder theo reminder_id, kiểm tra quyền sở hữu thông qua task.
    Raise 404 nếu không tìm thấy.
    """
    from fastapi import HTTPException, status

    stmt = (
        select(Reminder)
        .join(Task, Reminder.task_id == Task.id)
        .where(
            Reminder.id == reminder_id,
            Reminder.is_deleted == False,  # noqa: E712
            Task.user_id == user_id,
            Task.is_deleted == False,  # noqa: E712
        )
    )
    result = await db.execute(stmt)
    reminder = result.scalar_one_or_none()
    if reminder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reminder {reminder_id} không tồn tại hoặc không thuộc quyền truy cập của bạn.",
        )
    return reminder


def _create_eventbridge_schedule(
    scheduler_client: Any,
    schedule_name: str,
    schedule_expression: str,
    target_input: str,
    reminder_id: int,
    is_recurring: bool,
) -> str:
    """
    Tạo mới một schedule trên EventBridge Scheduler.
    Trả về schedule ARN nếu thành công.
    - Với one-time reminder: dùng biểu thức 'at(...)' và FlexibleTimeWindow NONE.
    - Với recurring reminder: dùng biểu thức 'cron(...)' và FlexibleTimeWindow OFF.
    """
    try:
        kwargs: dict[str, Any] = {
            "Name": schedule_name,
            "GroupName": SCHEDULER_GROUP_NAME,
            "ScheduleExpression": schedule_expression,
            "ScheduleExpressionTimezone": "UTC",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": NOTIFICATION_LAMBDA_ARN,
                "RoleArn": SCHEDULER_ROLE_ARN,
                "Input": target_input,
            },
            "State": "ENABLED",
        }
        # One-time reminder: tự xóa schedule sau khi trigger
        if not is_recurring:
            kwargs["ActionAfterCompletion"] = "DELETE"

        response = scheduler_client.create_schedule(**kwargs)
        schedule_arn: str = response.get("ScheduleArn", "")
        logger.info(
            "Đã tạo EventBridge schedule '%s' cho reminder %d, ARN: %s",
            schedule_name,
            reminder_id,
            schedule_arn,
        )
        return schedule_arn
    except ClientError as exc:
        logger.error(
            "Lỗi khi tạo EventBridge schedule '%s': %s",
            schedule_name,
            exc,
        )
        raise


def _update_eventbridge_schedule(
    scheduler_client: Any,
    schedule_name: str,
    schedule_expression: str,
    target_input: str,
    is_recurring: bool,
) -> str:
    """
    Cập nhật schedule đã tồn tại trên EventBridge Scheduler.
    Trả về schedule ARN sau khi cập nhật.
    """
    try:
        kwargs: dict[str, Any] = {
            "Name": schedule_name,
            "GroupName": SCHEDULER_GROUP_NAME,
            "ScheduleExpression": schedule_expression,
            "ScheduleExpressionTimezone": "UTC",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": NOTIFICATION_LAMBDA_ARN,
                "RoleArn": SCHEDULER_ROLE_ARN,
                "Input": target_input,
            },
            "State": "ENABLED",
        }
        if not is_recurring:
            kwargs["ActionAfterCompletion"] = "DELETE"

        response = scheduler_client.update_schedule(**kwargs)
        schedule_arn: str = response.get("ScheduleArn", "")
        logger.info(
            "Đã cập nhật EventBridge schedule '%s', ARN: %s",
            schedule_name,
            schedule_arn,
        )
        return schedule_arn
    except ClientError as exc:
        logger.error(
            "Lỗi khi cập nhật EventBridge schedule '%s': %s",
            schedule_name,
            exc,
        )
        raise


def _delete_eventbridge_schedule(
    scheduler_client: Any,
    schedule_name: str,
) -> None:
    """
    Xóa schedule khỏi EventBridge Scheduler.
    Bỏ qua lỗi ResourceNotFoundException vì schedule có thể đã tự xóa (one-time).
    """
    try:
        scheduler_client.delete_schedule(
            Name=schedule_name,
            GroupName=SCHEDULER_GROUP_NAME,
        )
        logger.info("Đã xóa EventBridge schedule '%s'.", schedule_name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            # Schedule đã bị xóa trước đó (one-time đã trigger), không cần xử lý
            logger.info(
                "Schedule '%s' không tồn tại trên EventBridge, có thể đã tự xóa sau khi trigger.",
                schedule_name,
            )
        else:
            logger.error(
                "Lỗi khi xóa EventBridge schedule '%s': %s",
                schedule_name,
                exc,
            )
            raise


def _disable_eventbridge_schedule(
    scheduler_client: Any,
    schedule_name: str,
) -> None:
    """
    Vô hiệu hóa (DISABLED) schedule trên EventBridge thay vì xóa hẳn.
    Dùng khi task hoàn thành hoặc cần tạm dừng reminder.
    """
    try:
        # Lấy thông tin schedule hiện tại để cập nhật đúng target
        get_response = scheduler_client.get_schedule(
            Name=schedule_name,
            GroupName=SCHEDULER_GROUP_NAME,
        )
        scheduler_client.update_schedule(
            Name=schedule_name,
            GroupName=SCHEDULER_GROUP_NAME,
            ScheduleExpression=get_response["ScheduleExpression"],
            ScheduleExpressionTimezone=get_response.get("ScheduleExpressionTimezone", "UTC"),
            FlexibleTimeWindow=get_response["FlexibleTimeWindow"],
            Target=get_response["Target"],
            State="DISABLED",
        )
        logger.info("Đã vô hiệu hóa EventBridge schedule '%s'.", schedule_name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            logger.info(
                "Schedule '%s' không tồn tại, bỏ qua việc vô hiệu hóa.",
                schedule_name,
            )
        else:
            logger.error(
                "Lỗi khi vô hiệu hóa EventBridge schedule '%s': %s",
                schedule_name,
                exc,
            )
            raise


async def list_reminders_for_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> list[Reminder]:
    """
    Lấy danh sách tất cả reminder của một task.
    Kiểm tra quyền truy cập trước khi trả kết quả.
    """
    # Xác nhận task tồn tại và thuộc về user
    await _get_task_or_raise(db, task_id, user_id)

    stmt = (
        select(Reminder)
        .where(
            Reminder.task_id == task_id,
            Reminder.is_deleted == False,  # noqa: E712
        )
        .order_by(Reminder.created_at.asc())
    )
    result = await db.execute(stmt)
    reminders = list(result.scalars().all())
    return reminders


async def create_reminder(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    payload: ReminderCreate,
) -> Reminder:
    """
    Tạo reminder mới cho task.
    Quy trình:
    1. Kiểm tra task tồn tại và có deadline (với one-time reminder).
    2. Kiểm tra giới hạn 3 reminder/task.
    3. Validate thời điểm nhắc nhở phải trước deadline và trong tương lai.
    4. Lưu reminder vào DB.
    5. Tạo schedule trên EventBridge Scheduler.
    6. Cập nhật schedule_arn vào DB.
    """
    from fastapi import HTTPException, status

    # Bước 1: Lấy task và kiểm tra quyền
    task = await _get_task_or_raise(db, task_id, user_id)

    # Kiểm tra task chưa completed - không nên tạo reminder cho task đã xong
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Không thể tạo nhắc nhở cho task đã hoàn thành.",
        )

    # Với one-time reminder, task BẮT BUỘC phải có deadline
    if payload.reminder_type == ReminderType.ONE_TIME:
        if task.deadline is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task cần có deadline để tạo nhắc nhở một lần (one-time reminder).",
            )
        if payload.scheduled_at is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Trường 'scheduled_at' là bắt buộc với nhắc nhở one-time.",
            )

        now_utc = datetime.now(timezone.utc)
        scheduled_at_utc = (
            payload.scheduled_at.astimezone(timezone.utc)
            if payload.scheduled_at.tzinfo
            else payload.scheduled_at.replace(tzinfo=timezone.utc)
        )

        # Thời điểm nhắc nhở phải trong tương lai
        if scheduled_at_utc <= now_utc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Thời điểm nhắc nhở phải là thời điểm trong tương lai.",
            )

        # Thời điểm nhắc nhở phải trước hoặc bằng deadline
        deadline_utc = (
            task.deadline.astimezone(timezone.utc)
            if task.deadline.tzinfo
            else task.deadline.replace(tzinfo=timezone.utc)
        )
        if scheduled_at_utc > deadline_utc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Thời điểm nhắc nhở không được sau deadline của task.",
            )

    # Với recurring reminder, cron_expression là bắt buộc
    if payload.reminder_type == ReminderType.RECURRING:
        if not payload.cron_expression:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Trường 'cron_expression' là bắt buộc với nhắc nhở định kỳ (recurring).",
            )

    # Bước 2: Kiểm tra giới hạn 3 reminder/task
    current_count = await _count_reminders_for_task(db, task_id)
    if current_count >= MAX_REMINDERS_PER_TASK:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Mỗi task chỉ được đặt tối đa {MAX_REMINDERS_PER_TASK} nhắc nhở.",
        )

    # Bước 3: Tạo đối tượng Reminder trong DB (chưa có schedule_arn)
    new_reminder = Reminder(
        task_id=task_id,
        user_id=user_id,
        reminder_type=payload.reminder_type,
        channel=payload.channel,
        scheduled_at=payload.scheduled_at if payload.reminder_type == ReminderType.ONE_TIME else None,
        cron_expression=payload.cron_expression if payload.reminder_type == ReminderType.RECURRING else None,
        note=payload.note,
        is_deleted=False,
        schedule_arn=None,
    )
    db.add(new_reminder)
    await db.flush()  # Lấy reminder.id trước khi commit
    await db.refresh(new_reminder)

    # Bước 4: Tạo schedule trên EventBridge Scheduler
    schedule_arn: str = ""
    try:
        scheduler_client = _get_scheduler_client()
        schedule_name = _build_schedule_name(new_reminder.id)
        target_input = _build_target_input(new_reminder.id, task_id, user_id)
        is_recurring = payload.reminder_type == ReminderType.RECURRING

        if is_recurring:
            schedule_expression = _build_cron_expression(payload.cron_expression)  # type: ignore[arg-type]
        else:
            schedule_expression = _build_at_expression(payload.scheduled_at)  # type: ignore[arg-type]

        schedule_arn = _create_eventbridge_schedule(
            scheduler_client=scheduler_client,
            schedule_name=schedule_name,
            schedule_expression=schedule_expression,
            target_input=target_input,
            reminder_id=new_reminder.id,
            is_recurring=is_recurring,
        )
    except Exception as exc:
        # Nếu tạo schedule thất bại, log lỗi nhưng vẫn giữ reminder trong DB
        # để retry sau nếu cần. Schedule_arn sẽ là empty string.
        logger.error(
            "Không thể tạo EventBridge schedule cho reminder %d: %s. "
            "Reminder đã được lưu vào DB nhưng chưa có schedule.",
            new_reminder.id,
            exc,
        )

    # Bước 5: Cập nhật schedule_arn vào reminder
    new_reminder.schedule_arn = schedule_arn
    await db.commit()
    await db.refresh(new_reminder)

    logger.info(
        "Đã tạo reminder %d cho task %d của user %d. Type: %s, Channel: %s",
        new_reminder.id,
        task_id,
        user_id,
        payload.reminder_type,
        payload.channel,
    )
    return new_reminder


async def update_reminder(
    db: AsyncSession,
    reminder_id: int,
    user_id: int,
    payload: ReminderUpdate,
) -> Reminder:
    """
    Cập nhật reminder hiện có.
    Nếu thay đổi thời điểm/cron, cập nhật lại schedule trên EventBridge.
    Nếu thay đổi channel, chỉ cần update DB (không ảnh hưởng schedule).
    """
    from fastapi import HTTPException, status

    # Lấy reminder và kiểm tra quyền
    reminder = await _get_reminder_or_raise(db, reminder_id, user_id)

    # Lấy task để validate
    task_stmt = select(Task).where(Task.id == reminder.task_id)
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()
    if task is None or task.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task liên kết với reminder này không còn tồn tại.",
        )

    # Không cho phép cập nhật reminder của task đã hoàn thành
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Không thể cập nhật nhắc nhở cho task đã hoàn thành.",
        )

    schedule_needs_update = False
    new_schedule_expression: str | None = None

    # Cập nhật scheduled_at cho one-time reminder
    if payload.scheduled_at is not None and reminder.reminder_type == ReminderType.ONE_TIME:
        now_utc = datetime.now(timezone.utc)
        scheduled_at_utc = (
            payload.scheduled_at.astimezone(timezone.utc)
            if payload.scheduled_at.tzinfo
            else payload.scheduled_at.replace(tzinfo=timezone.utc)
        )
        if scheduled_at_utc <= now_utc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Thời điểm nhắc nhở phải là thời điểm trong tương lai.",
            )
        if task.deadline:
            deadline_utc = (
                task.deadline.astimezone(timezone.utc)
                if task.deadline.tzinfo
                else task.deadline.replace(tzinfo=timezone.utc)
            )
            if scheduled_at_utc > deadline_utc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Thời điểm nhắc nhở không được sau deadline của task.",
                )
        reminder.scheduled_at = payload.scheduled_at
        new_schedule_expression = _build_at_expression(payload.scheduled_at)
        schedule_needs_update = True

    # Cập nhật cron_expression cho recurring reminder
    if payload.cron_expression is not None and reminder.reminder_type == ReminderType.RECURRING:
        reminder.cron_expression = payload.cron_expression
        new_schedule_expression = _build_cron_expression(payload.cron_expression)
        schedule_needs_update = True

    # Cập nhật channel nếu có
    if payload.channel is not None:
        reminder.channel = payload.channel

    # Cập nhật note nếu có
    if payload.note is not None:
        reminder.note = payload.note

    # Cập nhật schedule trên EventBridge nếu cần
    if schedule_needs_update and new_schedule_expression is not None:
        try:
            scheduler_client = _get_scheduler_client()
            schedule_name = _build_schedule_name(reminder_id)
            target_input = _build_target_input(reminder_id, reminder.task_id, user_id)
            is_recurring = reminder.reminder_type == ReminderType.RECURRING

            # Kiểm tra xem schedule có tồn tại không
            schedule_exists = True
            try:
                scheduler_client.get_schedule(
                    Name=schedule_name,
                    GroupName=SCHEDULER_GROUP_NAME,
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                    schedule_exists = False
                else:
                    raise

            if schedule_exists:
                new_arn = _update_eventbridge_schedule(
                    scheduler_client=scheduler_client,
                    schedule_name=schedule_name,
                    schedule_expression=new_schedule_expression,
                    target_input=target_input,
                    is_recurring=is_recurring,
                )
            else:
                # Schedule đã bị xóa (one-time đã trigger), tạo lại
                new_arn = _create_eventbridge_schedule(
                    scheduler_client=scheduler_client,
                    schedule_name=schedule_name,
                    schedule_expression=new_schedule_expression,
                    target_input=target_input,
                    reminder_id=reminder_id,
                    is_recurring=is_recurring,
                )
            reminder.schedule_arn = new_arn
        except Exception as exc:
            logger.error(
                "Không thể cập nhật EventBridge schedule cho reminder %d: %s",
                reminder_id,
                exc,
            )
            # Tiếp tục lưu thay đổi vào DB dù schedule update thất bại

    reminder.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(reminder)

    logger.info("Đã cập nhật reminder %d.", reminder_id)
    return reminder


async def delete_reminder(
    db: AsyncSession,
    reminder_id: int,
    user_id: int,
) -> None:
    """
    Xóa mềm reminder (is_deleted=True) và xóa schedule tương ứng trên EventBridge.
    """
    # Lấy reminder và kiểm tra quyền
    reminder = await _get_reminder_or_raise(db, reminder_id, user_id)

    # Soft delete reminder trong DB
    reminder.is_deleted = True
    reminder.updated_at = datetime.now(timezone.utc)

    # Xóa schedule trên EventBridge
    if reminder.schedule_arn:
        try:
            scheduler_client = _get_scheduler_client()
            schedule_name = _build_schedule_name(reminder_id)
            _delete_eventbridge_schedule(scheduler_client, schedule_name)
        except Exception as exc:
            logger.error(
                "Không thể xóa EventBridge schedule cho reminder %d: %s",
                reminder_id,
                exc,
            )
            # Tiếp tục xóa mềm trong DB dù EventBridge delete thất bại

    await db.commit()
    logger.info("Đã xóa mềm reminder %d.", reminder_id)


async def cancel_reminders_for_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> None:
    """
    Hủy tất cả reminder đang active của một task (khi task hoàn thành hoặc bị xóa).
    Vô hiệu hóa schedule trên EventBridge thay vì xóa hẳn để có thể khôi phục.
    """
    stmt = select(Reminder).where(
        Reminder.task_id == task_id,
        Reminder.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    reminders = list(result.scalars().all())

    if not reminders:
        return

    scheduler_client = _get_scheduler_client()

    for reminder in reminders:
        if reminder.schedule_arn:
            try:
                schedule_name = _build_schedule_name(reminder.id)
                _disable_eventbridge_schedule(scheduler_client, schedule_name)
            except Exception as exc:
                logger.error(
                    "Không thể vô hiệu hóa schedule cho reminder %d: %s",
                    reminder.id,
                    exc,
                )

    logger.info(
        "Đã vô hiệu hóa %d reminder cho task %d.",
        len(reminders),
        task_id,
    )


async def restore_reminders_for_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> None:
    """
    Kích hoạt lại các reminder của task khi task được undo (khôi phục từ completed/deleted).
    Chỉ kích hoạt lại các reminder có scheduled_at trong tương lai (với one-time).
    """
    stmt = select(Reminder).where(
        Reminder.task_id == task_id,
        Reminder.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    reminders = list(result.scalars().all())

    if not reminders:
        return

    scheduler_client = _get_scheduler_client()
    now_utc = datetime.now(timezone.utc)

    for reminder in reminders:
        # Với one-time reminder, chỉ kích hoạt lại nếu scheduled_at còn trong tương lai
        if reminder.reminder_type == ReminderType.ONE_TIME:
            if reminder.scheduled_at is None:
                continue
            scheduled_at_utc = (
                reminder.scheduled_at.astimezone(timezone.utc)
                if reminder.scheduled_at.tzinfo
                else reminder.scheduled_at.replace(tzinfo=timezone.utc)
            )
            if scheduled_at_utc <= now_utc:
                # Thời điểm nhắc nhở đã qua, không thể kích hoạt lại
                logger.info(
                    "Reminder %d đã qua thời điểm nhắc nhở, bỏ qua kích hoạt lại.",
                    reminder.id,
                )
                continue

        if not reminder.schedule_arn:
            # Schedule chưa được tạo, tạo mới
            try:
                schedule_name = _build_schedule_name(reminder.id)
                target_input = _build_target_input(reminder.id, task_id, user_id)
                is_recurring = reminder.reminder_type == ReminderType.RECURRING

                if is_recurring and reminder.cron_expression:
                    schedule_expression = _build_cron_expression(reminder.cron_expression)
                elif not is_recurring and reminder.scheduled_at:
                    schedule_expression = _build_at_expression(reminder.scheduled_at)
                else:
                    continue

                new_arn = _create_eventbridge_schedule(
                    scheduler_client=scheduler_client,
                    schedule_name=schedule_name,
                    schedule_expression=schedule_expression,
                    target_input=target_input,
                    reminder_id=reminder.id,
                    is_recurring=is_recurring,
                )
                reminder.schedule_arn = new_arn
            except Exception as exc:
                logger.error(
                    "Không thể tạo lại schedule cho reminder %d: %s",
                    reminder.id,
                    exc,
                )
            continue

        # Kích hoạt lại schedule đang DISABLED
        try:
            schedule_name = _build_schedule_name(reminder.id)
            get_response = scheduler_client.get_schedule(
                Name=schedule_name,
                GroupName=SCHEDULER_GROUP_NAME,
            )
            scheduler_client.update_schedule(
                Name=schedule_name,
                GroupName=SCHEDULER_GROUP_NAME,
                ScheduleExpression=get_response["ScheduleExpression"],
                ScheduleExpressionTimezone=get_response.get("ScheduleExpressionTimezone", "UTC"),
                FlexibleTimeWindow=get_response["FlexibleTimeWindow"],
                Target=get_response["Target"],
                State="ENABLED",
            )
            logger.info("Đã kích hoạt lại schedule cho reminder %d.", reminder.id)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                # Schedule không còn tồn tại, tạo lại
                try:
                    schedule_name = _build_schedule_name(reminder.id)
                    target_input = _build_target_input(reminder.id, task_id, user_id)
                    is_recurring = reminder.reminder_type == ReminderType.RECURRING

                    if is_recurring and reminder.cron_expression:
                        schedule_expression = _build_cron_expression(reminder.cron_expression)
                    elif not is_recurring and reminder.scheduled_at:
                        schedule_expression = _build_at_expression(reminder.scheduled_at)
                    else:
                        continue

                    new_arn = _create_eventbridge_schedule(
                        scheduler_client=scheduler_client,
                        schedule_name=schedule_name,
                        schedule_expression=schedule_expression,
                        target_input=target_input,
                        reminder_id=reminder.id,
                        is_recurring=is_recurring,
                    )
                    reminder.schedule_arn = new_arn
                except Exception as inner_exc:
                    logger.error(
                        "Không thể tạo lại schedule cho reminder %d: %s",
                        reminder.id,
                        inner_exc,
                    )
            else:
                logger.error(
                    "Lỗi khi kích hoạt lại schedule cho reminder %d: %s",
                    reminder.id,
                    exc,
                )

    await db.commit()
    logger.info(
        "Đã xử lý kích hoạt lại reminder cho task %d.",
        task_id,
    )


async def update_reminders_on_deadline_change(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    new_deadline: datetime,
) -> None:
    """
    Khi deadline của task thay đổi, cập nhật lại tất cả one-time reminder
    mà scheduled_at > new_deadline để tránh nhắc nhở sau deadline mới.
    - Nếu scheduled_at > new_deadline: xóa mềm reminder và hủy schedule.
    - Nếu scheduled_at <= new_deadline: giữ nguyên.
    Recurring reminder không bị ảnh hưởng bởi deadline.
    """
    stmt = select(Reminder).where(
        Reminder.task_id == task_id,
        Reminder.is_deleted == False,  # noqa: E712
        Reminder.reminder_type == ReminderType.ONE_TIME,
    )
    result = await db.execute(stmt)
    reminders = list(result.scalars().all())

    if not reminders:
        return

    deadline_utc = (
        new_deadline.astimezone(timezone.utc)
        if new_deadline.tzinfo
        else new_deadline.replace(tzinfo=timezone.utc)
    )
    now_utc = datetime.now(timezone.utc)
    scheduler_client = _get_scheduler_client()

    for reminder in reminders:
        if reminder.scheduled_at is None:
            continue

        scheduled_at_utc = (
            reminder.scheduled_at.astimezone(timezone.utc)
            if reminder.scheduled_at.tzinfo
            else reminder.scheduled_at.replace(tzinfo=timezone.utc)
        )

        # Nếu scheduled_at vượt qua deadline mới hoặc đã trong quá khứ, xóa reminder
        if scheduled_at_utc > deadline_utc or scheduled_at_utc <= now_utc:
            reminder.is_deleted = True
            reminder.updated_at = datetime.now(timezone.utc)

            if reminder.schedule_arn:
                try:
                    schedule_name = _build_schedule_name(reminder.id)
                    _delete_eventbridge_schedule(scheduler_client, schedule_name)
                except Exception as exc:
                    logger.error(
                        "Không thể xóa schedule cho reminder %d khi deadline thay đổi: %s",
                        reminder.id,
                        exc,
                    )

            logger.info(
                "Đã xóa reminder %d vì scheduled_at vượt quá deadline mới của task %d.",
                reminder.id,
                task_id,
            )

    await db.commit()


async def delete_all_reminders_for_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
) -> None:
    """
    Xóa mềm TẤT CẢ reminder của một task và hủy toàn bộ schedule trên EventBridge.
    Dùng khi task bị xóa vĩnh viễn.
    """
    stmt = select(Reminder).where(
        Reminder.task_id == task_id,
        Reminder.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    reminders = list(result.scalars().all())

    if not reminders:
        return

    scheduler_client = _get_scheduler_client()
    now_utc = datetime.now(timezone.utc)

    for reminder in reminders:
        reminder.is_deleted = True
        reminder.updated_at = now_utc

        if reminder.schedule_arn:
            try:
                schedule_name = _build_schedule_name(reminder.id)
                _delete_eventbridge_schedule(scheduler_client, schedule_name)
            except Exception as exc:
                logger.error(
                    "Không thể xóa schedule cho reminder %d khi xóa task: %s",
                    reminder.id,
                    exc,
                )

    await db.commit()
    logger.info(
        "Đã xóa mềm %d reminder và hủy schedule cho task %d.",
        len(reminders),
        task_id,
    )


async def get_reminder_by_id(
    db: AsyncSession,
    reminder_id: int,
    user_id: int,
) -> Reminder:
    """
    Lấy thông tin chi tiết của một reminder theo ID.
    Kiểm tra quyền truy cập của user.
    """
    return await _get_reminder_or_raise(db, reminder_id, user_id)


async def retry_schedule_creation(
    db: AsyncSession,
    reminder_id: int,
    user_id: int,
) -> Reminder:
    """
    Thử tạo lại EventBridge schedule cho reminder đã bị lỗi khi tạo lần đầu.
    Hữu ích khi AWS tạm thời không khả dụng lúc tạo reminder.
    """
    from fastapi import HTTPException, status

    reminder = await _get_reminder_or_raise(db, reminder_id, user_id)

    if reminder.schedule_arn:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Reminder này đã có schedule ARN, không cần tạo lại.",
        )

    is_recurring = reminder.reminder_type == ReminderType.RECURRING

    if is_recurring:
        if not reminder.cron_expression:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Reminder định kỳ thiếu cron_expression.",
            )
        schedule_expression = _build_cron_expression(reminder.cron_expression)
    else:
        if not reminder.scheduled_at:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Reminder one-time thiếu scheduled_at.",
            )
        now_utc = datetime.now(timezone.utc)
        scheduled_at_utc = (
            reminder.scheduled_at.astimezone(timezone.utc)
            if reminder.scheduled_at.tzinfo
            else reminder.scheduled_at.replace(tzinfo=timezone.utc)
        )
        if scheduled_at_utc <= now_utc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Thời điểm nhắc nhở đã qua, không thể tạo lại schedule.",
            )
        schedule_expression = _build_at_expression(reminder.scheduled_at)

    scheduler_client = _get_scheduler_client()
    schedule_name = _build_schedule_name(reminder_id)
    target_input = _build_target_input(reminder_id, reminder.task_id, user_id)

    new_arn = _create_eventbridge_schedule(
        scheduler_client=scheduler_client,
        schedule_name=schedule_name,
        schedule_expression=schedule_expression,
        target_input=target_input,
        reminder_id=reminder_id,
        is_recurring=is_recurring,
    )

    reminder.schedule_arn = new_arn
    reminder.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(reminder)

    logger.info(
        "Đã tạo lại EventBridge schedule cho reminder %d. ARN: %s",
        reminder_id,
        new_arn,
    )
    return reminder