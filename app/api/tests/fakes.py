"""In-memory stand-ins for S3 and SQS: just the calls the vault code makes."""

import io
import json


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.tags: dict[tuple[str, str], list] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key, VersionId=None):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key, VersionId=None):
        self.objects.pop((Bucket, Key), None)

    def get_object_tagging(self, Bucket, Key, VersionId=None):
        return {"TagSet": self.tags.get((Bucket, Key), [])}

    def put_object_tagging(self, Bucket, Key, Tagging):
        self.tags[(Bucket, Key)] = Tagging["TagSet"]

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k} for b, k in self.objects if b == Bucket and k.startswith(Prefix)]}


class FakeSQS:
    def __init__(self):
        self.sent = []

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append(json.loads(MessageBody))
