import logging

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)


def revert_s3_bucket_to_private(bucket_name: str, region: str, dry_run: bool) -> dict:
    """
    Revert S3 bucket policy ve private theo REQ-3.4 va REQ-3.6.
    """

    action = {
        "action": "delete_s3_bucket_policy",
        "resource_type": "s3",
        "resource_id": bucket_name,
        "region": region,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info(
            "[DRY-RUN] Would delete public S3 bucket policy for bucket %s in region %s",
            bucket_name,
            region,
        )
        action.update({
            "executed": False,
            "reason": "dry_run_mode",
            "status": "skipped"
        })
        return action

    s3 = boto3.client("s3", region_name=region)

    try:
        s3.delete_bucket_policy(Bucket=bucket_name)

        action.update({
            "executed": True,
            "status": "bucket_policy_deleted"
        })
        return action

    except botocore.exceptions.ClientError as exc:
        logger.error(
            "Error deleting bucket policy for bucket %s in region %s: %s",
            bucket_name,
            region,
            exc,
        )
        raise

