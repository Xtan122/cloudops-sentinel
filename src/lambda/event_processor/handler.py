import json
import logging
from datetime import datetime, timezone
import os

from shared.config_parser import load_config
from shared.ai_reporter import generate_report
from shared.notification_service import send_violation_alert
from shared.human_approval import send_approval_request

from compliance_engine.guardrail_cost import check_ec2_tagging
from compliance_engine.guardrail_security import check_s3_public_access
from compliance_engine.guardrail_iam import check_iam_access_key
from compliance_engine.guardrail_compliance import check_ebs_encryption

from remediation_engine.remediation_ec2 import stop_non_compliant_ec2
from remediation_engine.remediation_s3 import revert_s3_bucket_to_private
from remediation_engine.remediation_iam import deactivate_access_key
from remediation_engine.remediation_ebs import tag_non_compliant_ebs
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def extract_metadata(event: dict) -> dict:
    """Trich xuat metadata chuan hoa tu EventBridge event theo REQ-1.5."""
    source = event.get("source")
    detail_type = event.get("detail-type")
    detail = event.get("detail", {})
    
    event_name = detail.get("eventName")

    resource_id = None
    resource_type = None

    if source == "aws.ec2" and detail_type == "EC2 Instance State-change Notification":
        resource_id = detail.get("instance-id")
        resource_type = "ec2"
    
    elif source == "aws.s3" and detail_type == "AWS API Call via CloudTrail" and event_name == "PutBucketPolicy":
        resource_id = detail.get("requestParameters", {}).get("bucketName")
        resource_type = "s3"

    elif source == "aws.iam" and detail_type == "AWS API Call via CloudTrail" and event_name == "CreateAccessKey":
        resource_id = detail.get("responseElements", {}).get("accessKey", {}).get("accessKeyId")
        resource_type = "iam"

    elif source == "aws.ec2" and detail_type == "AWS API Call via CloudTrail" and event_name == "CreateVolume":
        resource_id = detail.get("responseElements", {}).get("volumeId")
        resource_type = "ebs"

    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "account_id": event.get("account"),
        "region": event.get("region"),
        "timestamp": event.get("time"),
        "raw_event": event,
    }


def log_structured(level: str, event_type: str, data: dict) -> None:
    """Ghi structured log JSON theo REQ-1.6 va REQ-12."""

    log_timestamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "timestamp": log_timestamp,
        "level": level,
        "event_type": event_type,
        "source": data.get("source"),
        "detail_type": data.get("detail_type"),
        "resource_id": data.get("resource_id"),
        "account_id": data.get("account_id"),
        "region": data.get("region"),
        "data": data
    }

    log_msg = json.dumps(payload)
    if level.upper() == "ERROR":
        logger.error(log_msg)
    elif level.upper() == "WARNING":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)


def route_to_compliance(metadata: dict, raw_event: dict) -> str:
    """Route event den dung compliance checker dua tren resource_type."""
    # Load config
    config_path = os.environ.get("CONFIG_PATH", "config.json")
    config = load_config(config_path)
    dry_run = config.get("dry_run_mode", True)

    resource_type = metadata.get("resource_type")

    if resource_type is None:
        log_structured("WARNING", "UNSUPPORTED_EVENT", {
            "message": "Unsupported event. resource_type is None, skipping routing.",
            "source": raw_event.get("source"),
            "detail_type": raw_event.get("detail-type"),
            "event_name": raw_event.get("detail", {}).get("eventName"),
            "account_id": metadata.get("account_id"),
            "region": metadata.get("region"),
            "event_timestamp": metadata.get("timestamp"),
            "raw_event": raw_event
        })
        return "SKIPPED"

    resource_id = metadata.get("resource_id")
    region = metadata.get("region")
    if resource_id is None:
        raise ValueError(f"Event matches {resource_type} pattern but missing resource_id. Cannot route to compliance checker.")

    violation = None
    action_type = None
    action_parameters = {}
    remediation_func = None
    remediation_action = None
    if resource_type == "ec2":
        violation = check_ec2_tagging(resource_id, region, config)
        action_type = "stop_ec2"
        action_parameters = {"instance_id": resource_id, "region": region}
        remediation_action = "Stop non-compliant EC2 instance"
        remediation_func = lambda: stop_non_compliant_ec2(resource_id, region, dry_run)
    elif resource_type == "s3":
        violation = check_s3_public_access(resource_id, region, config)
        action_type = "revert_s3_to_private"
        action_parameters = {"bucket_name": resource_id, "region": region}
        remediation_action = "Revert public S3 bucket to private"
        remediation_func = lambda: revert_s3_bucket_to_private(resource_id, region, dry_run)
    elif resource_type == "iam":
        event_detail = raw_event.get("detail", {})
        violation = check_iam_access_key(event_detail, config)
        username = event_detail.get("responseElements", {}).get("accessKey", {}).get("userName")
        action_type = "deactivate_iam_key"
        action_parameters = {"username": username, "access_key_id": resource_id}
        remediation_action = f"Deactivate IAM access key for user {username}"
        remediation_func = lambda: deactivate_access_key(username, resource_id, dry_run)
    elif resource_type == "ebs":
        violation = check_ebs_encryption(resource_id, region, config)
        action_type = "tag_ebs_noncompliant"
        action_parameters = {"volume_id": resource_id, "region": region}
        remediation_action = "Tag unencrypted EBS volume as non-compliant"
        remediation_func = lambda: tag_non_compliant_ebs(resource_id, region, dry_run)
    else:
        raise ValueError(f"Unknown resource_type for routing: {resource_type}")

    if not violation:
        log_structured("INFO", "COMPLIANT_RESOURCE", {
            "message": f"Resource {resource_id} is compliant.",
            "resource_type": resource_type,
            "resource_id": resource_id
        })
        return "COMPLIANT"

    # Handle violation
    log_structured("WARNING", "VIOLATION_DETECTED", violation)
    # Generate AI/template report
    ai_report = generate_report(violation)
    # Send Slack alert
    send_violation_alert(violation, ai_report, dry_run)

    if dry_run:
        log_structured("INFO", "REMEDIATION_SKIPPED_DRY_RUN", {
            "message": "Dry-run mode is enabled. Skipping remediation.",
            "resource_id": resource_id,
            "resource_type": resource_type
        })
        return "DRY_RUN"

    # Enforcement: check for human approval if high-risk action
    severity = violation.get("severity", "medium").lower()
    if severity in ["high", "critical"]:
        log_structured("INFO", "APPROVAL_REQUESTED", {
            "message": f"High risk action required for {resource_id}. Requesting human approval.",
            "action_type": action_type
        })
        send_approval_request(violation, remediation_action, action_type, action_parameters)
        return "PENDING_APPROVAL"
    else:
        log_structured("INFO", "EXECUTING_REMEDIATION", {
            "message": f"Executing automated remediation for {resource_id}.",
            "action_type": action_type
        })
        remediation_func()
        return "REMEDIATED"

    return "ROUTED"


def lambda_handler(event: dict, context) -> dict:
    """Entry point cho Lambda Event Processor."""
    try:
        # TODO 1: Log event_received
        log_structured("INFO", "EVENT_RECEIVED", {
            "source": event.get("source"),
            "detail_type": event.get("detail-type"),
            "account_id": event.get("account"),
            "region": event.get("region"),
            "raw_event": event
        })

        # TODO 2: Goi extract_metadata(event)
        metadata = extract_metadata(event)

        # TODO 3: Log metadata_extracted
        log_structured("INFO", "METADATA_EXTRACTED", {
            "source": event.get("source"),
            "detail_type": event.get("detail-type"),
            "event_name": event.get("detail", {}).get("eventName"),
            "resource_id": metadata.get("resource_id"),
            "resource_type": metadata.get("resource_type"),
            "account_id": metadata.get("account_id"),
            "region": metadata.get("region"),
            "event_timestamp": metadata.get("timestamp")
        })

        # TODO 4: Goi route_to_compliance(metadata, event)
        routing_status = route_to_compliance(metadata, event)

        if routing_status == "SKIPPED":
            log_structured("INFO", "EVENT_SKIPPED", {
                "message": "Event was skipped (unsupported)",
                "source": event.get("source"),
                "detail_type": event.get("detail-type"),
                "event_name": event.get("detail", {}).get("eventName"),
                "account_id": event.get("account"),
                "region": event.get("region")
            })
            return {
                "statusCode": 200,
                "body": json.dumps({"status": "SKIPPED"})
            }

        # TODO 5: Log event_routed hoac event_processed
        log_structured("INFO", "EVENT_PROCESSED", {
            "message": "Event routed to compliance checkers successfully",
            "resource_type": metadata.get("resource_type"),
            "resource_id": metadata.get("resource_id"),
            "account_id": metadata.get("account_id"),
            "region": metadata.get("region")
        })

        # TODO 6: Return response thanh cong
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "ROUTED"})
        }

    except Exception as exc:
        # TODO 7: Log event_processing_failed theo REQ-1.6
        log_structured("ERROR", "EVENT_PROCESSING_FAILED", {
            "error": str(exc),
            "source": event.get("source"),
            "detail_type": event.get("detail-type"),
            "account_id": event.get("account"),
            "region": event.get("region"),
            "raw_event": event
        })

        # TODO 8: Khong re-raise
        # TODO 9: Return response loi co kiem soat
        return {
            "statusCode": 500,
            "body": json.dumps({
                "status": "ERROR",
                "message": "Internal server error during event processing"
            })
        }