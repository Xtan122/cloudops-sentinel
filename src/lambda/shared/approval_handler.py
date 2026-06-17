import base64
import binascii
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import boto3
from botocore.exceptions import ClientError, BotoCoreError

# Thêm thư mục gốc của lambda (chứa remediation_engine) vào sys.path
# Đảm bảo import thành công dù Lambda package path thay đổi hay đang chạy test
lambda_root = Path(__file__).resolve().parent.parent
if str(lambda_root) not in sys.path:
    sys.path.append(str(lambda_root))

logger = logging.getLogger(__name__)

# Cache DynamoDB table
_table = None

def _get_table():
    global _table
    if _table is None:
        table_name = os.environ.get("APPROVAL_TABLE_NAME", "cloudops-approval-requests")
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(table_name)
    return _table

def parse_slack_payload(raw_body: str) -> dict:
    """Parse body callback từ Slack."""
    if not isinstance(raw_body, str) or not raw_body.strip():
        raise ValueError("raw_body must be a non-empty string")
    parsed = parse_qs(raw_body)
    if "payload" not in parsed:
        raise KeyError("Missing 'payload' in Slack callback body")
    try:
        return json.loads(parsed["payload"][0])
    except json.JSONDecodeError as e:
        logger.error("Failed to decode JSON payload: %s", e)
        raise ValueError("Invalid JSON in payload") from e


def _extract_request_body(event: dict) -> str:
    """Extract API Gateway body, decoding base64 form posts when needed."""
    raw_body = event.get("body", "")

    if not event.get("isBase64Encoded"):
        return raw_body

    if not isinstance(raw_body, str) or not raw_body.strip():
        return ""

    try:
        return base64.b64decode(raw_body).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        logger.error("Failed to decode base64 Slack callback body: %s", exc)
        raise ValueError("Invalid base64-encoded callback body") from exc


def _parse_expires_at_epoch(record: dict) -> int | None:
    """Parse expires_at_epoch from DynamoDB record."""
    try:
        val = record.get("expires_at_epoch")
        if val is None:
            return None
        return int(val)
    except (ValueError, TypeError):
        return None

def _record_remediation_result(request_id: str, success: bool, result: dict | None = None, error: str | None = None) -> bool:
    """Lưu kết quả remediation vào approval record để audit theo REQ-12.2/REQ-12.3."""
    table = _get_table()
    now_iso = datetime.now(timezone.utc).isoformat()
    
    # TODO 2: Nếu success:
    if success:
        update_expr = "SET remediation_status = :status, remediation_result = :result, remediation_completed_at = :time"
        expr_values = {
            ":status": "SUCCEEDED",
            ":result": result if result is not None else {},
            ":time": now_iso,
            ":approved": "APPROVED"
        }
    # TODO 3: Nếu failed:
    else:
        update_expr = "SET remediation_status = :status, remediation_error = :err, remediation_completed_at = :time"
        expr_values = {
            ":status": "FAILED",
            ":err": error if error is not None else "Unknown error",
            ":time": now_iso,
            ":approved": "APPROVED"
        }
        
    try:
        # TODO 4: Dùng best-effort update
        table.update_item(
            Key={"request_id": request_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=expr_values,
            ConditionExpression="#s = :approved"
        )
        return True
    except (ClientError, BotoCoreError) as e:
        logger.critical("CRITICAL: Failed to record remediation result for request %s. Audit trail is inconsistent: %s", request_id, e)
        return False

def dispatch_remediation(request_id: str, action_type: str, action_parameters: dict):
    """Gọi remediation function tương ứng với action_type đã lưu trong approval record."""
    logger.info("Dispatching remediation for request_id=%s, action_type=%s", request_id, action_type)
    from shared.dry_run import get_dry_run_mode
    dry_run = get_dry_run_mode()

    try:
        if action_type == "stop_ec2":
            from remediation_engine import remediation_ec2
            res = remediation_ec2.stop_non_compliant_ec2(
                instance_id=action_parameters["instance_id"],
                region=action_parameters["region"],
                dry_run=dry_run
            )
            return True, res, None
        elif action_type == "revert_s3_to_private":
            from remediation_engine import remediation_s3
            res = remediation_s3.revert_s3_bucket_to_private(
                bucket_name=action_parameters["bucket_name"],
                region=action_parameters["region"],
                dry_run=dry_run
            )
            return True, res, None
        elif action_type == "deactivate_iam_key":
            from remediation_engine import remediation_iam
            res = remediation_iam.deactivate_access_key(
                username=action_parameters["username"],
                access_key_id=action_parameters["access_key_id"],
                dry_run=dry_run
            )
            return True, res, None
        elif action_type == "tag_ebs_noncompliant":
            from remediation_engine import remediation_ebs
            res = remediation_ebs.tag_non_compliant_ebs(
                volume_id=action_parameters["volume_id"],
                region=action_parameters["region"],
                dry_run=dry_run
            )
            return True, res, None
        else:
            logger.error("Unknown action_type: %s", action_type)
            return False, None, f"Action type {action_type} not supported"
            
    except ImportError as e:
        logger.error("Failed to import remediation module for %s: %s", action_type, e)
        return False, None, f"Import error: {str(e)}"
    except KeyError as e:
        logger.error("Missing parameter %s for action %s", e, action_type)
        return False, None, f"Missing parameter: {str(e)}"
    except (ClientError, BotoCoreError) as e:
        logger.error("AWS API error during remediation %s: %s", action_type, e)
        return False, None, f"AWS API error: {str(e)}"
    except Exception as e:
        logger.error("Unexpected error during remediation %s for request %s: %s", action_type, request_id, e, exc_info=True)
        return False, None, f"Unexpected error: {str(e)}"

def lambda_handler(event, context):
    """Xử lý Slack interactive callback cho approval workflow."""
    try:
        raw_body = _extract_request_body(event)
        payload = parse_slack_payload(raw_body)
    except (ValueError, KeyError) as e:
        logger.error("Invalid payload format: %s", e)
        return {"statusCode": 400, "body": "Invalid payload format"}
    except Exception as e:
        logger.error("Unexpected error parsing payload: %s", e)
        return {"statusCode": 500, "body": "Internal server error"}

    # FIXME: Enterprise Enhancement
    # 1. Implement Slack signature verification using SLACK_SIGNING_SECRET to ensure 
    #    the payload actually came from Slack (preventing spoofing attacks).

    actions = payload.get("actions", [])
    if not actions:
        return {"statusCode": 400, "body": "No actions found"}
    
    action = actions[0]
    action_id = action.get("action_id")
    request_id = action.get("value")
    
    # Lọc action lạ trước khi lookup DB
    if action_id not in ("approve_action", "reject_action"):
        logger.warning("Invalid action_id: %s", action_id)
        return {"statusCode": 400, "body": "Invalid action"}
        
    # Lọc request_id rỗng
    if not request_id:
        logger.warning("Empty request_id in payload")
        return {"statusCode": 400, "body": "Missing request_id"}

    user = payload.get("user", {})
    user_id = user.get("id", "unknown_id")
    user_name = user.get("name", "unknown_name")
    approver = f"{user_name} ({user_id})"

    table = _get_table()
    
    try:
        response = table.get_item(Key={"request_id": request_id})
        record = response.get("Item")
    except (ClientError, BotoCoreError) as e:
        logger.error("DynamoDB get_item error for request_id %s: %s", request_id, e)
        return {"statusCode": 500, "body": "Internal server error"}

    if not record:
        logger.warning("Approval record not found for request_id: %s", request_id)
        return {"statusCode": 200, "body": "Approval request not found. It may have expired."}
        
    if record.get("status") != "PENDING":
        logger.info("Request %s is already processed. Current status: %s", request_id, record.get("status"))
        return {"statusCode": 200, "body": f"Request already processed. Status: {record.get('status')}"}

    now = int(time.time())
    
    expires_at_epoch = _parse_expires_at_epoch(record)
    if expires_at_epoch is None:
        logger.error("Invalid expires_at_epoch in record %s", request_id)
        # Mark as TIMED_OUT as a terminal state for corrupted record
        _mark_timed_out(record)
        return {"statusCode": 200, "body": "Invalid approval record. Marked as timed out."}
        
    if now > expires_at_epoch:
        logger.warning("Request %s has timed out", request_id)
        _mark_timed_out(record)
        return {"statusCode": 200, "body": "This approval request has expired."}

    if action_id == "reject_action":
        try:
            success = _reject_request(record, approver)
        except (ClientError, BotoCoreError):
            return {"statusCode": 200, "body": "Approval could not be processed."}
            
        if success:
            logger.info("Request %s REJECTED by %s", request_id, approver)
            return {"statusCode": 200, "body": f"Remediation rejected by {approver}."}
        else:
            logger.warning("Request %s was already processed.", request_id)
            return {"statusCode": 200, "body": "Request was already processed."}

    elif action_id == "approve_action":
        try:
            success = _approve_request(record, approver)
        except (ClientError, BotoCoreError):
            return {"statusCode": 200, "body": "Approval could not be processed."}
            
        if not success:
            return {"statusCode": 200, "body": "Request was already processed."}

        logger.info("Request %s APPROVED by %s", request_id, approver)
        action_type = record.get("action_type")
        action_parameters = record.get("action_parameters", {})
        
        # FIXME: Enterprise Enhancement
        # 2. Enqueue an async job (e.g. via SQS or Step Functions) instead of executing 
        #    remediation synchronously here to avoid Slack's 3-second timeout limit.
        dispatch_success, result_dict, error_text = dispatch_remediation(request_id, action_type, action_parameters)
        logger.info("Remediation dispatch result for %s: %s", request_id, dispatch_success)
        
        # TODO: nếu thành công, _record_remediation_result(...)
        # TODO: nếu thất bại, _record_remediation_
        audit_success = _record_remediation_result(
            request_id=request_id, 
            success=dispatch_success, 
            result=result_dict, 
            error=error_text
        )
        
        result_text = "successfully" if dispatch_success else "with errors"
        if not audit_success:
            return {"statusCode": 200, "body": f"Remediation approved by {approver} and executed {result_text}, but failed to update audit log."}
        return {"statusCode": 200, "body": f"Remediation approved by {approver} and executed {result_text}."}


def _approve_request(record: dict, approver: str) -> bool:
    request_id = record.get("request_id")
    now_iso = datetime.now(timezone.utc).isoformat()
    table = _get_table()
    
    try:
        table.update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #s = :s, approved_by = :user, approved_at = :time",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "APPROVED", 
                ":user": approver, 
                ":time": now_iso,
                ":pending": "PENDING"
            },
            ConditionExpression="#s = :pending"
        )
        return True
    except (ClientError, BotoCoreError) as e:
        if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("Request %s was already processed.", request_id)
            return False
        else:
            logger.warning("DynamoDB error updating request %s to APPROVED: %s", request_id, e)
            raise

def _reject_request(record: dict, approver: str) -> bool:
    request_id = record.get("request_id")
    now_iso = datetime.now(timezone.utc).isoformat()
    table = _get_table()
    
    try:
        table.update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #s = :s, rejected_by = :user, rejected_at = :time",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "REJECTED", 
                ":user": approver, 
                ":time": now_iso,
                ":pending": "PENDING"
            },
            ConditionExpression="#s = :pending"
        )
        return True
    except (ClientError, BotoCoreError) as e:
        if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("Request %s was already processed.", request_id)
            return False
        else:
            logger.warning("DynamoDB error updating request %s to REJECTED: %s", request_id, e)
            raise

def _mark_timed_out(record: dict) -> bool:
    request_id = record.get("request_id")
    now_iso = datetime.now(timezone.utc).isoformat()
    table = _get_table()
    
    try:
        table.update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #s = :s, timed_out_at = :time",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "TIMED_OUT",
                ":time": now_iso,
                ":pending": "PENDING"
            },
            ConditionExpression="#s = :pending"
        )
        return True
    except (ClientError, BotoCoreError) as e:
        if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("Request %s was already processed.", request_id)
        else:
            logger.warning("DynamoDB error updating status to TIMED_OUT for %s: %s", request_id, e)
        return False
