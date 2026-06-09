import json
import sys
from pathlib import Path
from unittest import mock

import pytest
from botocore.exceptions import ClientError

# Import handler
sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda" / "shared"))
import approval_handler

@pytest.fixture(autouse=True)
def reset_table():
    approval_handler._table = None
    yield
    approval_handler._table = None

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("APPROVAL_TABLE_NAME", "test-approval-table")
    monkeypatch.setenv("DRY_RUN_MODE", "true")

@pytest.fixture
def mock_dynamodb():
    with mock.patch("approval_handler.boto3.resource") as mock_resource:
        mock_table = mock.Mock()
        mock_resource.return_value.Table.return_value = mock_table
        # Cập nhật cache _table trong handler để test
        approval_handler._table = mock_table
        yield mock_table
        approval_handler._table = None

def build_slack_event(action_id, value, payload_dict=None, raw_body=None):
    if raw_body is not None:
        return {"body": raw_body}
    
    if payload_dict is None:
        payload_dict = {
            "type": "block_actions",
            "user": {"id": "U123", "name": "test_user"},
            "actions": [{"action_id": action_id, "value": value}]
        }
        
    from urllib.parse import urlencode
    body = urlencode({"payload": json.dumps(payload_dict)})
    return {"body": body}


def test_missing_payload(mock_env):
    event = {"body": "random=text"}
    res = approval_handler.lambda_handler(event, None)
    assert res["statusCode"] == 400
    assert "Invalid payload format" in res["body"]


def test_malformed_json(mock_env):
    from urllib.parse import urlencode
    event = {"body": urlencode({"payload": "invalid json"}) }
    res = approval_handler.lambda_handler(event, None)
    assert res["statusCode"] == 400
    assert "Invalid payload format" in res["body"]


def test_missing_request_id(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "") # Empty request_id
    res = approval_handler.lambda_handler(event, None)
    assert res["statusCode"] == 400
    assert "Missing request_id" in res["body"]
    mock_dynamodb.get_item.assert_not_called()


def test_unknown_action_id(mock_env, mock_dynamodb):
    event = build_slack_event("unknown_action", "req-123")
    res = approval_handler.lambda_handler(event, None)
    assert res["statusCode"] == 400
    assert "Invalid action" in res["body"]
    mock_dynamodb.get_item.assert_not_called()


def test_record_not_found(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    mock_dynamodb.get_item.return_value = {} # Record not found
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "not found" in res["body"]
        mock_dispatch.assert_not_called()
        mock_dynamodb.update_item.assert_not_called()


def test_record_not_pending(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "APPROVED", # Not PENDING
            "expires_at_epoch": 9999999999
        }
    }
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "already processed" in res["body"]
        mock_dispatch.assert_not_called()
        mock_dynamodb.update_item.assert_not_called()


def test_reject_khong_dispatch(mock_env, mock_dynamodb):
    event = build_slack_event("reject_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 9999999999
        }
    }
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "Remediation rejected" in res["body"]
        mock_dispatch.assert_not_called()
        mock_dynamodb.update_item.assert_called_once()
        call_kwargs = mock_dynamodb.update_item.call_args[1]
        assert call_kwargs["ExpressionAttributeValues"][":s"] == "REJECTED"


def test_timeout_khong_dispatch(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 1000 # Quá khứ
        }
    }
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "expired" in res["body"]
        mock_dispatch.assert_not_called()
        mock_dynamodb.update_item.assert_called_once()
        call_kwargs = mock_dynamodb.update_item.call_args[1]
        assert call_kwargs["ExpressionAttributeValues"][":s"] == "TIMED_OUT"


def test_invalid_expires_at_epoch(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": "abc" # Invalid type
        }
    }
    
    with mock.patch("approval_handler._mark_timed_out", wraps=approval_handler._mark_timed_out) as mock_mark:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "Invalid approval record" in res["body"]
        mock_mark.assert_called_once()
        
        mock_dynamodb.update_item.assert_called_once()
        call_kwargs = mock_dynamodb.update_item.call_args[1]
        assert call_kwargs["ExpressionAttributeValues"][":s"] == "TIMED_OUT"

def test_parse_expires_at_epoch():
    assert approval_handler._parse_expires_at_epoch({"expires_at_epoch": "123"}) == 123
    assert approval_handler._parse_expires_at_epoch({"expires_at_epoch": 123}) == 123
    assert approval_handler._parse_expires_at_epoch({"expires_at_epoch": "abc"}) is None
    assert approval_handler._parse_expires_at_epoch({}) is None


def test_approve_happy_path(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 9999999999,
            "action_type": "stop_ec2",
            "action_parameters": {"instance_id": "i-123", "region": "ap-southeast-1"}
        }
    }
    
    with mock.patch("approval_handler.dispatch_remediation", return_value=(True, {"executed": True}, None)) as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "Remediation approved" in res["body"]
        assert "successfully" in res["body"]
        assert "test_user (U123)" in res["body"]
        mock_dispatch.assert_called_once_with("req-123", "stop_ec2", {"instance_id": "i-123", "region": "ap-southeast-1"})
        
        calls = mock_dynamodb.update_item.call_args_list
        assert len(calls) == 2
        
        # Call 1: _approve_request
        approve_kwargs = calls[0][1]
        assert approve_kwargs["ExpressionAttributeValues"][":s"] == "APPROVED"
        
        # Call 2: _record_remediation_result
        audit_kwargs = calls[1][1]
        assert audit_kwargs["ExpressionAttributeValues"][":status"] == "SUCCEEDED"


def test_remediation_failure(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 9999999999,
            "action_type": "stop_ec2",
            "action_parameters": {"instance_id": "i-123", "region": "ap-southeast-1"}
        }
    }
    
    with mock.patch("approval_handler.dispatch_remediation", return_value=(False, None, "API Error")) as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "Remediation approved" in res["body"]
        assert "with errors" in res["body"]
        
        calls = mock_dynamodb.update_item.call_args_list
        assert len(calls) == 2
        
        # Call 1: _approve_request
        approve_kwargs = calls[0][1]
        assert approve_kwargs["ExpressionAttributeValues"][":s"] == "APPROVED"
        
        # Call 2: _record_remediation_result
        audit_kwargs = calls[1][1]
        assert audit_kwargs["ExpressionAttributeValues"][":status"] == "FAILED"
        assert audit_kwargs["ExpressionAttributeValues"][":err"] == "API Error"


def test_audit_update_failure(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 9999999999,
            "action_type": "stop_ec2",
            "action_parameters": {"instance_id": "i-123", "region": "ap-southeast-1"}
        }
    }
    
    # Simulate success dispatch, but audit update fails
    def mock_update_item(*args, **kwargs):
        # UpdateExpression for _approve_request works
        if "SET #s = :s" in kwargs.get("UpdateExpression", ""):
            return {}
        # UpdateExpression for _record_remediation_result fails
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "DB Error"}}, "UpdateItem")
        
    mock_dynamodb.update_item.side_effect = mock_update_item
    
    with mock.patch("approval_handler.dispatch_remediation", return_value=(True, {}, None)) as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "failed to update audit log" in res["body"]
        # Thêm assert để check _record_remediation_result() fail không retry remediation
        mock_dispatch.assert_called_once()


def test_get_item_dynamodb_error(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    mock_dynamodb.get_item.side_effect = ClientError({"Error": {"Code": "InternalServerError", "Message": "DB Error"}}, "GetItem")
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 500
        assert "Internal server error" in res["body"]
        mock_dispatch.assert_not_called()


from botocore.exceptions import BotoCoreError
class MockBotoCoreError(BotoCoreError):
    fmt = "Mock botocore error"

def test_approve_request_botocore_error(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 9999999999,
            "action_type": "stop_ec2",
            "action_parameters": {"instance_id": "i-123", "region": "ap-southeast-1"}
        }
    }
    
    mock_dynamodb.update_item.side_effect = MockBotoCoreError()
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "could not be processed" in res["body"]
        # CHÚ Ý: Không được gọi dispatch
        mock_dispatch.assert_not_called()



def test_double_click_approve_chi_dispatch_1_lan(mock_env, mock_dynamodb):
    event = build_slack_event("approve_action", "req-123")
    
    mock_dynamodb.get_item.return_value = {
        "Item": {
            "request_id": "req-123",
            "status": "PENDING",
            "expires_at_epoch": 9999999999,
            "action_type": "stop_ec2",
            "action_parameters": {"instance_id": "i-123", "region": "ap-southeast-1"}
        }
    }
    
    # Simulate DB update fails via ConditionalCheckFailedException 
    error_response = {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Check failed"}}
    mock_dynamodb.update_item.side_effect = ClientError(error_response, "UpdateItem")
    
    with mock.patch("approval_handler.dispatch_remediation") as mock_dispatch:
        res = approval_handler.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "already processed" in res["body"]
        # CHÚ Ý: Không được gọi dispatch
        mock_dispatch.assert_not_called()


def test_invalid_dry_run_mode(mock_env, mock_dynamodb, monkeypatch):
    monkeypatch.setenv("DRY_RUN_MODE", "invalid_value")
    
    # Phải setup sys.path cho test này nếu module chưa được load
    lambda_root = Path(__file__).resolve().parents[1] / "src" / "lambda"
    if str(lambda_root) not in sys.path:
        sys.path.append(str(lambda_root))
        
    with mock.patch("remediation_engine.remediation_ec2.stop_non_compliant_ec2") as mock_stop:
        success, result_dict, error_text = approval_handler.dispatch_remediation(
            "req-123",
            "stop_ec2",
            {"instance_id": "i-123", "region": "ap-southeast-1"}
        )
        assert success is True
        # Invalid config -> dry_run phải là True
        mock_stop.assert_called_once_with(instance_id="i-123", region="ap-southeast-1", dry_run=True)
