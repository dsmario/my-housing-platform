"""Local stand-in for GuardDuty Malware Protection for S3. Development only (Doc Vault §13).

Watches the quarantine bucket. Each new file gets the scan tag GuardDuty would
add, and a scan result in GuardDuty's EventBridge shape goes to the queue, so
vault_worker.py runs the same code locally as in AWS. Only the standard EICAR
antivirus test file counts as malware.

Run: python local_scanner.py
"""

import json
import logging
import os
import signal
import time

from vault_worker import SCAN_TAG, clients

log = logging.getLogger("local_scanner")

# Built from two halves so antivirus on a developer's machine doesn't flag this file.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def scan_result_event(bucket: str, key: str, result: str) -> dict:
    return {
        "source": "aws.guardduty",
        "detail-type": "GuardDuty Malware Protection Object Scan Result",
        "detail": {
            "schemaVersion": "1.0",
            "scanStatus": "COMPLETED",
            "resourceType": "S3_OBJECT",
            "s3ObjectDetails": {"bucketName": bucket, "objectKey": key},
            "scanResultDetails": {"scanResultStatus": result, "threats": None},
        },
    }


def scan_once(s3, sqs, bucket: str, queue_url: str) -> list[tuple[str, str]]:
    """Scans every untagged upload once. Returns (key, result) pairs."""
    done = []
    for obj in s3.list_objects_v2(Bucket=bucket, Prefix="uploads/").get("Contents", []):
        key = obj["Key"]
        tags = s3.get_object_tagging(Bucket=bucket, Key=key)["TagSet"]
        if any(t["Key"] == SCAN_TAG for t in tags):
            continue
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        result = "THREATS_FOUND" if EICAR in body else "NO_THREATS_FOUND"
        s3.put_object_tagging(Bucket=bucket, Key=key, Tagging={"TagSet": [{"Key": SCAN_TAG, "Value": result}]})
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(scan_result_event(bucket, key, result)))
        done.append((key, result))
    return done


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    bucket, queue_url = os.environ["VAULT_QUARANTINE_BUCKET"], os.environ["VAULT_SCAN_QUEUE_URL"]
    s3, sqs = clients()
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    log.info("watching %s", bucket)
    while not stopping:
        try:
            for key, result in scan_once(s3, sqs, bucket, queue_url):
                log.info("%s: %s", key, result)
        except Exception:  # noqa: BLE001 - storage or queue not up yet; try again
            log.exception("scan pass failed")
        time.sleep(2)


if __name__ == "__main__":
    run()
