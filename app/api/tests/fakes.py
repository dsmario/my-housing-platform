"""In-memory stand-ins for S3 and SQS: just the calls the vault code makes."""

import io
import itertools
import json


class FakeS3:
    """Buckets are versioned, like the real ones: a put adds a version; a delete
    without a version only hides the object (a delete marker), and its versions stay
    until each is deleted by id."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}  # what a plain GET returns
        self.versions: dict[tuple[str, str], list[tuple[str, bytes]]] = {}
        self.tags: dict[tuple[str, str], list] = {}
        self._ids = (f"v{n}" for n in itertools.count(1))

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body
        self.versions.setdefault((Bucket, Key), []).append((next(self._ids), Body))

    def get_object(self, Bucket, Key, VersionId=None):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key, VersionId=None):
        self.objects.pop((Bucket, Key), None)
        if VersionId:
            kept = [v for v in self.versions.get((Bucket, Key), []) if v[0] != VersionId]
            if kept:
                self.versions[(Bucket, Key)] = kept
            else:
                self.versions.pop((Bucket, Key), None)

    def list_object_versions(self, Bucket, Prefix):
        return {
            "Versions": [
                {"Key": k, "VersionId": vid}
                for (b, k), versions in self.versions.items()
                if b == Bucket and k.startswith(Prefix)
                for vid, _ in versions
            ]
        }

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
