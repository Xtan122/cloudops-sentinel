import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add lambda shared directory to path for normal import
sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda" / "shared"))
import notification_service

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.com/slack-webhook")

def test_send_violation_alert_success(mock_env):
    violation = {
        "severity": "high",
        "resource_id": "i-1234567890abcdef0",
        "region": "us-east-1",
        "timestamp": 1622548800
    }
    ai_report = "Mock AI Report"
    
    with mock.patch("notification_service._send_with_retry") as mock_retry:
        mock_retry.return_value = True
        
        result = notification_service.send_violation_alert(violation, ai_report, dry_run=False)
        
        assert result is True
        mock_retry.assert_called_once()
        payload = mock_retry.call_args[0][0]
        
        attachment = payload["attachments"][0]
        assert attachment["color"] == "#FF6600"
        assert attachment["title"] == "CloudOps Sentinel Alert"
        assert attachment["text"] == "Mock AI Report"
        assert "i-1234567890abcdef0" in attachment["footer"]
        assert "us-east-1" in attachment["footer"]
        assert attachment["ts"] == 1622548800

def test_send_violation_alert_dry_run(mock_env):
    violation = {}
    ai_report = "Dry Run Report"
    
    with mock.patch("notification_service._send_with_retry") as mock_retry:
        mock_retry.return_value = True
        
        result = notification_service.send_violation_alert(violation, ai_report, dry_run=True)
        
        assert result is True
        payload = mock_retry.call_args[0][0]
        attachment = payload["attachments"][0]
        
        assert attachment["title"].startswith("[DRY-RUN]")
        assert attachment["color"] == "#FFCC00" # Default is medium
        assert "unknown" in attachment["footer"] # Default resource and region
        assert "ts" not in attachment

def test_send_violation_alert_uppercase_severity(mock_env):
    violation = {"severity": "CRITICAL"}
    with mock.patch("notification_service._send_with_retry") as mock_retry:
        mock_retry.return_value = True
        result = notification_service.send_violation_alert(violation, "Report", dry_run=False)
        assert result is True
        attachment = mock_retry.call_args[0][0]["attachments"][0]
        assert attachment["color"] == "#FF0000"

def test_send_violation_alert_malformed_timestamp(mock_env, caplog):
    violation = {"timestamp": "not-a-valid-timestamp"}
    with mock.patch("notification_service._send_with_retry") as mock_retry:
        mock_retry.return_value = True
        result = notification_service.send_violation_alert(violation, "Report", dry_run=False)
        assert result is True
        attachment = mock_retry.call_args[0][0]["attachments"][0]
        assert "ts" not in attachment
        assert "Could not parse timestamp" in caplog.text

def test_send_with_retry_missing_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = notification_service._send_with_retry({"test": "data"})
    assert result is False

@mock.patch("notification_service.urllib3.PoolManager")
def test_send_with_retry_sends_valid_http_request(mock_pool_manager, mock_env):
    mock_http = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_http.request.return_value = mock_response
    mock_pool_manager.return_value = mock_http
    
    payload = {"test": "data"}
    result = notification_service._send_with_retry(payload)
    
    assert result is True
    assert mock_http.request.call_count == 1
    
    call_args = mock_http.request.call_args
    assert call_args.args[0] == "POST"
    assert call_args.args[1] == "https://example.com/slack-webhook"
    
    kwargs = call_args.kwargs
    assert kwargs["headers"]["Content-Type"] == "application/json"
    
    body_json = json.loads(kwargs["body"].decode("utf-8"))
    assert body_json == payload

@mock.patch("notification_service.urllib3.PoolManager")
@mock.patch("notification_service.time.sleep")
def test_send_with_retry_all_failures(mock_sleep, mock_pool_manager, mock_env):
    mock_http = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.status = 500
    mock_http.request.return_value = mock_response
    mock_pool_manager.return_value = mock_http
    
    result = notification_service._send_with_retry({"test": "data"}, max_retries=3)
    
    assert result is False
    assert mock_http.request.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_has_calls([mock.call(1), mock.call(2)])

@mock.patch("notification_service.urllib3.PoolManager")
@mock.patch("notification_service.time.sleep")
def test_send_with_retry_exception(mock_sleep, mock_pool_manager, mock_env):
    mock_http = mock.MagicMock()
    mock_http.request.side_effect = Exception("Network error")
    mock_pool_manager.return_value = mock_http
    
    result = notification_service._send_with_retry({"test": "data"}, max_retries=3)
    
    assert result is False
    assert mock_http.request.call_count == 3
    assert mock_sleep.call_count == 2

@mock.patch("notification_service.urllib3.PoolManager")
@mock.patch("notification_service.time.sleep")
def test_send_with_retry_eventual_success(mock_sleep, mock_pool_manager, mock_env):
    mock_http = mock.MagicMock()
    
    # Fail first 2 times, succeed on 3rd
    fail_response = mock.MagicMock()
    fail_response.status = 500
    
    success_response = mock.MagicMock()
    success_response.status = 200
    
    mock_http.request.side_effect = [fail_response, fail_response, success_response]
    mock_pool_manager.return_value = mock_http
    
    result = notification_service._send_with_retry({"test": "data"}, max_retries=3)
    
    assert result is True
    assert mock_http.request.call_count == 3
    assert mock_sleep.call_count == 2
