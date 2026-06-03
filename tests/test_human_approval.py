"""
Unit tests cho human_approval.py

Coverage targets:
- send_approval_request: happy path, validation failures, DynamoDB error, Slack failure
- _validate_action_parameters: unknown type, missing params, extra params, non-dict
- _save_pending_request: normal write, condition expression (duplicate guard)
- _update_request_status: conditional update, best-effort on failure
- TTL/expires_at_epoch separation: 90-day audit retention vs 15-min approval window
- action_type + action_parameters: stored, sanitized, validated before save
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

# Thêm shared directory vào path để import trực tiếp như Lambda runtime làm
sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda" / "shared"))

import human_approval


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.com/slack-webhook")
    monkeypatch.setenv("APPROVAL_TABLE_NAME", "test-approval-table")


@pytest.fixture
def sample_violation():
    return {
        "resource_id": "i-1234567890abcdef0",
        "resource_type": "ec2",
        "severity": "high",
        "region": "ap-southeast-1",
        "violation_type": "missing_required_tags",  # contract thực từ guardrail_cost.py
        "message": "Instance thiếu tag Owner và Project",
    }


@pytest.fixture
def ec2_action():
    """action_type và action_parameters hợp lệ cho stop_ec2."""
    return {
        "action_type": "stop_ec2",
        "action_parameters": {"instance_id": "i-1234567890abcdef0", "region": "ap-southeast-1"},
    }


# ---------------------------------------------------------------------------
# Tests: send_approval_request — Input Validation
# ---------------------------------------------------------------------------

def test_send_approval_request_missing_resource_id(mock_env, caplog):
    """Phải return None khi violation không có resource_id."""
    result = human_approval.send_approval_request(
        violation={},
        remediation_action="stop_instance",
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "ap-southeast-1"},
    )
    assert result is None
    assert "Validation failed" in caplog.text


def test_send_approval_request_empty_remediation_action(mock_env, sample_violation, caplog):
    """Phải return None khi remediation_action rỗng."""
    result = human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="",
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-1234567890abcdef0", "region": "ap-southeast-1"},
    )
    assert result is None
    assert "Validation failed" in caplog.text


# ---------------------------------------------------------------------------
# Tests: send_approval_request — Happy Path
# ---------------------------------------------------------------------------

@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_send_approval_request_success(
    mock_send, mock_save, mock_env, sample_violation, ec2_action
):
    """Happy path: lưu DynamoDB, gửi Slack, return request_id."""
    mock_send.return_value = True

    # Tạo manager để kiểm tra thứ tự gọi hàm (lưu DynamoDB phải trước gửi Slack)
    mock_manager = mock.Mock()
    mock_manager.attach_mock(mock_save, "mock_save")
    mock_manager.attach_mock(mock_send, "mock_send")

    result = human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="Dừng EC2 instance",
        **ec2_action,
    )

    assert result is not None
    assert len(result) == 36  # UUID4 format

    mock_save.assert_called_once()
    saved_record = mock_save.call_args[0][0]
    assert saved_record["status"] == "PENDING"
    assert saved_record["resource_id"] == "i-1234567890abcdef0"
    assert saved_record["remediation_action"] == "Dừng EC2 instance"
    assert "ttl" in saved_record
    assert "created_at" in saved_record
    assert "expires_at" in saved_record
    assert "expires_at_epoch" in saved_record
    assert isinstance(saved_record["expires_at_epoch"], int)

    # Không được lưu toàn bộ violation dict
    assert "violation" not in saved_record
    assert saved_record["resource_type"] == "ec2"
    assert saved_record["severity"] == "high"
    assert saved_record["region"] == "ap-southeast-1"
    assert saved_record["violation_type"] == "missing_required_tags"

    # action_type và sanitized params phải được lưu
    assert saved_record["action_type"] == "stop_ec2"
    assert saved_record["action_parameters"] == {
        "instance_id": "i-1234567890abcdef0",
        "region": "ap-southeast-1",
    }

    mock_send.assert_called_once()
    assert saved_record["ttl"] != saved_record["expires_at_epoch"]

    # Kiểm tra thứ tự: lưu DynamoDB phải xảy ra trước gửi Slack
    assert len(mock_manager.mock_calls) >= 2
    assert mock_manager.mock_calls[0][0] == "mock_save"
    assert mock_manager.mock_calls[1][0] == "mock_send"


@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_slack_payload_contains_required_blocks(
    mock_send, mock_save, mock_env, sample_violation, ec2_action
):
    """Payload gửi Slack phải có section block và actions block với Approve + Reject."""
    mock_send.return_value = True

    request_id = human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="Dừng EC2 instance",
        **ec2_action,
    )

    payload = mock_send.call_args[0][0]
    assert "text" in payload
    assert "Dừng EC2 instance" in payload["text"] or "i-1234567890abcdef0" in payload["text"]

    blocks = payload["blocks"]
    block_types = [b["type"] for b in blocks]
    assert "section" in block_types
    assert "actions" in block_types

    actions_block = next(b for b in blocks if b["type"] == "actions")
    action_ids = [el["action_id"] for el in actions_block["elements"]]
    assert "approve_action" in action_ids
    assert "reject_action" in action_ids

    for element in actions_block["elements"]:
        # Cả hai button value (Approve/Reject) phải bằng đúng request_id trả về
        assert element["value"] == request_id


@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_section_block_contains_resource_and_expiry(
    mock_send, mock_save, mock_env, sample_violation, ec2_action
):
    """Section block phải chứa resource_id, action, request_id, expires_at."""
    mock_send.return_value = True

    human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="Dừng EC2 instance",
        **ec2_action,
    )

    payload = mock_send.call_args[0][0]
    section_block = next(b for b in payload["blocks"] if b["type"] == "section")
    section_text = section_block["text"]["text"]

    assert "i-1234567890abcdef0" in section_text
    assert "Dừng EC2 instance" in section_text
    assert "expires_at" in section_text.lower() or "hết hạn" in section_text.lower()


# ---------------------------------------------------------------------------
# Tests: send_approval_request — DynamoDB Failure
# ---------------------------------------------------------------------------

@mock.patch("human_approval._save_pending_request")
def test_dynamodb_client_error_returns_none(mock_save, mock_env, sample_violation, ec2_action, caplog):
    """ClientError từ DynamoDB phải bị bắt, return None và không gửi Slack."""
    from botocore.exceptions import ClientError
    error_response = {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": ""}}
    mock_save.side_effect = ClientError(error_response, "PutItem")

    with mock.patch("human_approval.notification_service.send_slack_payload") as mock_send:
        result = human_approval.send_approval_request(
            violation=sample_violation,
            remediation_action="Dừng EC2",
            **ec2_action,
        )

    assert result is None
    mock_send.assert_not_called()
    assert "Failed to save approval request" in caplog.text


@mock.patch("human_approval._save_pending_request")
def test_dynamodb_botocore_error_returns_none(mock_save, mock_env, sample_violation, ec2_action, caplog):
    """BotoCoreError (thiếu region, SSL, connection timeout) cũng phải bị bắt (REQ-13.1)."""
    from botocore.exceptions import BotoCoreError
    mock_save.side_effect = BotoCoreError()

    with mock.patch("human_approval.notification_service.send_slack_payload") as mock_send:
        result = human_approval.send_approval_request(
            violation=sample_violation,
            remediation_action="Dừng EC2",
            **ec2_action,
        )

    assert result is None
    mock_send.assert_not_called()
    assert "Failed to save approval request" in caplog.text


@mock.patch("human_approval._save_pending_request")
def test_non_botocore_error_propagates(mock_save, mock_env, sample_violation, ec2_action):
    """Exception không phải ClientError/BotoCoreError phải propagate ra ngoài."""
    mock_save.side_effect = RuntimeError("Unexpected error")

    with pytest.raises(RuntimeError):
        human_approval.send_approval_request(
            violation=sample_violation,
            remediation_action="Dừng EC2",
            **ec2_action,
        )


# ---------------------------------------------------------------------------
# Tests: send_approval_request — Slack Send Failure
# ---------------------------------------------------------------------------

@mock.patch("human_approval._update_request_status")
@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_slack_failure_updates_status_and_returns_none(
    mock_send, mock_save, mock_update_status, mock_env, sample_violation, ec2_action
):
    """Nếu Slack gửi thất bại, phải update status SEND_FAILED và return None."""
    mock_send.return_value = False

    result = human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="Dừng EC2",
        **ec2_action,
    )

    assert result is None
    mock_update_status.assert_called_once()
    # Argument đầu tiên là request_id (UUID), thứ hai là status
    _, status = mock_update_status.call_args[0]
    assert status == "SEND_FAILED"


# ---------------------------------------------------------------------------
# Tests: _save_pending_request
# ---------------------------------------------------------------------------

def test_save_pending_request_calls_put_item(mock_env):
    """_save_pending_request phải gọi DynamoDB put_item với ConditionExpression."""
    record = {
        "request_id": "test-uuid-1234",
        "resource_id": "i-abc",
        "status": "PENDING",
        "ttl": 9999999999,
    }

    mock_table = mock.MagicMock()
    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table
        human_approval._save_pending_request(record)

    mock_dynamo.return_value.Table.assert_called_once_with("test-approval-table")
    mock_table.update_item.assert_not_called()
    call_kwargs = mock_table.put_item.call_args[1]

    # Item phải là record gốc
    assert call_kwargs["Item"] == record

    # ConditionExpression phải có để chặn ghi đè (duplicate guard)
    assert "ConditionExpression" in call_kwargs
    assert "attribute_not_exists" in call_kwargs["ConditionExpression"]


def test_save_pending_request_duplicate_raises(mock_env):
    """Phải propagate ConditionalCheckFailedException khi request_id đã tồn tại."""
    from botocore.exceptions import ClientError

    error_response = {
        "Error": {
            "Code": "ConditionalCheckFailedException",
            "Message": "The conditional request failed",
        }
    }

    mock_table = mock.MagicMock()
    mock_table.put_item.side_effect = ClientError(error_response, "PutItem")

    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table

        with pytest.raises(ClientError):
            human_approval._save_pending_request({"request_id": "dup-id"})

    mock_table.update_item.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _update_request_status
# ---------------------------------------------------------------------------

def test_update_request_status_calls_update_item(mock_env):
    """_update_request_status phải gọi DynamoDB update_item đúng cách."""
    mock_table = mock.MagicMock()

    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table
        human_approval._update_request_status("test-uuid", "SEND_FAILED")

    mock_table.update_item.assert_called_once()
    call_kwargs = mock_table.update_item.call_args[1]
    assert call_kwargs["Key"] == {"request_id": "test-uuid"}
    assert ":s" in call_kwargs["ExpressionAttributeValues"]
    assert call_kwargs["ExpressionAttributeValues"][":s"] == "SEND_FAILED"
    # ConditionExpression phải gác PENDING (fix #5)
    assert "ConditionExpression" in call_kwargs
    assert ":pending" in call_kwargs["ExpressionAttributeValues"]
    assert call_kwargs["ExpressionAttributeValues"][":pending"] == "PENDING"


def test_update_status_skipped_if_already_approved(mock_env, caplog):
    """Nếu record đã APPROVED/REJECTED, ConditionalCheckFailed không nên raise (best-effort)."""
    from botocore.exceptions import ClientError
    error_response = {"Error": {"Code": "ConditionalCheckFailedException", "Message": ""}}

    mock_table = mock.MagicMock()
    mock_table.update_item.side_effect = ClientError(error_response, "UpdateItem")

    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table
        # Không được raise — record đã ở trạng thái terminal, không phải lỗi thực sự
        human_approval._update_request_status("test-uuid", "SEND_FAILED")

    assert "Could not update status" in caplog.text


def test_update_request_status_failure_is_best_effort(mock_env, caplog):
    """BotoCoreError (connection failure) phải được bắt và log, không raise."""
    from botocore.exceptions import BotoCoreError
    mock_table = mock.MagicMock()
    mock_table.update_item.side_effect = BotoCoreError()

    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table

        # Không được raise — đây là best-effort cleanup
        human_approval._update_request_status("test-uuid", "SEND_FAILED")

    assert "Could not update status" in caplog.text


# ---------------------------------------------------------------------------
# Tests: TTL và Timeout Logic (REQ-7.5)
# ---------------------------------------------------------------------------

@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_ttl_and_expires_at_epoch_are_separate(mock_send, mock_save, mock_env, sample_violation):
    """
    TTL và expires_at_epoch phải là hai giá trị khác nhau (REQ-7.6, REQ-12.3):
    - expires_at_epoch = now + 15 phút (cửa sổ approve)
    - ttl             = now + 90 ngày  (audit retention, DynamoDB TTL)
    """
    mock_send.return_value = True
    frozen_now = 1700000000

    with mock.patch("human_approval.time.time", return_value=frozen_now):
        human_approval.send_approval_request(
            violation=sample_violation,
            remediation_action="Dừng EC2",
            action_type="stop_ec2",
            action_parameters={"instance_id": "i-1234567890abcdef0", "region": "ap-southeast-1"},
        )

    record = mock_save.call_args[0][0]

    expected_expires = frozen_now + human_approval.APPROVAL_TIMEOUT_SECONDS   # +900s
    expected_ttl     = frozen_now + human_approval.AUDIT_RETENTION_SECONDS    # +90 days

    assert record["expires_at_epoch"] == expected_expires, "expires_at_epoch phải là 15 phút"
    assert record["ttl"] == expected_ttl, "ttl phải là 90 ngày cho audit"
    assert record["ttl"] != record["expires_at_epoch"]


# ---------------------------------------------------------------------------
# Tests: _validate_action_parameters — enforcement (REQ-16)
# ---------------------------------------------------------------------------

def test_validate_rejects_unknown_action_type(caplog):
    """action_type không nằm trong allowlist phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="unknown_action",
        action_parameters={"secret": "data"},
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result is None
    assert "Invalid action_type" in caplog.text
    assert "unknown_action" in caplog.text


def test_validate_rejects_missing_required_param(caplog):
    """deactivate_iam_key thiếu username phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="deactivate_iam_key",
        action_parameters={"access_key_id": "AKIA123"},  # thiếu username
        violation_resource_id="AKIA123",
        violation_region="",
        violation_type="iam_access_key_created",
        violation_resource_type="iam_access_key",
    )
    assert result is None
    assert "missing required keys" in caplog.text
    assert "username" in caplog.text


def test_validate_rejects_extra_disallowed_param(caplog):
    """action_parameters có extra key không nằm trong allowlist phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="deactivate_iam_key",
        action_parameters={
            "username": "john",
            "access_key_id": "AKIA123",
            "secret": "injected_data",  # extra key không được phép
        },
        violation_resource_id="AKIA123",
        violation_region="",
        violation_type="iam_access_key_created",
        violation_resource_type="iam_access_key",
    )
    assert result is None
    assert "disallowed keys" in caplog.text
    assert "secret" in caplog.text


def test_validate_rejects_non_dict_parameters(caplog):
    """action_parameters không phải dict phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters=["instance_id", "region"],  # list thay vì dict
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result is None
    assert "must be a dict" in caplog.text


def test_validate_returns_sanitized_dict_on_success():
    """action_parameters hợp lệ phải trả về sanitized dict chỉ chứa required keys."""
    result = human_approval._validate_action_parameters(
        action_type="deactivate_iam_key",
        action_parameters={"username": "john.doe", "access_key_id": "AKIA1234567890"},
        violation_resource_id="AKIA1234567890",
        violation_region="",
        violation_type="iam_access_key_created",
        violation_resource_type="iam_access_key",
    )
    assert result == {"username": "john.doe", "access_key_id": "AKIA1234567890"}


# ---------------------------------------------------------------------------
# Tests: action_type và action_parameters stored in record
# ---------------------------------------------------------------------------

@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_action_type_and_parameters_saved_in_record(
    mock_send, mock_save, mock_env, sample_violation
):
    """
    action_type và action_parameters phải được lưu trong record.
    Ví dụ: deactivate_iam_key cần username + access_key_id — không chỉ resource_id.
    """
    mock_send.return_value = True
    iam_violation = {
        "resource_id": "AKIA1234567890ABCDEF",
        "resource_type": "iam_access_key",
        "severity": "high",
        "region": "global",
        "violation_type": "iam_access_key_created",  # contract thực từ guardrail_iam.py
    }

    human_approval.send_approval_request(
        violation=iam_violation,
        remediation_action="Vô hiệu hóa IAM access key",
        action_type="deactivate_iam_key",
        action_parameters={"username": "john.doe", "access_key_id": "AKIA1234567890ABCDEF"},
    )

    record = mock_save.call_args[0][0]
    assert record["action_type"] == "deactivate_iam_key"
    assert record["action_parameters"]["username"] == "john.doe"
    assert record["action_parameters"]["access_key_id"] == "AKIA1234567890ABCDEF"
    assert record["remediation_action"] == "Vô hiệu hóa IAM access key"


def test_action_parameters_allowlist_covers_all_remediation_types():
    """
    ACTION_PARAMETERS_ALLOWLIST phải bao gồm đủ 4 remediation types.
    Test này fail nếu ai thêm remediation mới mà quên cập nhật allowlist.
    """
    allowlist = human_approval.ACTION_PARAMETERS_ALLOWLIST
    assert "stop_ec2" in allowlist
    assert "revert_s3_to_private" in allowlist
    assert "deactivate_iam_key" in allowlist
    assert "tag_ebs_noncompliant" in allowlist

    # deactivate_iam_key cần cả username và access_key_id — không chỉ resource_id
    assert "username" in allowlist["deactivate_iam_key"]
    assert "access_key_id" in allowlist["deactivate_iam_key"]

    # ACTION_TARGET_PARAM phải có đủ 4 action types và map đúng target param
    target_map = human_approval.ACTION_TARGET_PARAM
    assert target_map["stop_ec2"] == "instance_id"
    assert target_map["revert_s3_to_private"] == "bucket_name"
    assert target_map["deactivate_iam_key"] == "access_key_id"
    assert target_map["tag_ebs_noncompliant"] == "volume_id"


# ---------------------------------------------------------------------------
# Tests: _validate_action_parameters — new checks (REQ-7.3, REQ-16)
# ---------------------------------------------------------------------------

def test_validate_rejects_empty_string_param_value(caplog):
    """Giá trị param là chuỗi rỗng phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "", "region": "ap-southeast-1"},
        violation_resource_id="",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result is None
    assert "non-empty string" in caplog.text


def test_validate_rejects_non_string_param_value(caplog):
    """Giá trị param không phải str (ví dụ int) phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": 12345, "region": "ap-southeast-1"},
        violation_resource_id=12345,
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result is None
    assert "non-empty string" in caplog.text


def test_validate_rejects_target_mismatch(caplog):
    """
    action_parameters[target_param] khác violation resource_id phải bị từ chối.
    Chống confused-deputy: Slack hiển i-production nhưng action_parameters chỉ đến i-attacker.
    """
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-attacker-selected", "region": "ap-southeast-1"},
        violation_resource_id="i-production",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result is None
    assert "Target mismatch" in caplog.text
    assert "i-attacker-selected" in caplog.text
    assert "i-production" in caplog.text


def test_validate_rejects_region_mismatch(caplog):
    """action_parameters[region] khác violation region phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "us-east-1"},
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result is None
    assert "Region mismatch" in caplog.text
    assert "us-east-1" in caplog.text
    assert "ap-southeast-1" in caplog.text


def test_send_approval_request_rejects_non_dict_violation(mock_env, caplog):
    """violation không phải dict phải bị từ chối ngay từ đầu."""
    result = human_approval.send_approval_request(
        violation="i-1234567890abcdef0",  # string thay vì dict
        remediation_action="Dừng EC2",
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "ap-southeast-1"},
    )
    assert result is None
    assert "must be a dict" in caplog.text


def test_update_request_status_rejects_invalid_status(mock_env, caplog):
    """status không nằm trong _SYSTEM_STATUSES phải bị từ chối mà không gọi DynamoDB."""
    mock_table = mock.MagicMock()

    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table
        human_approval._update_request_status("test-uuid", "INVALID_STATE")

    mock_table.update_item.assert_not_called()
    assert "Invalid status" in caplog.text
    assert "INVALID_STATE" in caplog.text


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED"])
def test_update_request_status_rejects_terminal_user_actions(mock_env, caplog, status):
    """
    APPROVED/REJECTED không được đi qua _update_request_status()
    vì chúng cần approver identity từ Slack payload (REQ-7.6).
    """
    mock_table = mock.MagicMock()
    with mock.patch("human_approval.boto3.resource") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = mock_table
        human_approval._update_request_status("test-uuid", status)
    
    # DynamoDB update_item không được gọi
    mock_table.update_item.assert_not_called()
    assert "Invalid status" in caplog.text


@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_slack_section_shows_sanitized_target(
    mock_send, mock_save, mock_env, sample_violation, ec2_action
):
    """
    Section block phải hiển thị action_type và target đã sanitize,
    không chỉ mô tả người dùng nhập vào (REQ-7.3).
    """
    mock_send.return_value = True

    human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="Dừng EC2 instance",
        **ec2_action,
    )

    payload = mock_send.call_args[0][0]
    section_text = next(
        b for b in payload["blocks"] if b["type"] == "section"
    )["text"]["text"]

    # Phải hiển action_type (mã dispatch ổn định)
    assert "stop_ec2" in section_text
    # Phải hiển target param name và giá trị đã sanitize
    assert "instance_id" in section_text
    assert "i-1234567890abcdef0" in section_text
    # Fallback text cũng phải hiển action_type và target
    assert "stop_ec2" in payload["text"]
    assert "i-1234567890abcdef0" in payload["text"]


# ---------------------------------------------------------------------------
# Tests: action–violation type mapping (REQ-7.1, REQ-16)
# ---------------------------------------------------------------------------

def test_validate_rejects_action_violation_type_mismatch(caplog):
    """
    action_type không phù hợp với violation_type thực tế phải bị từ chối.
    EC2 violation (missing_required_tags) không thể trigger deactivate_iam_key.
    """
    result = human_approval._validate_action_parameters(
        action_type="deactivate_iam_key",
        action_parameters={"username": "john", "access_key_id": "AKIA123"},
        violation_resource_id="AKIA123",
        violation_region="",
        violation_type="missing_required_tags",  # ← EC2 violation, không phợp với IAM action
        violation_resource_type="ec2",
    )
    assert result is None
    assert "Action\u2013violation type mismatch" in caplog.text
    assert "deactivate_iam_key" in caplog.text
    assert "missing_required_tags" in caplog.text


def test_validate_rejects_iam_action_for_ec2_violation(caplog):
    """stop_ec2 không phù hợp với iam_access_key_created phải bị từ chối."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "ap-southeast-1"},
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="iam_access_key_created",  # ← IAM violation, không phợp với EC2 action
        violation_resource_type="iam_access_key",
    )
    assert result is None
    assert "Action\u2013violation type mismatch" in caplog.text


def test_validate_accepts_matching_action_violation_type():
    """stop_ec2 với missing_required_tags (contract EC2) phải được chấp nhận."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-1234567890abcdef0", "region": "ap-southeast-1"},
        violation_resource_id="i-1234567890abcdef0",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="ec2",
    )
    assert result == {"instance_id": "i-1234567890abcdef0", "region": "ap-southeast-1"}


def test_validate_rejects_empty_violation_type_fail_closed(caplog):
    """
    violation_type rỗng phải bị từ chối (fail-closed) — không backward-compat.
    High-risk workflow không cho phép bỏ qua kiểm tra action–violation.
    """
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "ap-southeast-1"},
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="",           # rỗng — phải từ chối
        violation_resource_type="ec2",
    )
    assert result is None
    assert "violation_type is empty" in caplog.text


def test_validate_rejects_empty_resource_type(caplog):
    """violation_resource_type rỗng phải bị từ chối (fail-closed)."""
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "ap-southeast-1"},
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="",  # rỗng — phải từ chối
    )
    assert result is None
    assert "violation_resource_type is empty" in caplog.text


def test_validate_rejects_resource_type_mismatch(caplog):
    """
    resource_type không khớp ACTION_RESOURCE_TYPES phải bị từ chối — kiểm tra kép.
    Ngăn trường hợp: violation_type khớp nhưng resource_type lại sai.
    """
    result = human_approval._validate_action_parameters(
        action_type="stop_ec2",
        action_parameters={"instance_id": "i-abc", "region": "ap-southeast-1"},
        violation_resource_id="i-abc",
        violation_region="ap-southeast-1",
        violation_type="missing_required_tags",
        violation_resource_type="s3",  # ← sai resource_type cho stop_ec2
    )
    assert result is None
    assert "Resource type mismatch" in caplog.text
    assert "s3" in caplog.text
    assert "ec2" in caplog.text


def test_action_violation_types_covers_all_action_types():
    """
    ACTION_VIOLATION_TYPES và ACTION_RESOURCE_TYPES phải có đủ 4 action types
    khớp ACTION_PARAMETERS_ALLOWLIST. Test này fail nếu ai thêm action mới
    mà quên cập nhật các mapping.
    """
    allowlist_keys = set(human_approval.ACTION_PARAMETERS_ALLOWLIST.keys())
    assert set(human_approval.ACTION_VIOLATION_TYPES.keys()) == allowlist_keys, (
        "ACTION_VIOLATION_TYPES thiếu action types"
    )
    assert set(human_approval.ACTION_RESOURCE_TYPES.keys()) == allowlist_keys, (
        "ACTION_RESOURCE_TYPES thiếu action types"
    )

    # Kiểm tra contract thực tế — khóa các tên violation/resource khớp guardrail modules
    vt = human_approval.ACTION_VIOLATION_TYPES
    assert "missing_required_tags" in vt["stop_ec2"]
    assert "public_s3_access" in vt["revert_s3_to_private"]
    assert "iam_access_key_created" in vt["deactivate_iam_key"]
    assert "unencrypted_ebs_volume" in vt["tag_ebs_noncompliant"]

    rt = human_approval.ACTION_RESOURCE_TYPES
    assert rt["stop_ec2"] == "ec2"
    assert rt["revert_s3_to_private"] == "s3"
    assert rt["deactivate_iam_key"] == "iam_access_key"
    assert rt["tag_ebs_noncompliant"] == "ebs"


def test_system_statuses_excludes_approved_rejected():
    """_SYSTEM_STATUSES chỉ chứa SEND_FAILED và TIMED_OUT, không có APPROVED/REJECTED."""
    ss = human_approval._SYSTEM_STATUSES
    assert "SEND_FAILED" in ss
    assert "TIMED_OUT" in ss
    assert "APPROVED" not in ss
    assert "REJECTED" not in ss


@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
@pytest.mark.parametrize("invalid_kwargs", [
    {"violation_type": ""}, # Thiếu violation_type
    {"violation_type": "iam_access_key_created"}, # Mismatch violation_type
    {"resource_type": "s3"}, # Mismatch resource_type
    {"region": "us-east-1"}, # Mismatch region (in violation vs action_parameters)
])
def test_send_approval_request_invalid_mismatches(
    mock_send, mock_save, mock_env, sample_violation, ec2_action, invalid_kwargs
):
    """
    Test các trường hợp mismatch hoặc thiếu field bắt buộc qua send_approval_request().
    Phải return None và không gọi DynamoDB/Slack.
    """
    violation = sample_violation.copy()
    violation.update(invalid_kwargs)

    result = human_approval.send_approval_request(
        violation=violation,
        remediation_action="Dừng EC2",
        **ec2_action,
    )

    assert result is None
    mock_save.assert_not_called()
    mock_send.assert_not_called()


@mock.patch("human_approval._save_pending_request")
@mock.patch("human_approval.notification_service.send_slack_payload")
def test_send_approval_request_target_mismatch(
    mock_send, mock_save, mock_env, sample_violation, ec2_action
):
    """
    Test target mismatch qua send_approval_request().
    """
    ec2_action["action_parameters"]["instance_id"] = "i-attacker"
    
    result = human_approval.send_approval_request(
        violation=sample_violation,
        remediation_action="Dừng EC2",
        **ec2_action,
    )

    assert result is None
    mock_save.assert_not_called()
    mock_send.assert_not_called()
