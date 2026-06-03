import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

import notification_service

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 15 * 60

# Thời gian lưu record để audit (REQ-7.6, REQ-12.3) — tách biệt với thời hạn approve
AUDIT_RETENTION_SECONDS = 90 * 24 * 60 * 60  # 90 ngày

# State machine: các trạng thái terminal (chỉ từ PENDING)
# PENDING → APPROVED | REJECTED | TIMED_OUT | SEND_FAILED
# Dùng bởi approval_handler để chặn chuyển trạng thái khi record đã ở terminal state
_TERMINAL_STATES = frozenset({"APPROVED", "REJECTED", "TIMED_OUT", "SEND_FAILED"})

# Allowlist các field bắt buộc theo từng action_type để approval_handler dispatch an toàn.
# Caller phải cung cấp đúng và đủ các keys này; mọi key ngoài danh sách bị từ chối.
# Dùng frozenset vì đây là constant — không được thêm/xóa runtime.
ACTION_PARAMETERS_ALLOWLIST: dict[str, frozenset[str]] = {
    "stop_ec2":             frozenset({"instance_id", "region"}),
    "revert_s3_to_private": frozenset({"bucket_name", "region"}),
    "deactivate_iam_key":   frozenset({"username", "access_key_id"}),
    "tag_ebs_noncompliant": frozenset({"volume_id", "region"}),
}

# Map từng action_type sang parameter đại diện resource được thực thi.
# Phải khớp với violation["resource_id"] để ngăn confused-deputy attack (REQ-7.1, REQ-7.3).
# Parameter này được hiển thị rõ trên Slack để người duyệt biết chính xác target.
ACTION_TARGET_PARAM: dict[str, str] = {
    "stop_ec2":             "instance_id",
    "revert_s3_to_private": "bucket_name",
    "deactivate_iam_key":   "access_key_id",
    "tag_ebs_noncompliant": "volume_id",
}

# Map action_type → violation_type hợp lệ (contract thực tế từ guardrail modules).
# Ngăn confused-deputy: EC2 violation không thể trigger action deactivate_iam_key,
# kể cả khi target param và resource_id đều khớp tình cờ.
# Không có catch-all — high-risk workflow phải fail-closed: violation_type lạ bị từ chối.
# (multi-account): approval record nên lưu account_id để tránh nhầm resource
#   cùng ID ở account khác. Cần event_processor truyền account_id vào violation dict
#   trước khi implement — xem REQ-7.6, REQ-12.3.
ACTION_VIOLATION_TYPES: dict[str, frozenset[str]] = {
    "stop_ec2":             frozenset({"missing_required_tags"}),
    "revert_s3_to_private": frozenset({"public_s3_access"}),
    "deactivate_iam_key":   frozenset({"iam_access_key_created"}),
    "tag_ebs_noncompliant": frozenset({"unencrypted_ebs_volume"}),
}

# Map action_type → resource_type hợp lệ (contract thực tế từ guardrail và remediation modules).
# Kiểm tra song song với ACTION_VIOLATION_TYPES — cả hai phải khớp mới cho phép qua.
ACTION_RESOURCE_TYPES: dict[str, str] = {
    "stop_ec2":             "ec2",
    "revert_s3_to_private": "s3",
    "deactivate_iam_key":   "iam_access_key",
    "tag_ebs_noncompliant": "ebs",
}


def _validate_action_parameters(
    action_type: str,
    action_parameters: dict,
    violation_resource_id: str,
    violation_region: str,
    violation_type: str,
    violation_resource_type: str,
) -> dict | None:
    """
    Validate và sanitize action_parameters theo ACTION_PARAMETERS_ALLOWLIST.

    Kiểm tra theo thứ tự:
    1. action_type có trong allowlist
    2. action_parameters là dict
    3. Đủ required keys
    4. Không có extra keys
    5. Mọi value là str không rỗng
    6. Target resource khớp violation_resource_id (chống confused-deputy)
    7. Region khớp violation_region (nếu action có param region)
    8. violation_type khớp ACTION_VIOLATION_TYPES (chống action–violation mismatch)
    9. violation_resource_type khớp ACTION_RESOURCE_TYPES (kiểm tra kép)

    Args:
        violation_type: violation_type của violation gốc theo contract guardrail.
                        Không được rỗng — high-risk workflow fail-closed.
        violation_resource_type: resource_type của violation gốc.
                        Không được rỗng — kiểm tra kép với violation_type.

    Returns:
        dict chứa chỉ các key được phép nếu hợp lệ.
        None nếu validation thất bại.
    """
    # 1. action_type phải nằm trong allowlist
    if action_type not in ACTION_PARAMETERS_ALLOWLIST:
        logger.error(
            "Invalid action_type %r — not in ACTION_PARAMETERS_ALLOWLIST. "
            "Valid types: %s",
            action_type,
            list(ACTION_PARAMETERS_ALLOWLIST),
        )
        return None

    # 2. action_parameters phải là dict
    if not isinstance(action_parameters, dict):
        logger.error(
            "action_parameters must be a dict, got %s",
            type(action_parameters).__name__,
        )
        return None

    required_keys = ACTION_PARAMETERS_ALLOWLIST[action_type]

    # 3. Kiểm tra đủ required parameters
    missing = required_keys - action_parameters.keys()
    if missing:
        logger.error(
            "action_parameters for %r missing required keys: %s",
            action_type,
            sorted(missing),
        )
        return None

    # 4. Từ chối các parameter không nằm trong allowlist (chống data injection)
    extra = action_parameters.keys() - required_keys
    if extra:
        logger.error(
            "action_parameters for %r contains disallowed keys: %s",
            action_type,
            sorted(extra),
        )
        return None

    # 5. Mọi value phải là str không rỗng
    for key in required_keys:
        val = action_parameters[key]
        if not isinstance(val, str) or not val.strip():
            logger.error(
                "action_parameters[%r] must be a non-empty string, got %r",
                key,
                val,
            )
            return None

    # 6. Target resource phải khớp violation resource_id — ngăn confused-deputy attack
    target_param = ACTION_TARGET_PARAM[action_type]
    actual_target = action_parameters[target_param]
    if actual_target != violation_resource_id:
        logger.error(
            "Target mismatch for %r: action_parameters[%r]=%r does not match "
            "violation resource_id=%r",
            action_type,
            target_param,
            actual_target,
            violation_resource_id,
        )
        return None

    # 7. Region phải khớp violation region (nếu action có param region)
    if "region" in required_keys:
        actual_region = action_parameters["region"]
        if actual_region != violation_region:
            logger.error(
                "Region mismatch for %r: action_parameters[region]=%r does not match "
                "violation region=%r",
                action_type,
                actual_region,
                violation_region,
            )
            return None

    # 8. violation_type phải khớp ACTION_VIOLATION_TYPES — fail-closed, không bỏ qua khi rỗng
    if not violation_type:
        logger.error(
            "violation_type is empty for action %r — high-risk workflow requires explicit "
            "violation_type to prevent action–violation mismatch",
            action_type,
        )
        return None

    allowed_violation_types = ACTION_VIOLATION_TYPES.get(action_type, frozenset())
    if violation_type not in allowed_violation_types:
        logger.error(
            "Action–violation type mismatch for %r: violation_type=%r is not in "
            "allowed set %s",
            action_type,
            violation_type,
            sorted(allowed_violation_types),
        )
        return None

    # 9. violation_resource_type phải khớp ACTION_RESOURCE_TYPES — kiểm tra kép
    if not violation_resource_type:
        logger.error(
            "violation_resource_type is empty for action %r — required for resource type check",
            action_type,
        )
        return None

    expected_resource_type = ACTION_RESOURCE_TYPES.get(action_type, "")
    if violation_resource_type != expected_resource_type:
        logger.error(
            "Resource type mismatch for %r: violation resource_type=%r, expected %r",
            action_type,
            violation_resource_type,
            expected_resource_type,
        )
        return None

    # Trả về sanitized dict — chỉ giữ lại các key được phép
    return {k: action_parameters[k] for k in required_keys}


def send_approval_request(
    violation: dict,
    remediation_action: str,
    action_type: str,
    action_parameters: dict,
) -> str | None:
    """
    Tạo và gửi Slack approval request cho high-risk remediation.

    Args:
        violation: vi phạm được phát hiện (chỉ các field whitelist được lưu)
        remediation_action: mô tả người dùng đọc được (hiển trên Slack)
        action_type: mã ổn định để approval_handler dispatch — phải có trong ACTION_PARAMETERS_ALLOWLIST
        action_parameters: tham số cần cho remediation function — phải đủ và đúng key theo action_type

    Bám requirements:
    - REQ-7.1: gửi approval request cho high-risk action
    - REQ-7.2: hiển thị nút Approve và Reject
    - REQ-7.3: thực thi remediation khi Approve trong 15 phút (tại đây đảm bảo target hiển thị đúng, logic thực thi ở approval_handler)
    - REQ-7.5: request hết hạn sau 15 phút
    """
    # Validate violation là dict trước khi gọi .get()
    if not isinstance(violation, dict):
        logger.error(
            "violation must be a dict, got %s",
            type(violation).__name__,
        )
        return None

    # Validate resource_id và remediation_action
    resource_id = violation.get("resource_id")
    if not resource_id or not remediation_action:
        logger.error(
            "Validation failed: resource_id=%r, remediation_action=%r",
            resource_id,
            remediation_action,
        )
        return None

    violation_region = violation.get("region", "")
    violation_type = violation.get("violation_type", "")
    violation_resource_type = violation.get("resource_type", "")

    # Validate, sanitize và kiểm tra target consistency trước khi tạo record.
    # Truyền violation_type + violation_resource_type để kiểm tra action–violation match
    # (REQ-7.1): ngăn EC2 violation trigger action deactivate_iam_key, v.v.
    sanitized_params = _validate_action_parameters(
        action_type, action_parameters, resource_id, violation_region,
        violation_type=violation_type,
        violation_resource_type=violation_resource_type,
    )
    if sanitized_params is None:
        return None

    request_id = str(uuid.uuid4())
    now = int(time.time())
    expires_at = now + APPROVAL_TIMEOUT_SECONDS
    created_at_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    expires_at_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()

    # Chỉ lưu các field cần thiết cho remediation và audit (REQ-12.3)
    # Không lưu violation đầy đủ vì có thể chứa raw_event, policy nhạy cảm, hoặc > 400KB
    record = {
        "request_id": request_id,
        "resource_id": resource_id,
        "resource_type": violation.get("resource_type", "unknown"),
        "region": violation.get("region", "unknown"),
        "violation_type": violation.get("violation_type", "unknown"),
        "severity": violation.get("severity", "unknown"),
        # Mô tả hiển thị trên Slack — không dùng để dispatch logic
        "remediation_action": remediation_action,
        # Mã dispatch ổn định — đã validate qua ACTION_PARAMETERS_ALLOWLIST
        "action_type": action_type,
        # Chỉ chứa các field được allowlist và đã sanitize — không có extra keys
        "action_parameters": sanitized_params,
        "status": "PENDING",
        "created_at": created_at_iso,
        # ISO string để hiển thị cho người dùng trên Slack
        "expires_at": expires_at_iso,
        # Epoch integer để approval_handler so sánh thời gian trực tiếp (không cần parse)
        "expires_at_epoch": expires_at,
        # DynamoDB TTL — giữ record 90 ngày cho audit (REQ-7.6, REQ-12.3)
        # Tách biệt với expires_at_epoch (cửa sổ phê duyệt 15 phút)
        "ttl": now + AUDIT_RETENTION_SECONDS,
    }


    try:
        _save_pending_request(record)
    except (ClientError, BotoCoreError) as exc:
        # ClientError: lỗi API cụ thể (ProvisionedThroughputExceeded, ConditionalCheckFailed...)
        # BotoCoreError: lỗi infrastructure (thiếu region, connection timeout, SSL...)
        # Cả hai đều là lỗi mong đợi — không nên crash Lambda (REQ-13.1)
        error_code = (
            exc.response["Error"]["Code"]
            if isinstance(exc, ClientError)
            else type(exc).__name__
        )
        logger.error(
            "Failed to save approval request %s to DynamoDB: %s",
            request_id,
            error_code,
        )
        return None

    # target_param là resource được thực thi — hiển rõ trên Slack để người duyệt xác nhận.
    # REQ-7.3 (approval_handler): thực thi remediation khi Approve trong 15 phút.
    # Tại đây chỉ đảm bảo target đúng được hiển thị — logic thực thi thuộc approval_handler.
    target_param = ACTION_TARGET_PARAM[action_type]
    target_value = sanitized_params[target_param]

    payload = {
        # Fallback text hiển thị trên notifications / clients không hỗ trợ Block Kit
        "text": (
            f"⚠️ Approval required: [{action_type}] on {target_value}"
        ),
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"⚠️ *Yêu cầu phê duyệt hành động nguy hiểm*\n"
                        f"Mô tả: *{remediation_action}*\n"
                        f"Action type: `{action_type}`\n"
                        # Hiển target đã sanitize — đây là resource sẽ thực sự được xử lý
                        f"Target ({target_param}): `{target_value}`\n"
                        f"Request ID: `{request_id}`\n"
                        f"Hết hạn lúc: `{expires_at_iso}`"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        # value chứa request_id để approval_handler tra cứu DynamoDB
                        "value": request_id,
                        "action_id": "approve_action",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "value": request_id,
                        "action_id": "reject_action",
                    },
                ],
            },
        ],
    }

    success = notification_service.send_slack_payload(payload)

    if not success:
        _update_request_status(request_id, "SEND_FAILED")
        return None

    logger.info(
        "Approval request sent: request_id=%s resource_id=%s action=%s expires_at=%s",
        request_id,
        resource_id,
        remediation_action,
        expires_at_iso,
    )

    return request_id


def _save_pending_request(record: dict) -> None:
    """Lưu approval request để approval_handler truy vấn sau này."""
    table_name = os.environ.get("APPROVAL_TABLE_NAME", "cloudops-approval-requests")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    table.put_item(
        Item=record,
        # ConditionExpression bảo vệ tính toàn vẹn: không ghi đè nếu request_id đã tồn tại
        ConditionExpression="attribute_not_exists(request_id)",
    )


# Allowlist các status mà _update_request_status() được phép set — chỉ trạng thái hệ thống tự quyết định.
# APPROVED và REJECTED thuộc approval_handler.py (Bài 4.4) và cần lưu approver_identity + timestamp.
_SYSTEM_STATUSES = frozenset({"SEND_FAILED", "TIMED_OUT"})


def _update_request_status(request_id: str, status: str) -> None:
    """
    Cập nhật status của approval request — best-effort, không raise exception.

    Chỉ dùng cho SEND_FAILED và TIMED_OUT (các trạng thái do hệ thống tự set).

    KHÔNG dùng cho APPROVED hoặc REJECTED:
      - Hai trạng thái này được set bởi approval_handler.py (Bài 4.4)
        khi người dùng bấm nút trên Slack.
      - Cần lưu thêm approver_identity và approved_at/rejected_at timestamp
        theo REQ-7.6 (ghi danh tính người phê duyệt) và REQ-12.3 (audit log).
      - Dùng hàm này cho APPROVED/REJECTED sẽ mất audit trail bắt buộc.
    """
    # Giới hạn allowlist riêng — không dùng _TERMINAL_STATES để tránh mở cửa cho APPROVED/REJECTED
    if status not in _SYSTEM_STATUSES:
        logger.error(
            "Invalid status %r for _update_request_status — must be one of %s. "
            "Use approval_handler for APPROVED/REJECTED.",
            status,
            sorted(_SYSTEM_STATUSES),
        )
        return

    table_name = os.environ.get("APPROVAL_TABLE_NAME", "cloudops-approval-requests")
    try:
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":pending": "PENDING"},
            # Chỉ ghi đè khi record vẫn còn ở trạng thái PENDING
            # Tránh overwrite APPROVED hoặc REJECTED nếu user đã phản hồi
            ConditionExpression="#s = :pending",
        )
    except (ClientError, BotoCoreError) as exc:
        # Best-effort: log rõ error code nhưng không raise
        # ConditionalCheckFailedException = record đã ở terminal state, không phải lỗi thực sự
        error_code = (
            exc.response["Error"]["Code"]
            if isinstance(exc, ClientError)
            else type(exc).__name__
        )
        logger.warning(
            "Could not update status for request %s to %s: %s",
            request_id,
            status,
            error_code,
        )
