import importlib.util
from pathlib import Path
from unittest.mock import patch

import boto3
import botocore.exceptions
import pytest
from moto import mock_aws


@pytest.fixture
def guardrail_compliance_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lambda"
        / "compliance_engine"
        / "guardrail_compliance.py"
    )
    spec = importlib.util.spec_from_file_location("guardrail_compliance", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config():
    return {
        "guardrails": {
            "compliance": {
                "violation_severity": "high",
            }
        }
    }


@mock_aws
def test_returns_none_when_ebs_volume_is_encrypted(guardrail_compliance_module, config):
    ec2 = boto3.client("ec2", region_name="ap-southeast-1")
    response = ec2.create_volume(
        AvailabilityZone="ap-southeast-1a",
        Size=10,
        Encrypted=True,
    )
    volume_id = response["VolumeId"]

    result = guardrail_compliance_module.check_ebs_encryption(volume_id, "ap-southeast-1", config)

    assert result is None


@mock_aws
def test_returns_violation_when_ebs_volume_is_not_encrypted(guardrail_compliance_module, config):
    ec2 = boto3.client("ec2", region_name="ap-southeast-1")
    response = ec2.create_volume(
        AvailabilityZone="ap-southeast-1a",
        Size=10,
        Encrypted=False,
    )
    volume_id = response["VolumeId"]

    result = guardrail_compliance_module.check_ebs_encryption(volume_id, "ap-southeast-1", config)

    assert result is not None
    assert result["violation_type"] == "unencrypted_ebs_volume"
    assert result["severity"] == "high"
    assert result["resource_type"] == "ebs"
    assert result["resource_id"] == volume_id
    assert result["region"] == "ap-southeast-1"
    assert volume_id in result["message"]


@mock_aws
def test_skip_enforcement_tag_skips_unencrypted_volume(guardrail_compliance_module, config):
    ec2 = boto3.client("ec2", region_name="ap-southeast-1")
    response = ec2.create_volume(
        AvailabilityZone="ap-southeast-1a",
        Size=10,
        Encrypted=False,
        TagSpecifications=[
            {
                "ResourceType": "volume",
                "Tags": [{"Key": "Skip-Enforcement", "Value": "TRUE"}]
            }
        ]
    )
    volume_id = response["VolumeId"]

    result = guardrail_compliance_module.check_ebs_encryption(volume_id, "ap-southeast-1", config)

    assert result is None


@mock_aws
def test_uses_severity_from_config(guardrail_compliance_module, config):
    config["guardrails"]["compliance"]["violation_severity"] = "critical"

    ec2 = boto3.client("ec2", region_name="ap-southeast-1")
    response = ec2.create_volume(
        AvailabilityZone="ap-southeast-1a",
        Size=10,
        Encrypted=False,
    )
    volume_id = response["VolumeId"]

    result = guardrail_compliance_module.check_ebs_encryption(volume_id, "ap-southeast-1", config)

    assert result is not None
    assert result["severity"] == "critical"


def test_raises_when_describe_volumes_returns_empty(guardrail_compliance_module, config):
    with patch("boto3.client") as mock_client:
        mock_ec2 = mock_client.return_value
        mock_ec2.describe_volumes.return_value = {"Volumes": []}

        with pytest.raises(ValueError, match="not found in describe_volumes response"):
            guardrail_compliance_module.check_ebs_encryption("vol-dummy123", "ap-southeast-1", config)


def test_raises_when_encrypted_field_is_missing(guardrail_compliance_module, config):
    with patch("boto3.client") as mock_client:
        mock_ec2 = mock_client.return_value
        mock_ec2.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-dummy123"}]
        }
        with pytest.raises(ValueError, match="missing 'Encrypted' state"):
            guardrail_compliance_module.check_ebs_encryption("vol-dummy123", "ap-southeast-1", config)


@mock_aws
def test_raises_exception_when_aws_api_fails(guardrail_compliance_module, config):
    with pytest.raises(botocore.exceptions.ClientError) as exc_info:
        guardrail_compliance_module.check_ebs_encryption("vol-nonexistent123", "ap-southeast-1", config)
    assert "InvalidVolume.NotFound" in str(exc_info.value)
