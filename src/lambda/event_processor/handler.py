import json
import logging
from datetime import datetime, timezone

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
    if resource_id is None:
        raise ValueError(f"Event matches {resource_type} pattern but missing resource_id. Cannot route to compliance checker.")

    if resource_type == "ec2":
        # TODO 2: chuan bi goi cost guardrail checker
        pass
    elif resource_type == "s3":
        # TODO 3: chuan bi goi security guardrail checker
        pass
    elif resource_type == "iam":
        # TODO 4: chuan bi goi iam guardrail checker
        pass
    elif resource_type == "ebs":
        # TODO 5: chuan bi goi compliance guardrail checker
        pass
    else:
        # TODO 6: roi vao nhanh khong mong doi
        raise ValueError(f"Unknown resource_type for routing: {resource_type}")

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