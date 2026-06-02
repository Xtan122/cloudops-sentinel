import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add lambda shared directory to path for normal import
sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda" / "shared"))
import ai_reporter


@pytest.fixture
def sample_violation():
    return {
        "violation_type": "Security Violation",
        "resource_type": "S3",
        "resource_id": "my-bucket",
        "severity": "CRITICAL",
        "message": "Bucket allows public read access.",
        "owner": "security-team",
        "region": "us-east-1"
    }


@pytest.fixture
def cost_violation():
    return {
        "violation_type": "Cost Violation",
        "resource_type": "EC2",
        "resource_id": "i-1234567890abcdef0",
        "severity": "HIGH",
        "message": "Missing required tags.",
        "owner": "dev-team",
        "region": "us-west-2"
    }


def test_generate_report_success(sample_violation):
    """Test successful generation of AI report."""
    with patch.object(ai_reporter.boto3, "client") as mock_boto_client:
        mock_client_instance = MagicMock()
        mock_boto_client.return_value = mock_client_instance

        mock_response = {
            "body": MagicMock()
        }
        mock_response["body"].read.return_value = json.dumps({
            "content": [{"text": "*AI Generated Report*\nThis is a mock response."}]
        }).encode("utf-8")
        
        mock_client_instance.invoke_model.return_value = mock_response

        report = ai_reporter.generate_report(sample_violation)

        assert "*AI Generated Report*" in report
        
        mock_client_instance.invoke_model.assert_called_once()
        call_kwargs = mock_client_instance.invoke_model.call_args.kwargs
        assert call_kwargs["modelId"] == ai_reporter.BEDROCK_MODEL_ID
        
        body = json.loads(call_kwargs["body"])
        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert body["max_tokens"] == ai_reporter.MAX_TOKENS
        assert "messages" in body
        assert body["messages"][0]["role"] == "user"
        assert "content" in body["messages"][0]


def test_generate_report_bedrock_failure(sample_violation):
    """Test fallback to template when Bedrock throws an exception."""
    with patch.object(ai_reporter.boto3, "client") as mock_boto_client:
        mock_client_instance = MagicMock()
        mock_boto_client.return_value = mock_client_instance

        mock_client_instance.invoke_model.side_effect = Exception("Bedrock timeout")

        report = ai_reporter.generate_report(sample_violation)

        assert "🔴 Vi Phạm Tuân Thủ: Security Violation" in report
        assert "(Báo cáo tự động được tạo qua template)" in report


def test_generate_report_empty_ai_output(sample_violation):
    """Test fallback to template when Bedrock returns empty text."""
    with patch.object(ai_reporter.boto3, "client") as mock_boto_client:
        mock_client_instance = MagicMock()
        mock_boto_client.return_value = mock_client_instance

        mock_response = {
            "body": MagicMock()
        }
        mock_response["body"].read.return_value = json.dumps({
            "content": [{"text": "   "}]
        }).encode("utf-8")
        
        mock_client_instance.invoke_model.return_value = mock_response

        report = ai_reporter.generate_report(sample_violation)

        assert "🔴 Vi Phạm Tuân Thủ: Security Violation" in report
        assert "(Báo cáo tự động được tạo qua template)" in report


def test_generate_report_malformed_response(sample_violation):
    """Test fallback to template when Bedrock returns malformed JSON."""
    with patch.object(ai_reporter.boto3, "client") as mock_boto_client:
        mock_client_instance = MagicMock()
        mock_boto_client.return_value = mock_client_instance

        mock_response = {
            "body": MagicMock()
        }
        mock_response["body"].read.return_value = json.dumps({
            "unexpected_key": "some value"
        }).encode("utf-8")
        
        mock_client_instance.invoke_model.return_value = mock_response

        report = ai_reporter.generate_report(sample_violation)

        assert "🔴 Vi Phạm Tuân Thủ: Security Violation" in report
        assert "(Báo cáo tự động được tạo qua template)" in report


def test_build_prompt_cost_inclusion(cost_violation):
    """Test if cost-related prompt includes cost estimate request."""
    prompt = ai_reporter._build_prompt(cost_violation)
    assert "cost-related violation" in prompt
    assert "qualitative cost impact" in prompt


def test_build_prompt_no_cost_inclusion(sample_violation):
    """Test non-cost-related prompt does not include cost estimate request."""
    prompt = ai_reporter._build_prompt(sample_violation)
    assert "cost-related violation" not in prompt


def test_generate_template_report(sample_violation):
    """Test basic template report structure."""
    report = ai_reporter._generate_template_report(sample_violation)
    assert "🔴 Vi Phạm Tuân Thủ: Security Violation" in report
    assert "Mức độ:* CRITICAL" in report
    assert "Tài nguyên:* `my-bucket` (us-east-1)" in report
    assert "Chi tiết:* Bucket allows public read access." in report
    assert "(Báo cáo tự động được tạo qua template)" in report


def test_generate_template_report_missing_fields(caplog):
    """Test template report handles missing fields gracefully."""
    empty_violation = {}
    report = ai_reporter._generate_template_report(empty_violation)
    
    assert "Vi Phạm Tuân Thủ: unknown" in report
    assert "Mức độ:* UNKNOWN" in report
    assert "Tài nguyên:* `unknown` (unknown)" in report
    
    assert "Missing critical fields in violation" in caplog.text
