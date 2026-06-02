import logging

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)


def deactivate_access_key(username: str, access_key_id: str, dry_run: bool) -> dict:
    """Vô hiệu hóa IAM access key theo REQ-4.2 và REQ-4.4."""
    action = {
        "action": "deactivate_iam_access_key",
        "resource_type": "iam_access_key",
        "resource_id": access_key_id,
        "username": username,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info(
            "[DRY-RUN] Would deactivate IAM access key %s for user %s",
            access_key_id,
            username,
        )
        action.update(
            {
                "executed": False,
                "reason": "dry_run_mode",
                "status": "skipped",
            }
        )
        return action

    iam = boto3.client("iam")

    try:
        iam.update_access_key(
            UserName=username,
            AccessKeyId=access_key_id,
            Status="Inactive",
        )

        action.update(
            {
                "executed": True,
                "status": "access_key_deactivated",
            }
        )
        return action

    except botocore.exceptions.ClientError as exc:
        logger.error(
            "Failed to deactivate access key %s for user %s: %s",
            access_key_id,
            username,
            exc,
        )
        raise
