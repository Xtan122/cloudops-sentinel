import json
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(scope="module")
def handler_module():
    handler_path = (
        Path(__file__).resolve()           # tests/test_event_processor.py
        .parent.parent                     # project root
        / "src" / "lambda" / "event_processor" / "handler.py"
    )
    spec = importlib.util.spec_from_file_location("handler", handler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.fixture
def ec2_running_event():
    """Happy-path: EC2 Instance State-change Notification (running)."""
    return {
        "version": "0",
        "id": "abc-123",
        "source": "aws.ec2",
        "account": "123456789012",
        "time": "2024-01-15T10:30:00Z",
        "region": "us-east-1",
        "detail-type": "EC2 Instance State-change Notification",
        "detail": {
            "instance-id": "i-0abcdef1234567890",
            "state": "running",
        },
    }


@pytest.fixture
def unsupported_event():
    return {
        "version": "0",
        "id": "xyz-999",
        "source": "aws.rds",                       # nguồn không được hỗ trợ
        "account": "123456789012",
        "time": "2024-01-15T11:00:00Z",
        "region": "us-east-1",
        "detail-type": "RDS DB Instance Event",
        "detail": {
            "EventCategories": ["availability"],
            "Message": "DB instance restarted",
        },
    }


@pytest.fixture
def malformed_s3_event():
    """
    Event khớp pattern S3 PutBucketPolicy nhưng thiếu bucketName.
    → extract_metadata trả resource_id = None
    → route_to_compliance raise ValueError
    → lambda_handler bắt exception và trả về 500 / ERROR
    """
    return {
        "version": "0",
        "id": "s3-bad-001",
        "source": "aws.s3",
        "account": "123456789012",
        "time": "2024-01-15T12:00:00Z",
        "region": "us-east-1",
        "detail-type": "AWS API Call via CloudTrail",
        "detail": {
            "eventName": "PutBucketPolicy",
            "requestParameters": {},   # thiếu bucketName → resource_id = None
        },
    }


class TestExtractMetadata:

    def test_returns_correct_fields_for_ec2_running(self, handler_module, ec2_running_event):
        """EC2 running event phải trả đủ 6 field với giá trị đúng."""
        result = handler_module.extract_metadata(ec2_running_event)

        assert result["resource_id"] == "i-0abcdef1234567890"
        assert result["resource_type"] == "ec2"
        assert result["account_id"] == "123456789012"
        assert result["region"] == "us-east-1"
        assert result["timestamp"] == "2024-01-15T10:30:00Z"
        assert result["raw_event"] is ec2_running_event   # reference, không phải copy

    def test_returns_none_resource_for_unsupported_event(self, handler_module, unsupported_event):
        """Event không khớp pattern nào → resource_id và resource_type đều None."""
        result = handler_module.extract_metadata(unsupported_event)

        assert result["resource_id"] is None
        assert result["resource_type"] is None
        # account/region vẫn được extract bình thường
        assert result["account_id"] == "123456789012"
        assert result["region"] == "us-east-1"

    def test_s3_event_extracts_bucket_name(self, handler_module):
        """S3 PutBucketPolicy event hợp lệ phải trả resource_id = bucketName."""
        event = {
            "source": "aws.s3",
            "account": "111111111111",
            "time": "2024-01-15T09:00:00Z",
            "region": "eu-west-1",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventName": "PutBucketPolicy",
                "requestParameters": {"bucketName": "my-secure-bucket"},
            },
        }
        result = handler_module.extract_metadata(event)

        assert result["resource_id"] == "my-secure-bucket"
        assert result["resource_type"] == "s3"

    def test_malformed_s3_event_resource_id_is_none(self, handler_module, malformed_s3_event):
        """S3 event thiếu bucketName → resource_id = None (ValueError sẽ do route_to_compliance raise)."""
        result = handler_module.extract_metadata(malformed_s3_event)

        assert result["resource_id"] is None
        assert result["resource_type"] == "s3"

    def test_iam_create_access_key_extracts_access_key_id(self, handler_module):
        """IAM CreateAccessKey event phải trả resource_id = accessKeyId."""
        event = {
            "source": "aws.iam",
            "account": "222222222222",
            "time": "2024-01-15T08:00:00Z",
            "region": "us-east-1",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventName": "CreateAccessKey",
                "responseElements": {
                    "accessKey": {"accessKeyId": "AKIAIOSFODNN7EXAMPLE"}
                },
            },
        }
        result = handler_module.extract_metadata(event)

        assert result["resource_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert result["resource_type"] == "iam"

    def test_ebs_create_volume_extracts_volume_id(self, handler_module):
        """EBS CreateVolume event phải trả resource_id = volumeId."""
        event = {
            "source": "aws.ec2",
            "account": "333333333333",
            "time": "2024-01-15T07:00:00Z",
            "region": "ap-southeast-1",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventName": "CreateVolume",
                "responseElements": {"volumeId": "vol-0987654321abcdef0"},
            },
        }
        result = handler_module.extract_metadata(event)

        assert result["resource_id"] == "vol-0987654321abcdef0"
        assert result["resource_type"] == "ebs"



class TestLambdaHandlerRouted:
    """lambda_handler() trả 200 / ROUTED khi event được xử lý thành công."""

    def test_returns_200_and_routed_status(self, handler_module, ec2_running_event):
        """
        patch route_to_compliance để cô lập orchestration logic.
        Verify: statusCode 200, body.status == 'ROUTED', route được gọi 1 lần.
        """
        with patch.object(handler_module, "route_to_compliance", return_value="ROUTED") as mock_route:
            response = handler_module.lambda_handler(ec2_running_event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "ROUTED"
        mock_route.assert_called_once()

    def test_route_to_compliance_receives_correct_metadata(self, handler_module, ec2_running_event):
        """metadata truyền vào route_to_compliance phải có resource_type == 'ec2'."""
        captured_metadata = {}

        def fake_route(metadata, raw_event):
            captured_metadata.update(metadata)
            return "ROUTED"

        with patch.object(handler_module, "route_to_compliance", side_effect=fake_route):
            handler_module.lambda_handler(ec2_running_event, None)

        assert captured_metadata["resource_type"] == "ec2"
        assert captured_metadata["resource_id"] == "i-0abcdef1234567890"



class TestLambdaHandlerSkipped:
    """lambda_handler() trả 200 / SKIPPED cho event không được hỗ trợ."""

    def test_returns_200_and_skipped_status(self, handler_module, unsupported_event):
        """
        Không patch route_to_compliance → chạy thật.
        resource_type == None → route trả về SKIPPED tự nhiên.
        """
        response = handler_module.lambda_handler(unsupported_event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "SKIPPED"

    def test_skipped_via_explicit_patch(self, handler_module, unsupported_event):
        """Variant: patch rõ ràng để test chỉ orchestration, không route logic."""
        with patch.object(handler_module, "route_to_compliance", return_value="SKIPPED"):
            response = handler_module.lambda_handler(unsupported_event, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["status"] == "SKIPPED"


class TestLambdaHandlerError:
    """lambda_handler() trả 500 / ERROR khi có exception."""

    def test_returns_500_and_error_status_for_malformed_s3(self, handler_module, malformed_s3_event):
        """
        Không patch route_to_compliance.
        S3 event thiếu bucketName → resource_id = None → ValueError → handler catch → 500/ERROR.
        """
        response = handler_module.lambda_handler(malformed_s3_event, None)

        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["status"] == "ERROR"

    def test_returns_500_when_route_raises_unexpected_error(self, handler_module, ec2_running_event):
        """Bất kỳ exception nào từ route_to_compliance cũng phải bị bắt → 500/ERROR."""
        with patch.object(
            handler_module, "route_to_compliance",
            side_effect=RuntimeError("simulated downstream failure")
        ):
            response = handler_module.lambda_handler(ec2_running_event, None)

        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["status"] == "ERROR"

    def test_error_response_body_has_message_field(self, handler_module, malformed_s3_event):
        """Response 500 phải có field 'message' để client biết lỗi gì."""
        response = handler_module.lambda_handler(malformed_s3_event, None)
        body = json.loads(response["body"])
        assert "message" in body



class TestLogStructured:
    """log_structured() phải emit JSON hợp lệ với đủ top-level fields."""

    @pytest.fixture
    def sample_log_data(self):
        return {
            "source": "aws.ec2",
            "detail_type": "EC2 Instance State-change Notification",
            "resource_id": "i-0abcdef1234567890",
            "account_id": "123456789012",
            "region": "us-east-1",
        }

    def test_outputs_valid_json(self, handler_module, sample_log_data):
        """logger.info phải nhận string JSON parse được."""
        with patch.object(handler_module.logger, "info") as mock_info:
            handler_module.log_structured("INFO", "TEST_EVENT", sample_log_data)

        mock_info.assert_called_once()
        logged_string = mock_info.call_args[0][0]      # positional arg thứ 1
        parsed = json.loads(logged_string)             # must not raise

        assert isinstance(parsed, dict)

    def test_json_contains_required_top_level_fields(self, handler_module, sample_log_data):
        """Payload phải có: timestamp, level, event_type, resource_id, account_id, region."""
        required_fields = {"timestamp", "level", "event_type", "resource_id", "account_id", "region"}

        with patch.object(handler_module.logger, "info") as mock_info:
            handler_module.log_structured("INFO", "TEST_EVENT", sample_log_data)

        parsed = json.loads(mock_info.call_args[0][0])
        missing = required_fields - parsed.keys()
        assert not missing, f"Thiếu fields: {missing}"

    def test_level_and_event_type_are_correct(self, handler_module, sample_log_data):
        """Giá trị level và event_type phải khớp với tham số truyền vào."""
        with patch.object(handler_module.logger, "info") as mock_info:
            handler_module.log_structured("INFO", "TEST_EVENT", sample_log_data)

        parsed = json.loads(mock_info.call_args[0][0])
        assert parsed["level"] == "INFO"
        assert parsed["event_type"] == "TEST_EVENT"

    def test_error_level_uses_logger_error(self, handler_module, sample_log_data):
        """level='ERROR' phải gọi logger.error, không phải logger.info."""
        with patch.object(handler_module.logger, "error") as mock_error, \
             patch.object(handler_module.logger, "info") as mock_info:
            handler_module.log_structured("ERROR", "PROCESSING_FAILED", sample_log_data)

        mock_error.assert_called_once()
        mock_info.assert_not_called()
        # vẫn phải là JSON hợp lệ
        parsed = json.loads(mock_error.call_args[0][0])
        assert parsed["level"] == "ERROR"

    def test_warning_level_uses_logger_warning(self, handler_module, sample_log_data):
        """level='WARNING' phải gọi logger.warning."""
        with patch.object(handler_module.logger, "warning") as mock_warn:
            handler_module.log_structured("WARNING", "UNSUPPORTED_EVENT", sample_log_data)

        mock_warn.assert_called_once()
        parsed = json.loads(mock_warn.call_args[0][0])
        assert parsed["level"] == "WARNING"

    def test_timestamp_is_iso8601(self, handler_module, sample_log_data):
        """timestamp phải là string ISO-8601 có thể parse bởi datetime."""
        from datetime import datetime

        with patch.object(handler_module.logger, "info") as mock_info:
            handler_module.log_structured("INFO", "TEST_EVENT", sample_log_data)

        parsed = json.loads(mock_info.call_args[0][0])
        # datetime.fromisoformat() raise ValueError nếu không hợp lệ
        dt = datetime.fromisoformat(parsed["timestamp"].replace("Z", "+00:00"))
        assert dt is not None
