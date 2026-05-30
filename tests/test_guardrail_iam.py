import importlib.util
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

@pytest.fixture
def guardrail_iam_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lambda"
        / "compliance_engine"
        / "guardrail_iam.py"
    )
    spec = importlib.util.spec_from_file_location("guardrail_iam", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture
def config():
    return {
        "guardrails": {
            "iam": {
                "violation_severity": "high",
            }
        }
    }

@pytest.fixture
def event_detail():
    return {
        "responseElements": {
            "accessKey": {
                "accessKeyId": "AKIAIOSFODNN7EXAMPLE",
                "userName": "test-user"
            }
        }
    }

@mock_aws
def test_returns_violation_for_normal_event(event_detail, config, guardrail_iam_module):
    # Setup IAM user
    iam = boto3.client("iam", region_name="us-east-1")
    iam.create_user(UserName="test-user")
    # No tags added

    result = guardrail_iam_module.check_iam_access_key(event_detail, config)

    assert result is not None
    assert result["violation_type"] == "iam_access_key_created"
    assert result["severity"] == "high"
    assert result["resource_type"] == "iam_access_key"
    assert result["resource_id"] == "AKIAIOSFODNN7EXAMPLE"
    assert result["username"] == "test-user"
    assert result["access_key_id"] == "AKIAIOSFODNN7EXAMPLE"

@mock_aws
def test_uses_severity_from_config(event_detail, config, guardrail_iam_module):
    # Setup IAM user
    iam = boto3.client("iam", region_name="us-east-1")
    iam.create_user(UserName="test-user")

    # Change config severity
    config["guardrails"]["iam"]["violation_severity"] = "critical"
    
    result = guardrail_iam_module.check_iam_access_key(event_detail, config)

    assert result is not None
    assert result["severity"] == "critical"

@mock_aws
def test_returns_none_when_user_is_exempted(event_detail, config, guardrail_iam_module):
    # Setup IAM user with Exempted-User tag
    iam = boto3.client("iam", region_name="us-east-1")
    iam.create_user(
        UserName="test-user",
        Tags=[
            {"Key": "Exempted-User", "Value": "TRUE"}
        ]
    )

    result = guardrail_iam_module.check_iam_access_key(event_detail, config)

    assert result is None

def test_raises_client_error_when_api_fails(event_detail, config, guardrail_iam_module):
    # Simulate AccessDenied when calling list_user_tags
    with patch.object(guardrail_iam_module.boto3, "client") as mock_boto3_client:
        mock_iam = mock_boto3_client.return_value
        mock_iam.list_user_tags.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
            "ListUserTags",
        )

        with pytest.raises(ClientError) as exc_info:
            guardrail_iam_module.check_iam_access_key(event_detail, config)

        assert exc_info.value.response["Error"]["Code"] == "AccessDenied"

def test_raises_value_error_on_malformed_event(config, guardrail_iam_module):
    malformed_event = {
        "responseElements": {
            "accessKey": {
                "userName": "test-user"
                # Missing accessKeyId
            }
        }
    }
    with pytest.raises(ValueError) as exc_info:
        guardrail_iam_module.check_iam_access_key(malformed_event, config)
    
    assert "Malformed CreateAccessKey event" in str(exc_info.value)
