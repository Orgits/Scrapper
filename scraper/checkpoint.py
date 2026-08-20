"""
Checkpoint system for CSV processing - enables resume after crashes/restarts.
"""
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from threading import Lock

from config.settings import settings
from scraper.utils.logger import get_logger


logger = get_logger("checkpoint")


@dataclass
class CSVCheckpoint:
    """Checkpoint state for a single CSV file."""
    csv_file: str
    total_rows: int
    current_row_index: int
    processed_count: int
    skipped_count: int
    failed_count: int
    last_updated: float
    priority: bool = False

    @property
    def progress_pct(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return (self.current_row_index / self.total_rows) * 100

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CSVCheckpoint":
        return cls(**data)


@dataclass
class GlobalCheckpoint:
    """Global checkpoint state across all CSV files."""
    csv_checkpoints: Dict[str, CSVCheckpoint]
    active_csv: Optional[str]
    last_updated: float
    completed_files: list[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "csv_checkpoints": {k: v.to_dict() for k, v in self.csv_checkpoints.items()},
            "active_csv": self.active_csv,
            "last_updated": self.last_updated,
            "completed_files": self.completed_files,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GlobalCheckpoint":
        return cls(
            csv_checkpoints={k: CSVCheckpoint.from_dict(v) for k, v in data.get("csv_checkpoints", {}).items()},
            active_csv=data.get("active_csv"),
            last_updated=data.get("last_updated", 0),
            completed_files=data.get("completed_files", []),
        )


class CheckpointManager:
    """Manages checkpoints for crash recovery and resume capability."""

    def __init__(self, checkpoint_dir: Optional[Path] = None):
        self.checkpoint_dir = checkpoint_dir or settings.checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file = self.checkpoint_dir / "global_checkpoint.json"
        self._lock = Lock()
        self._global_checkpoint: Optional[GlobalCheckpoint] = None

    def _get_csv_checkpoint_path(self, csv_name: str) -> Path:
        """Get path for individual CSV checkpoint file."""
        safe_name = csv_name.replace("/", "_").replace(".", "_")
        return self.checkpoint_dir / f"checkpoint_{safe_name}.json"

    def load(self) -> GlobalCheckpoint:
        """Load global checkpoint from disk."""
        with self._lock:
            if self._global_checkpoint is not None:
                return self._global_checkpoint

            if self.checkpoint_file.exists():
                try:
                    with open(self.checkpoint_file, "r") as f:
                        data = json.load(f)
                    self._global_checkpoint = GlobalCheckpoint.from_dict(data)
                    logger.info(f"Loaded checkpoint: {len(self._global_checkpoint.csv_checkpoints)} CSVs, "
                               f"active={self._global_checkpoint.active_csv}, "
                               f"completed={len(self._global_checkpoint.completed_files)}")
                    return self._global_checkpoint
                except Exception as e:
                    logger.warning(f"Failed to load checkpoint: {e}")

            self._global_checkpoint = GlobalCheckpoint(
                csv_checkpoints={},
                active_csv=None,
                last_updated=time.time(),
                completed_files=[],
            )
            return self._global_checkpoint

    def save(self, checkpoint: Optional[GlobalCheckpoint] = None):
        """Save global checkpoint to disk atomically."""
        with self._lock:
            if checkpoint is not None:
                self._global_checkpoint = checkpoint
            if self._global_checkpoint is None:
                return

            self._global_checkpoint.last_updated = time.time()
            data = self._global_checkpoint.to_dict()

            # Atomic write: write to temp file then rename
            temp_file = self.checkpoint_file.with_suffix(".tmp")
            try:
                with open(temp_file, "w") as f:
                    json.dump(data, f)
                temp_file.replace(self.checkpoint_file)
            except Exception as e:
                logger.error(f"Failed to save checkpoint: {e}")
                if temp_file.exists():
                    temp_file.unlink(missing_ok=True)

    def initialize_csv(self, csv_name: str, total_rows: int, priority: bool = False) -> CSVCheckpoint:
        """Initialize or load checkpoint for a CSV file."""
        checkpoint = self.load()

        if csv_name in checkpoint.csv_checkpoints:
            existing = checkpoint.csv_checkpoints[csv_name]
            if existing.total_rows == total_rows:
                logger.info(f"Resuming {csv_name} from row {existing.current_row_index} "
                           f"({existing.progress_pct:.1f}%)")
                return existing

        # New or changed CSV
        csv_checkpoint = CSVCheckpoint(
            csv_file=csv_name,
            total_rows=total_rows,
            current_row_index=0,
            processed_count=0,
            skipped_count=0,
            failed_count=0,
            last_updated=time.time(),
            priority=priority,
        )
        checkpoint.csv_checkpoints[csv_name] = csv_checkpoint
        self.save(checkpoint)
        logger.info(f"Initialized checkpoint for {csv_name} ({total_rows} rows)")
        return csv_checkpoint

    def update_csv_progress(
        self,
        csv_name: str,
        current_row_index: int,
        processed_delta: int = 0,
        skipped_delta: int = 0,
        failed_delta: int = 0,
    ) -> CSVCheckpoint:
        """Update progress for a CSV file."""
        checkpoint = self.load()
        if csv_name not in checkpoint.csv_checkpoints:
            logger.warning(f"CSV {csv_name} not in checkpoint, initializing")
            return self.initialize_csv(csv_name, 0)

        cp = checkpoint.csv_checkpoints[csv_name]
        cp.current_row_index = current_row_index
        cp.processed_count += processed_delta
        cp.skipped_count += skipped_delta
        cp.failed_count += failed_delta
        cp.last_updated = time.time()

        # Save periodically
        if current_row_index % settings.checkpoint_interval == 0:
            self.save(checkpoint)

        return cp

    def mark_csv_completed(self, csv_name: str):
        """Mark a CSV file as fully processed."""
        checkpoint = self.load()
        if csv_name in checkpoint.csv_checkpoints:
            cp = checkpoint.csv_checkpoints[csv_name]
            cp.current_row_index = cp.total_rows
            cp.last_updated = time.time()
            if csv_name not in checkpoint.completed_files:
                checkpoint.completed_files.append(csv_name)
            self.save(checkpoint)
            logger.info(f"Marked {csv_name} as completed")

    def set_active_csv(self, csv_name: Optional[str]):
        """Set the currently active CSV file."""
        checkpoint = self.load()
        checkpoint.active_csv = csv_name
        checkpoint.last_updated = time.time()
        self.save(checkpoint)

    def get_resume_state(self, csv_files: list[str]) -> Dict[str, Any]:
        """Get resume state for all CSV files."""
        checkpoint = self.load()
        result = {}
        for csv_name in csv_files:
            if csv_name in checkpoint.csv_checkpoints:
                cp = checkpoint.csv_checkpoints[csv_name]
                if cp.current_row_index < cp.total_rows and csv_name not in checkpoint.completed_files:
                    result[csv_name] = {
                        "resume": True,
                        "start_row": cp.current_row_index,
                        "processed": cp.processed_count,
                        "skipped": cp.skipped_count,
                        "failed": cp.failed_count,
                    }
                else:
                    result[csv_name] = {"resume": False, "completed": True}
            else:
                result[csv_name] = {"resume": False, "start_row": 0}
        return result

    def clear_completed(self):
        """Clear completed files from checkpoint (for fresh start)."""
        checkpoint = self.load()
        checkpoint.completed_files = []
        checkpoint.active_csv = None
        for cp in checkpoint.csv_checkpoints.values():
            cp.current_row_index = 0
            cp.processed_count = 0
            cp.skipped_count = 0
            cp.failed_count = 0
        self.save(checkpoint)
        logger.info("Cleared all checkpoint data for fresh start")


# Global instance
checkpoint_manager = CheckpointManager()