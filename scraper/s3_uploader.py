"""
S3 uploader — pushes scraped output to S3 so results are durable off the box.

Design goals:
- FAIL-SAFE: an S3 error must never crash a scrape. Every method catches its
  own errors, logs, and returns a bool.
- CHEAP: skip files whose size already matches the S3 object (a growing JSONL
  re-uploads because its size changed; a finished file is skipped).
- CREDENTIALS: uses boto3's default chain — an EC2 instance IAM role in prod,
  or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars locally. No secrets in code.
"""
from pathlib import Path
from typing import Optional

from config.settings import settings
from scraper.utils.logger import get_logger

logger = get_logger("s3_uploader")


class S3Uploader:
    def __init__(self):
        self._enabled = bool(settings.s3_enabled and settings.s3_bucket)
        self._client = None
        if not self._enabled:
            logger.info("S3 upload disabled (set S3_ENABLED=true and S3_BUCKET to enable)")
            return
        try:
            import boto3  # imported lazily so the app runs without boto3 when S3 is off
            self._client = boto3.client("s3", region_name=settings.aws_region or None)
            logger.info(
                f"S3 upload enabled -> s3://{settings.s3_bucket}/{settings.s3_prefix}"
            )
        except Exception as e:
            self._enabled = False
            logger.error(f"S3 init failed, uploads disabled: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def _key(self, *parts: str) -> str:
        prefix = (settings.s3_prefix or "").strip("/")
        segs = [p.strip("/") for p in parts if p]
        return "/".join([s for s in ([prefix] + segs) if s])

    def _remote_size(self, key: str) -> Optional[int]:
        try:
            head = self._client.head_object(Bucket=settings.s3_bucket, Key=key)
            return int(head["ContentLength"])
        except Exception:
            return None  # missing object or head not permitted -> treat as "upload"

    def upload_file(self, local_path: Path, key: str, force: bool = False) -> bool:
        """Upload one file to s3://<bucket>/<key>. Skips if size already matches."""
        if not self.enabled:
            return False
        try:
            local_path = Path(local_path)
            if not local_path.exists() or local_path.stat().st_size == 0:
                return False
            if not force:
                local_size = local_path.stat().st_size
                if self._remote_size(key) == local_size:
                    return False  # unchanged since last sync
            self._client.upload_file(
                str(local_path),
                settings.s3_bucket,
                key,
                ExtraArgs={"ContentType": "application/json"},
            )
            logger.info(f"Uploaded s3://{settings.s3_bucket}/{key} ({local_path.stat().st_size} bytes)")
            return True
        except Exception as e:
            logger.error(f"S3 upload failed for {local_path}: {e}")
            return False

    def sync_dir(self, local_dir: Path, sub_prefix: str, patterns=("*.jsonl", "*.csv")) -> int:
        """Recursively upload matching files, preserving the sub-path in the key.

        e.g. data/json/delhi/delhi-part-00001.jsonl
             -> s3://<bucket>/<prefix>/<sub_prefix>/delhi/delhi-part-00001.jsonl
        """
        if not self.enabled:
            return 0
        local_dir = Path(local_dir)
        if not local_dir.exists():
            return 0
        uploaded = 0
        for pattern in patterns:
            for f in sorted(local_dir.rglob(pattern)):
                rel = f.relative_to(local_dir).as_posix()  # e.g. "delhi/delhi-part-00001.jsonl"
                if self.upload_file(f, self._key(sub_prefix, rel)):
                    uploaded += 1
        return uploaded

    def sync_output(self) -> dict:
        """Sync the standard output dirs (data/json, data/csv) to S3, including
        per-CSV chunk subfolders."""
        out = Path(settings.output_dir)
        json_n = self.sync_dir(out / "json", "json", patterns=("*.jsonl",))
        csv_n = self.sync_dir(out / "csv", "csv", patterns=("*.csv",))
        result = {"json_uploaded": json_n, "csv_uploaded": csv_n}
        logger.info(f"S3 sync complete: {result}")
        return result


# Global instance
s3_uploader = S3Uploader()
