import csv
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import redis

from config.settings import settings
from scraper.utils.logger import logger

try:
    _redis = redis.from_url(settings.redis_url, decode_responses=True)
    _redis.ping()
    REDIS_AVAILABLE = True
except Exception:
    _redis = None
    REDIS_AVAILABLE = False
    logger.warning("Redis not available, using local file-based deduplication")

SEEN_SET_KEY = "scraper:seen_urls"
SEEN_FILE = Path(settings.output_dir) / "json" / ".seen_keys"


class DataPipeline:
    """
    Every record is appended to a daily JSON-lines file the instant it's
    scraped (so a crash never loses already-collected data), and
    consolidate_to_csv() rolls that file into CSV on demand. Dedup is
    tracked in a Redis set (or local file fallback) keyed by a unique field
    (usually the detail URL) so concurrent workers / re-runs never
    double-write a record.
    """

    def __init__(self):
        self.json_path = Path(settings.output_dir) / "json" / f"{date.today()}.jsonl"
        self._local_seen: set[str] = set()
        if not REDIS_AVAILABLE:
            self._load_local_seen()

    def _load_local_seen(self):
        if SEEN_FILE.exists():
            try:
                content = SEEN_FILE.read_text(encoding="utf-8").strip()
                if content:
                    self._local_seen = set(content.splitlines())
            except Exception:
                self._local_seen = set()

    def _save_local_seen(self):
        try:
            SEEN_FILE.write_text("\n".join(self._local_seen) + "\n", encoding="utf-8")
        except Exception:
            pass

    def is_duplicate(self, unique_key: str) -> bool:
        if REDIS_AVAILABLE and _redis:
            return bool(_redis.sismember(SEEN_SET_KEY, unique_key))
        return unique_key in self._local_seen

    def save(self, record: dict[str, Any], unique_key: str):
        if self.is_duplicate(unique_key):
            return
        if REDIS_AVAILABLE and _redis:
            _redis.sadd(SEEN_SET_KEY, unique_key)
        else:
            self._local_seen.add(unique_key)
            self._save_local_seen()
        with open(self.json_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def consolidate_to_csv(self, jsonl_path: Optional[Path] = None):
        """Roll a JSONL file into CSV. Run this at the end of a crawl cycle."""
        jsonl_path = jsonl_path or self.json_path
        if not jsonl_path.exists():
            logger.warning(f"No JSONL file to consolidate at {jsonl_path}")
            return

        rows = [json.loads(line) for line in jsonl_path.open(encoding="utf-8") if line.strip()]
        if not rows:
            return

        csv_path = Path(settings.output_dir) / "csv" / f"{jsonl_path.stem}.csv"
        fieldnames = sorted({key for row in rows for key in row.keys()})
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"Consolidated {len(rows)} records -> {csv_path}")


pipeline = DataPipeline()
