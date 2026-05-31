import importlib.util
import logging
from pathlib import Path
from unittest.mock import patch

import botocore.exceptions
import pytest


@pytest.fixture
def remediation_ec2_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lambda"
        / "remediation_engine"
        / "remediation_ec2.py"
    )
    spec = importlib.util.spec_from_file_location("remediation_ec2", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dry_run_does_not_call_boto3_client(remediation_ec2_module):
    with patch("boto3.client") as mock_client:
        result = remediation_ec2_module.stop_non_compliant_ec2(
            instance_id="i-1234567890abcdef0",
            region="ap-southeast-1",
            dry_run=True,
        )
        assert result["executed"] is False
        assert result["status"] == "skipped"
        assert result["reason"] == "dry_run_mode"
        assert result["action"] == "stop_ec2"
        assert result["resource_type"] == "ec2"
        assert result["resource_id"] == "i-1234567890abcdef0"
        assert result["region"] == "ap-southeast-1"
        assert result["dry_run"] is True
        mock_client.assert_not_called()


def test_stop_instances_called_when_dry_run_false(remediation_ec2_module):
    with patch("boto3.client") as mock_client:
        mock_ec2 = mock_client.return_value
        result = remediation_ec2_module.stop_non_compliant_ec2(
            instance_id="i-1234567890abcdef0",
            region="ap-southeast-1",
            dry_run=False,
        )
        
        mock_client.assert_called_once_with("ec2", region_name="ap-southeast-1")
        mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-1234567890abcdef0"])
        
        assert result["executed"] is True
        assert result["status"] == "stop_requested"
        assert result["action"] == "stop_ec2"
        assert result["resource_type"] == "ec2"
        assert result["resource_id"] == "i-1234567890abcdef0"
        assert result["region"] == "ap-southeast-1"
        assert result["dry_run"] is False


def test_raises_client_error_when_stop_instances_fails(remediation_ec2_module):
    error = botocore.exceptions.ClientError(
        error_response={
            "Error": {
                "Code": "InvalidInstanceID.NotFound",
                "Message": "Instance not found",
            }
        },
        operation_name="StopInstances",
    )

    with patch("boto3.client") as mock_client:
        mock_ec2 = mock_client.return_value
        mock_ec2.stop_instances.side_effect = error
        
        with pytest.raises(botocore.exceptions.ClientError):
            remediation_ec2_module.stop_non_compliant_ec2(
                instance_id="i-1234567890abcdef0",
                region="ap-southeast-1",
                dry_run=False,
            )
            
        mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-1234567890abcdef0"])


@pytest.fixture
def remediation_s3_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lambda"
        / "remediation_engine"
        / "remediation_s3.py"
    )
    spec = importlib.util.spec_from_file_location("remediation_s3", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revert_s3_bucket_to_private_dry_run_does_not_call_boto3(remediation_s3_module, caplog):
    caplog.set_level(logging.INFO)
    with patch("boto3.client") as mock_client:
        result = remediation_s3_module.revert_s3_bucket_to_private(
            bucket_name="test-bucket",
            region="us-east-1",
            dry_run=True,
        )
        assert result["executed"] is False
        assert result["status"] == "skipped"
        assert result["reason"] == "dry_run_mode"
        assert result["action"] == "delete_s3_bucket_policy"
        assert result["resource_type"] == "s3"
        assert result["resource_id"] == "test-bucket"
        assert result["region"] == "us-east-1"
        assert result["dry_run"] is True
        
        assert "[DRY-RUN]" in caplog.text
        mock_client.assert_not_called()


def test_revert_s3_bucket_to_private_success_deletes_bucket_policy(remediation_s3_module):
    with patch("boto3.client") as mock_client:
        mock_s3 = mock_client.return_value
        result = remediation_s3_module.revert_s3_bucket_to_private(
            bucket_name="test-bucket",
            region="us-east-1",
            dry_run=False,
        )
        
        mock_client.assert_called_once_with("s3", region_name="us-east-1")
        mock_s3.delete_bucket_policy.assert_called_once_with(Bucket="test-bucket")
        
        assert result["executed"] is True
        assert result["status"] == "bucket_policy_deleted"
        assert result["action"] == "delete_s3_bucket_policy"
        assert result["resource_type"] == "s3"
        assert result["resource_id"] == "test-bucket"
        assert result["region"] == "us-east-1"
        assert result["dry_run"] is False


def test_revert_s3_bucket_to_private_client_error_raises(remediation_s3_module):
    error = botocore.exceptions.ClientError(
        error_response={
            "Error": {
                "Code": "NoSuchBucket",
                "Message": "The specified bucket does not exist",
            }
        },
        operation_name="DeleteBucketPolicy",
    )

    with patch("boto3.client") as mock_client:
        mock_s3 = mock_client.return_value
        mock_s3.delete_bucket_policy.side_effect = error
        
        with pytest.raises(botocore.exceptions.ClientError):
            remediation_s3_module.revert_s3_bucket_to_private(
                bucket_name="test-bucket",
                region="us-east-1",
                dry_run=False,
            )
            
        mock_client.assert_called_once_with("s3", region_name="us-east-1")
        mock_s3.delete_bucket_policy.assert_called_once_with(Bucket="test-bucket")
