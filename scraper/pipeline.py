"""
Data Pipeline with atomic writes, crash recovery, and per-CSV output.
"""
import csv
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Optional, Dict
from contextlib import contextmanager

import redis

from config.settings import settings
from scraper.utils.logger import get_logger


logger = get_logger("pipeline")


class DataPipeline:
    """
    Pipeline for saving scraped records with:
    - Atomic writes (write to temp, then rename)
    - Per-CSV JSONL output files
    - Deduplication via Redis or local file
    - Crash-safe operations
    """

    def __init__(self, json_path: Optional[Path] = None, csv_name: Optional[str] = None):
        # Chunking: when a csv_name is given, records are written into
        # data/json/<csv_name>/<csv_name>-part-NNNNN.jsonl, rolling to a new part
        # every settings.chunk_size records. Legacy single-file mode otherwise.
        self.csv_name = csv_name
        self.chunk_size = max(1, int(getattr(settings, "chunk_size", 10000)))
        if csv_name:
            self.chunk_dir = Path(settings.output_dir) / "json" / csv_name
            self.chunk_dir.mkdir(parents=True, exist_ok=True)
            # Resume the running count from records already written to disk so
            # chunk numbering continues correctly after a restart.
            self._count = self._count_existing_records()
            self.json_path = self._chunk_path_for(self._count)  # current target chunk
        else:
            # Daily file (legacy, no chunking)
            self.json_path = json_path or Path(settings.output_dir) / "json" / f"{date.today()}.jsonl"
            self.chunk_dir = None
            self._count = 0

        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        # Stable dedup file (independent of the rolling chunk path).
        self._seen_file = (
            (self.chunk_dir / f"{csv_name}.seen") if csv_name
            else self.json_path.with_suffix(".seen")
        )
        self._local_seen: set[str] = set()
        self._redis = None
        self._redis_available = False
        self._init_redis()

    def _chunk_path_for(self, count: int) -> Path:
        """The chunk file the (count)-th record (0-based) belongs in."""
        part = count // self.chunk_size + 1
        return self.chunk_dir / f"{self.csv_name}-part-{part:05d}.jsonl"

    def _count_existing_records(self) -> int:
        """Total records already written across this CSV's chunk files."""
        total = 0
        if self.chunk_dir and self.chunk_dir.exists():
            for f in self.chunk_dir.glob(f"{self.csv_name}-part-*.jsonl"):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        total += sum(1 for line in fh if line.strip())
                except Exception:
                    pass
        return total

    def _init_redis(self):
        """Initialize Redis connection with error handling."""
        try:
            self._redis = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5)
            self._redis.ping()
            self._redis_available = True
            logger.info("Redis connection established for deduplication")
        except Exception as e:
            self._redis = None
            self._redis_available = False
            logger.warning(f"Redis not available ({e}), using local file-based deduplication")
            self._load_local_seen()

    def _load_local_seen(self):
        """Load seen keys from local file."""
        seen_file = self._seen_file
        if seen_file.exists():
            try:
                content = seen_file.read_text(encoding="utf-8").strip()
                if content:
                    self._local_seen = set(content.splitlines())
                    logger.debug(f"Loaded {len(self._local_seen)} seen keys from {seen_file}")
            except Exception as e:
                logger.warning(f"Failed to load local seen file: {e}")
                self._local_seen = set()

    def _save_local_seen(self):
        """Save seen keys to local file atomically."""
        seen_file = self._seen_file
        try:
            # Atomic write
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=seen_file.parent,
                delete=False,
                suffix=".tmp",
                encoding="utf-8",
            ) as f:
                f.write("\n".join(sorted(self._local_seen)) + "\n")
                temp_name = f.name
            os.replace(temp_name, seen_file)
        except Exception as e:
            logger.error(f"Failed to save local seen file: {e}")

    def is_duplicate(self, unique_key: str) -> bool:
        """Check if a record has already been processed."""
        if self._redis_available and self._redis:
            try:
                return bool(self._redis.sismember("scraper:seen_urls", unique_key))
            except Exception as e:
                logger.warning(f"Redis error checking duplicate, falling back to local: {e}")
                self._redis_available = False
                self._init_redis()
        return unique_key in self._local_seen

    def save(self, record: Dict[str, Any], unique_key: str) -> bool:
        """
        Save a record to JSONL file.
        Returns True if saved, False if duplicate.
        """
        if self.is_duplicate(unique_key):
            return False

        # Mark as seen first (before write) to prevent duplicates on crash
        if self._redis_available and self._redis:
            try:
                self._redis.sadd("scraper:seen_urls", unique_key)
            except Exception as e:
                logger.warning(f"Redis error marking seen, falling back to local: {e}")
                self._redis_available = False
                self._init_redis()
                self._local_seen.add(unique_key)
                self._save_local_seen()
        else:
            self._local_seen.add(unique_key)
            self._save_local_seen()

        # In chunk mode, route this record to the current chunk file (rolls to a
        # new part every chunk_size records).
        if self.chunk_name_active():
            self.json_path = self._chunk_path_for(self._count)
            self.json_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write to JSONL
        try:
            with tempfile.NamedTemporaryFile(
                mode="a",
                dir=self.json_path.parent,
                delete=False,
                suffix=".tmp",
                encoding="utf-8",
            ) as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                temp_name = f.name
            # Append temp file to main file atomically
            with open(self.json_path, "a", encoding="utf-8") as main_f:
                with open(temp_name, "r", encoding="utf-8") as temp_f:
                    main_f.write(temp_f.read())
            os.unlink(temp_name)
            self._count += 1
            return True
        except Exception as e:
            logger.error(f"Failed to save record: {e}")
            # Try direct append as fallback
            try:
                with open(self.json_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._count += 1
                return True
            except Exception as e2:
                logger.error(f"Fallback save also failed: {e2}")
                return False

    def chunk_name_active(self) -> bool:
        return bool(self.csv_name and self.chunk_dir is not None)

    def consolidate_to_csv(self, jsonl_path: Optional[Path] = None) -> Optional[Path]:
        """Roll a JSONL file into CSV. Returns CSV path or None."""
        jsonl_path = jsonl_path or self.json_path
        if not jsonl_path.exists():
            logger.warning(f"No JSONL file to consolidate at {jsonl_path}")
            return None

        try:
            rows = []
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Skipping invalid JSON line: {e}")
                            continue

            if not rows:
                logger.info(f"No valid records in {jsonl_path}")
                return None

            csv_path = Path(settings.output_dir) / "csv" / f"{jsonl_path.stem}.csv"
            csv_path.parent.mkdir(parents=True, exist_ok=True)

            # Get all unique fieldnames
            fieldnames = sorted({key for row in rows for key in row.keys()})

            # Atomic write
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=csv_path.parent,
                delete=False,
                suffix=".tmp",
                newline="",
                encoding="utf-8",
            ) as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
                temp_name = f.name

            os.replace(temp_name, csv_path)
            logger.info(f"Consolidated {len(rows)} records -> {csv_path}")
            return csv_path

        except Exception as e:
            logger.error(f"Failed to consolidate to CSV: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics."""
        if self._redis_available and self._redis:
            try:
                seen_count = self._redis.scard("scraper:seen_urls")
            except Exception:
                seen_count = len(self._local_seen)
        else:
            seen_count = len(self._local_seen)

        return {
            "json_path": str(self.json_path),
            "seen_count": seen_count,
            "redis_available": self._redis_available,
        }


class MultiPipeline:
    """Manages multiple pipelines for different CSV files."""

    def __init__(self):
        self._pipelines: Dict[str, DataPipeline] = {}

    def get_pipeline(self, csv_name: str) -> DataPipeline:
        """Get or create pipeline for a CSV file."""
        if csv_name not in self._pipelines:
            self._pipelines[csv_name] = DataPipeline(csv_name=csv_name)
        return self._pipelines[csv_name]

    def consolidate_all(self) -> Dict[str, Optional[Path]]:
        """Consolidate all JSONL files to CSV."""
        results = {}
        for csv_name, pipeline in self._pipelines.items():
            results[csv_name] = pipeline.consolidate_to_csv()
        return results

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get stats for all pipelines."""
        return {name: p.get_stats() for name, p in self._pipelines.items()}


# Global instance for backward compatibility
pipeline = DataPipeline()
multi_pipeline = MultiPipeline()