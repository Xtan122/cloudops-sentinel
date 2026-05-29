import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def guardrail_security_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lambda"
        / "compliance_engine"
        / "guardrail_security.py"
    )
    spec = importlib.util.spec_from_file_location("guardrail_security", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config():
    return {
        "guardrails": {
            "security": {
                "violation_severity": "critical"
            }
        }
    }


@mock_aws
def test_private_no_policy_returns_none(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-private")

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-private", "us-east-1", config
    )

    assert result is None


@mock_aws
def test_private_with_policy_returns_none(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-private-policy")

    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": "arn:aws:iam::123456789012:root"},
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::test-bucket-private-policy/*"]
            }
        ]
    }
    s3.put_bucket_policy(Bucket="test-bucket-private-policy", Policy=json.dumps(policy))

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-private-policy", "us-east-1", config
    )

    assert result is None


@mock_aws
def test_public_read_returns_critical_violation(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-public-read")

    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::test-bucket-public-read/*"]
            }
        ]
    }
    s3.put_bucket_policy(Bucket="test-bucket-public-read", Policy=json.dumps(policy))

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-public-read", "us-east-1", config
    )

    assert result is not None
    assert result["severity"] == "critical"
    assert result["access_type"] == "read"
    assert result["violation_type"] == "public_s3_access"
    assert result["resource_type"] == "s3"
    assert result["resource_id"] == "test-bucket-public-read"
    assert result["region"] == "us-east-1"


@mock_aws
def test_public_write_returns_critical_violation(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-public-write")

    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:PutObject"],
                "Resource": ["arn:aws:s3:::test-bucket-public-write/*"]
            }
        ]
    }
    s3.put_bucket_policy(Bucket="test-bucket-public-write", Policy=json.dumps(policy))

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-public-write", "us-east-1", config
    )

    assert result is not None
    assert result["severity"] == "critical"
    assert result["access_type"] == "write"
    assert result["violation_type"] == "public_s3_access"
    assert result["resource_type"] == "s3"
    assert result["resource_id"] == "test-bucket-public-write"
    assert result["region"] == "us-east-1"


@mock_aws
def test_public_read_write_returns_read_write_access_type(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-public-rw")

    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": ["arn:aws:s3:::test-bucket-public-rw/*"]
            }
        ]
    }
    s3.put_bucket_policy(Bucket="test-bucket-public-rw", Policy=json.dumps(policy))

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-public-rw", "us-east-1", config
    )

    assert result is not None
    assert result["severity"] == "critical"
    assert result["access_type"] == "read_write"
    assert result["violation_type"] == "public_s3_access"
    assert result["resource_type"] == "s3"
    assert result["resource_id"] == "test-bucket-public-rw"
    assert result["region"] == "us-east-1"


@mock_aws
def test_skip_enforcement_tag_returns_none(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-skip")

    # Add a public policy
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:*"],
                "Resource": ["arn:aws:s3:::test-bucket-skip/*"]
            }
        ]
    }
    s3.put_bucket_policy(Bucket="test-bucket-skip", Policy=json.dumps(policy))

    # Add Skip-Enforcement tag
    s3.put_bucket_tagging(
        Bucket="test-bucket-skip",
        Tagging={
            "TagSet": [
                {"Key": "Skip-Enforcement", "Value": "TRUE"}
            ]
        }
    )

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-skip", "us-east-1", config
    )

    assert result is None


def test_malformed_policy_raises_value_error(guardrail_security_module):
    malformed_json = '{"Statement": [{"Effect": "Allow", "Principal": "*" '

    with pytest.raises(ValueError, match="Malformed bucket policy JSON"):
        guardrail_security_module.is_policy_public(malformed_json)


def test_check_s3_public_access_malformed_policy_raises_value_error(guardrail_security_module, config):
    malformed_json = '{"Statement": [{"Effect": "Allow", "Principal": "*" '

    with patch.object(guardrail_security_module.boto3, "client") as mock_boto3_client:
        mock_s3 = mock_boto3_client.return_value
        
        # get_bucket_tagging throws NoSuchTagSet to bypass tags check
        mock_s3.get_bucket_tagging.side_effect = guardrail_security_module.botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchTagSet"}}, "GetBucketTagging"
        )
        
        # get_bucket_policy returns malformed JSON
        mock_s3.get_bucket_policy.return_value = {"Policy": malformed_json}

        with pytest.raises(ValueError, match="Malformed bucket policy JSON"):
            guardrail_security_module.check_s3_public_access(
                "test-bucket-malformed", "us-east-1", config
            )


@mock_aws
def test_public_read_with_aws_star_principal_returns_violation(guardrail_security_module, config):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket-public-aws-star")

    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": ["s3:GetObject"],
                "Resource": ["arn:aws:s3:::test-bucket-public-aws-star/*"]
            }
        ]
    }
    s3.put_bucket_policy(Bucket="test-bucket-public-aws-star", Policy=json.dumps(policy))

    result = guardrail_security_module.check_s3_public_access(
        "test-bucket-public-aws-star", "us-east-1", config
    )

    assert result is not None
    assert result["severity"] == "critical"
    assert result["access_type"] == "read"
    assert result["violation_type"] == "public_s3_access"
    assert result["resource_type"] == "s3"
    assert result["resource_id"] == "test-bucket-public-aws-star"
    assert result["region"] == "us-east-1"


def test_get_bucket_tagging_access_denied_raises(guardrail_security_module, config):
    with patch.object(guardrail_security_module.boto3, "client") as mock_boto3_client:
        mock_s3 = mock_boto3_client.return_value
        
        # get_bucket_tagging raises AccessDenied
        mock_s3.get_bucket_tagging.side_effect = guardrail_security_module.botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied"}}, "GetBucketTagging"
        )
        
        with pytest.raises(guardrail_security_module.botocore.exceptions.ClientError) as exc_info:
            guardrail_security_module.check_s3_public_access(
                "test-bucket-access-denied", "us-east-1", config
            )
            
        assert exc_info.value.response["Error"]["Code"] == "AccessDenied"


def test_policy_with_s3_star_is_public_write_or_read_write(guardrail_security_module):
    policy_json = json.dumps({
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:*"]
            }
        ]
    })
    
    is_public, access_type = guardrail_security_module.is_policy_public(policy_json)
    
    assert is_public is True
    assert access_type == "read_write"
