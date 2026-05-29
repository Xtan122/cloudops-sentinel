import importlib.util
from pathlib import Path

import boto3
import botocore.exceptions
import pytest
from moto import mock_aws
from unittest.mock import patch


@pytest.fixture
def guardrail_cost_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lambda"
        / "compliance_engine"
        / "guardrail_cost.py"
    )
    spec = importlib.util.spec_from_file_location("guardrail_cost", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config():
    return {
        "guardrails": {
            "cost": {
                "required_tags": ["Owner", "Project"],
                "violation_severity": "medium",
            }
        }
    }


@mock_aws
def test_returns_none_when_ec2_has_required_tags(guardrail_cost_module, config):
    ec2 = boto3.resource("ec2", region_name="ap-southeast-1")

    # Tạo instance có tags Owner và Project
    instance = ec2.create_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Owner", "Value": "alice"},
                    {"Key": "Project", "Value": "sentinel"},
                ],
            }
        ],
    )[0]

    result = guardrail_cost_module.check_ec2_tagging(instance.id, "ap-southeast-1", config)

    assert result is None


@mock_aws
def test_returns_violation_when_owner_tag_is_missing(guardrail_cost_module, config):
    ec2 = boto3.resource("ec2", region_name="ap-southeast-1")

    # Tạo instance chỉ có Project, thiếu Owner
    instance = ec2.create_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Project", "Value": "sentinel"},
                ],
            }
        ],
    )[0]

    result = guardrail_cost_module.check_ec2_tagging(instance.id, "ap-southeast-1", config)

    assert result is not None
    assert result["severity"] == "medium"
    assert result["resource_type"] == "ec2"
    assert "Owner" in result["missing_tags"]


@mock_aws
def test_lowercase_owner_does_not_satisfy_required_owner(guardrail_cost_module, config):
    ec2 = boto3.resource("ec2", region_name="ap-southeast-1")

    # Tạo instance có tag "owner" (lowercase) và "Project" — "owner" != "Owner"
    instance = ec2.create_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "owner", "Value": "alice"},   # lowercase ≠ "Owner"
                    {"Key": "Project", "Value": "sentinel"},
                ],
            }
        ],
    )[0]

    result = guardrail_cost_module.check_ec2_tagging(instance.id, "ap-southeast-1", config)

    # Tag matching là case-sensitive → "owner" không thỏa mãn "Owner"
    assert result is not None
    assert "Owner" in result["missing_tags"]


@mock_aws
def test_skip_enforcement_tag_skips_check_case_insensitive(guardrail_cost_module, config):
    ec2 = boto3.resource("ec2", region_name="ap-southeast-1")

    # Tạo instance không có Owner/Project nhưng có "Skip-Enforcement": "TRUE"
    instance = ec2.create_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Skip-Enforcement", "Value": "TRUE"},
                ],
            }
        ],
    )[0]

    result = guardrail_cost_module.check_ec2_tagging(instance.id, "ap-southeast-1", config)

    # Exclusion tag hiện diện → bỏ qua kiểm tra, kết quả phải là None
    assert result is None


@mock_aws
def test_returns_violation_when_both_tags_are_missing(guardrail_cost_module, config):
    ec2 = boto3.resource("ec2", region_name="ap-southeast-1")

    # Tạo instance không có tag nào
    instance = ec2.create_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
    )[0]

    result = guardrail_cost_module.check_ec2_tagging(instance.id, "ap-southeast-1", config)

    assert result is not None
    assert set(result["missing_tags"]) == {"Owner", "Project"}


@mock_aws
def test_uses_severity_from_config(guardrail_cost_module, config):
    # Thay đổi severity trong config thành "high"
    config["guardrails"]["cost"]["violation_severity"] = "high"

    ec2 = boto3.resource("ec2", region_name="ap-southeast-1")

    # Tạo instance thiếu tags để tạo ra violation
    instance = ec2.create_instances(
        ImageId="ami-00000000",
        MinCount=1,
        MaxCount=1,
    )[0]

    result = guardrail_cost_module.check_ec2_tagging(instance.id, "ap-southeast-1", config)

    assert result is not None
    assert result["severity"] == "high"


@mock_aws
def test_raises_exception_when_aws_api_fails(guardrail_cost_module, config):
    # Test error path khi instance không tồn tại (Moto sẽ raise ClientError)
    with pytest.raises(botocore.exceptions.ClientError) as exc_info:
        guardrail_cost_module.check_ec2_tagging("i-nonexistent123", "ap-southeast-1", config)
    assert "InvalidInstanceID.NotFound" in str(exc_info.value)


def test_raises_value_error_when_instance_missing_in_response(guardrail_cost_module, config):
    # Test error path khi API trả về thành công nhưng không có instance (mock boto3.client)
    with patch("boto3.client") as mock_client:
        mock_ec2 = mock_client.return_value
        mock_ec2.describe_instances.return_value = {"Reservations": []}

        with pytest.raises(ValueError, match="not found in describe_instances response"):
            guardrail_cost_module.check_ec2_tagging("i-dummy123", "ap-southeast-1", config)

