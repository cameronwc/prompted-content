"""Upload rendered pins to R2 under pins/ using the repo's existing client
(tools/publish.py). Keys carry the content hash, so re-uploading an
unchanged pin is a HEAD + skip."""
from __future__ import annotations

import hashlib
from pathlib import Path

from publish import IMMUTABLE_CACHE, require_credentials, resolve_config, s3_client


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_key(prefix: str, cohort: str, pin_id: str, digest: str, ext: str) -> str:
    safe = pin_id.replace(":", "-")
    return f"{prefix}/{cohort}/{safe}-{digest[:12]}.{ext}"


def public_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/{key}"


class Uploader:
    def __init__(self, env: str = "dev", tf_dir: str | None = None):
        self.config = resolve_config(env, tf_dir)
        require_credentials()
        self.s3 = s3_client(self.config)
        self.bucket = self.config["bucket"]

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.s3.exceptions.ClientError:
            return False

    def put(self, path: Path, key: str, content_type: str) -> bool:
        """Upload unless the (content-addressed) key already exists."""
        if self.exists(key):
            return False
        self.s3.upload_file(str(path), self.bucket, key,
                            ExtraArgs={"ContentType": content_type, "CacheControl": IMMUTABLE_CACHE})
        return True
