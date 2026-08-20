"""
CSV Processor for Tofler.in Company Data Enrichment with:
- Priority-based processing
- Checkpoint/resume capability
- Graceful shutdown
- Structured logging
"""
import csv
import asyncio
import re
import time
import signal
from pathlib import Path
from urllib.parse import quote
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

from config.settings import settings
from scraper.browser import browser_manager
from scraper.fetcher import fetch_page_with_retry
from scraper.exceptions import FetchFailedError
from scraper.parser import parse_company_page
from scraper.pipeline import DataPipeline, multi_pipeline
from scraper.checkpoint import checkpoint_manager, CSVCheckpoint
from scraper.utils.logger import get_logger


logger = get_logger("csv_processor")

COMPANYDATA_DIR = Path("companydata")
OUTPUT_DIR = Path(settings.output_dir) / "json"

# Fields we expect from CSV (at minimum CompanyName and CIN)
REQUIRED_CSV_FIELDS = ["CompanyName", "CIN"]

# Tofler target name
TOFLER_TARGET = "tofler"


def normalize_company_name_for_url(name: str) -> str:
    """
    Normalize company name for tofler.in URL format.
    
    Example: "Fintech Compu Systems Limited" -> "fintech-compu-systems-limited"
    """
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")
    return quote(name)


def construct_tofler_url(company_name: str, cin: str) -> str:
    """Construct tofler.in company page URL."""
    normalized_name = normalize_company_name_for_url(company_name)
    return f"https://www.tofler.in/{normalized_name}/company/{cin}"


def get_csv_files() -> list[Path]:
    """Get all CSV files from companydata directory."""
    if not COMPANYDATA_DIR.exists():
        logger.error(f"Companydata directory not found: {COMPANYDATA_DIR}")
        return []
    
    csv_files = list(COMPANYDATA_DIR.glob("*.csv"))
    logger.info(f"Found {len(csv_files)} CSV files in {COMPANYDATA_DIR}")
    return csv_files


def get_output_jsonl_path(csv_path: Path) -> Path:
    """Get output JSONL path based on input CSV filename."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{csv_path.stem}.jsonl"


@dataclass
class CSVFileState:
    """Tracks processing state for a single CSV file (in-memory)."""
    path: Path
    checkpoint: CSVCheckpoint
    rows: list[dict] = field(default_factory=list)
    is_priority: bool = False
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def progress_pct(self) -> float:
        if self.checkpoint.total_rows == 0:
            return 0.0
        return (self.checkpoint.current_row_index / self.checkpoint.total_rows) * 100

    @property
    def has_rows_remaining(self) -> bool:
        return self.checkpoint.current_row_index < self.checkpoint.total_rows


class ToflerCSVProcessor:
    """Processes CSV files with priority scheduling and checkpoints."""

    def __init__(self, max_concurrent: int = None):
        self.max_concurrent = max_concurrent or settings.csv_processing_concurrency
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        self.target_config = None
        
        # Priority configuration from settings
        self.priority_files = self._parse_priority_files(settings.priority_csv_files)
        self.fallback_threshold = settings.priority_fallback_threshold
        self.recheck_interval = settings.priority_recheck_interval
        self.test_limit = settings.test_limit
        
        # State tracking
        self.csv_states: Dict[str, CSVFileState] = {}
        self.active_priority_name: Optional[str] = None
        self.last_priority_check = 0.0
        self._shutdown = False
        self._shutdown_event = asyncio.Event()
        # Circuit breaker state
        self._global_consecutive_failures = 0
        self._current_row_attempts = 0
        self._current_row_pause_cycles = 0
        self._last_success_time = time.time()  # any successful fetch, any CSV

    def _parse_priority_files(self, priority_setting: str) -> list[str]:
        """Parse comma-separated priority file list from settings."""
        if not priority_setting:
            return []
        return [f.strip().lower() for f in priority_setting.split(",") if f.strip()]

    def _load_target_config(self):
        """Load tofler target configuration from targets.yaml."""
        import yaml
        targets_file = Path(__file__).parent.parent / "config" / "targets.yaml"
        targets = yaml.safe_load(targets_file.read_text())["targets"]
        self.target_config = next((t for t in targets if t["name"] == TOFLER_TARGET), None)
        if not self.target_config:
            raise ValueError(f"Target '{TOFLER_TARGET}' not found in targets.yaml")

    def _initialize_csv_states(self, csv_files: list[Path]) -> Dict[str, CSVFileState]:
        """Initialize state tracking for all CSV files with checkpoint resume."""
        states = {}
        
        for csv_path in csv_files:
            # Count total rows
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            is_priority = csv_path.stem.lower() in self.priority_files
            
            # Load or create checkpoint
            checkpoint = checkpoint_manager.initialize_csv(
                csv_path.stem, 
                len(rows), 
                priority=is_priority
            )
            
            # If resuming, slice rows from checkpoint position
            start_row = checkpoint.current_row_index
            remaining_rows = rows[start_row:]
            
            state = CSVFileState(
                path=csv_path,
                checkpoint=checkpoint,
                rows=remaining_rows,
                is_priority=is_priority,
            )
            states[csv_path.stem] = state
            
            if is_priority:
                logger.info(f"Priority CSV: {csv_path.name} ({len(rows)} rows, resuming from {start_row})")
            else:
                logger.info(f"Regular CSV: {csv_path.name} ({len(rows)} rows, resuming from {start_row})")
        
        return states

    def _select_next_csv(self) -> Optional[CSVFileState]:
        """Select the next CSV file to process based on priority and availability."""
        now = time.time()
        
        # Check if we should re-evaluate priority file
        if (self.active_priority_name and 
            self.active_priority_name in self.csv_states):
            active_state = self.csv_states[self.active_priority_name]
            if (active_state.consecutive_failures >= self.fallback_threshold and
                now - self.last_priority_check >= self.recheck_interval):
                
                logger.info(f"Re-checking priority file: {active_state.name}")
                active_state.consecutive_failures = 0
                self.last_priority_check = now
        
        # 1. Try active priority file if it has rows and is below threshold
        if (self.active_priority_name and 
            self.active_priority_name in self.csv_states):
            active_state = self.csv_states[self.active_priority_name]
            if (active_state.has_rows_remaining and
                active_state.consecutive_failures < self.fallback_threshold):
                return active_state
        
        # 2. Try other priority files (in configured order)
        for priority_name in self.priority_files:
            if priority_name in self.csv_states:
                state = self.csv_states[priority_name]
                if (state.has_rows_remaining and 
                    state.name != self.active_priority_name and
                    state.consecutive_failures < self.fallback_threshold):
                    self.active_priority_name = state.name
                    logger.info(f"Switching to priority file: {state.name}")
                    return state
        
        # 3. Fall back to non-priority files (pick one with rows remaining)
        non_priority = [
            s for s in self.csv_states.values()
            if not s.is_priority and s.has_rows_remaining
        ]
        
        if non_priority:
            return non_priority[0]
        
        # 4. Last resort: try priority files even if they exceeded threshold
        for priority_name in self.priority_files:
            if priority_name in self.csv_states:
                state = self.csv_states[priority_name]
                if state.has_rows_remaining:
                    logger.warning(
                        f"Priority file {state.name} exceeded fallback threshold, "
                        f"but no other files available. Continuing anyway."
                    )
                    self.active_priority_name = state.name
                    return state
        
        return None

    async def _process_next_row(self, state: CSVFileState) -> str:
        """Process the next row from the given CSV state."""
        if not state.has_rows_remaining:
            checkpoint_manager.mark_csv_completed(state.name)
            return "exhausted"
        
        row = state.rows[state.checkpoint.current_row_index - state.checkpoint.current_row_index + state.checkpoint.current_row_index]
        # The rows list is already sliced from checkpoint position
        row_idx = state.checkpoint.current_row_index
        
        company_name = row.get("CompanyName", "").strip()
        cin = row.get("CIN", "").strip()
        
        if not company_name or not cin:
            logger.warning(f"{state.name} row {row_idx}: Missing CompanyName or CIN")
            state.checkpoint.skipped_count += 1
            checkpoint_manager.update_csv_progress(state.name, row_idx + 1, skipped_delta=1)
            state.consecutive_successes += 1
            state.consecutive_failures = 0
            return "skipped"
        
        # Construct URL
        url = construct_tofler_url(company_name, cin)
        unique_key = f"tofler:{cin}"
        
        # Use ONE cached pipeline per CSV so the chunk counter persists across
        # rows (a fresh DataPipeline per row would re-scan files every time).
        pipeline = multi_pipeline.get_pipeline(state.name)
        
        # Check if already processed
        if pipeline.is_duplicate(unique_key):
            logger.debug(f"Skipping duplicate: {company_name} ({cin})")
            state.checkpoint.skipped_count += 1
            checkpoint_manager.update_csv_progress(state.name, row_idx + 1, skipped_delta=1)
            state.consecutive_successes += 1
            state.consecutive_failures = 0
            return "skipped"
        
        logger.info(
            f"[{state.name}] Scraping: {company_name} ({cin}) -> {url}",
            extra={"company": company_name, "cin": cin, "url": url, "csv": state.name}
        )
        
        try:
            async with self.semaphore:
                html, proxy_used = await fetch_page_with_retry(
                    url,
                    wait_for_selector=self.target_config.get("company_wait_for_selector"),
                    target_name=TOFLER_TARGET
                )
            
            # Parse company page
            company_fields = self.target_config.get("company_fields", {})
            company_data = parse_company_page(html, company_fields, TOFLER_TARGET)
            
            # Merge with original CSV data (preserve existing fields)
            enriched_record = {**row, **company_data}
            enriched_record["_source"] = TOFLER_TARGET
            enriched_record["_source_url"] = url
            enriched_record["_scraped_at"] = time.time()
            
            # Save to JSONL
            saved = pipeline.save(enriched_record, unique_key)
            
            if not saved:
                logger.warning(f"Record not saved (duplicate?): {company_name} ({cin})")
            
            extracted_fields = [k for k, v in company_data.items() if v is not None]
            logger.info(
                f"[{state.name}] Success: {company_name} ({cin}) - "
                f"extracted {len(extracted_fields)} fields",
                extra={"extracted_fields": extracted_fields, "proxy": proxy_used}
            )
            
            # Update checkpoint
            state.checkpoint.processed_count += 1
            checkpoint_manager.update_csv_progress(state.name, row_idx + 1, processed_delta=1)
            state.consecutive_successes += 1
            state.consecutive_failures = 0
            return "processed"
            
        except FetchFailedError as e:
            logger.warning(
                f"[{state.name}] Fetch failed: {url} - {e}",
                extra={"company": company_name, "cin": cin, "url": url, "error": str(e)}
            )
            # NOTE: do NOT advance the checkpoint here. The main loop's circuit
            # breaker decides whether to skip (isolated bad row) or pause+hold
            # the row (systemic outage), so an outage never burns rows.
            state.consecutive_failures += 1
            state.consecutive_successes = 0
            return "failed"

        except Exception as e:
            logger.error(
                f"[{state.name}] Unexpected error scraping {url}: {type(e).__name__}: {e}",
                extra={"company": company_name, "cin": cin, "url": url}
            )
            state.consecutive_failures += 1
            state.consecutive_successes = 0
            return "failed"

    async def process_all_csv_files(self) -> list[dict]:
        """Process all CSV files with priority-based scheduling and checkpointing."""
        self._load_target_config()
        
        # Set up signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except NotImplementedError:
                pass
        
        await browser_manager.start()
        try:
            csv_files = get_csv_files()
            if not csv_files:
                logger.warning("No CSV files found to process")
                return []
            
            # Initialize states with checkpoint resume
            self.csv_states = self._initialize_csv_states(csv_files)
            
            # Set initial active priority file
            for priority_name in self.priority_files:
                if priority_name in self.csv_states:
                    self.active_priority_name = priority_name
                    break
            
            if not self.active_priority_name and self.csv_states:
                self.active_priority_name = next(iter(self.csv_states.keys()))
            
            logger.info(
                f"Starting priority-based processing. "
                f"Priority files: {self.priority_files}, "
                f"Fallback threshold: {self.fallback_threshold}, "
                f"Concurrency: {self.max_concurrent}"
            )
            
            # Process until all files exhausted or shutdown
            while not self._shutdown:
                # Check test limit
                total_processed = sum(s.checkpoint.processed_count for s in self.csv_states.values())
                if self.test_limit > 0 and total_processed >= self.test_limit:
                    logger.info(f"Test limit reached: {self.test_limit} companies processed")
                    break
                
                state = self._select_next_csv()
                
                if state is None:
                    logger.info("All CSV files exhausted")
                    break
                
                # Process one row
                row_idx = state.checkpoint.current_row_index
                result = await self._process_next_row(state)

                # --- Circuit breaker: decide how to handle a fetch failure ---
                if result == "failed":
                    self._global_consecutive_failures += 1
                    self._current_row_attempts += 1
                    # "isolated" = every failure since the last success is on THIS
                    # row (its neighbours are healthy) -> a genuine bad row (e.g. a
                    # tofler 404), NOT an outage. If earlier rows also failed,
                    # global > attempts -> failures span rows -> outage.
                    isolated = self._current_row_attempts == self._global_consecutive_failures

                    if isolated and self._current_row_attempts < settings.row_max_attempts:
                        # Lone failure, still within the quick-retry budget: short
                        # backoff and retry the SAME row (no advance).
                        await asyncio.sleep(3)
                    elif isolated:
                        # Lone row exhausted its retries while everything else is
                        # healthy -> permanently unavailable (404 etc). Skip it.
                        logger.warning(
                            f"[{state.name}] Skipping row {row_idx} after "
                            f"{self._current_row_attempts} attempts (permanently unavailable)."
                        )
                        state.checkpoint.failed_count += 1
                        checkpoint_manager.update_csv_progress(state.name, row_idx + 1, failed_delta=1)
                        self._current_row_attempts = 0
                        self._current_row_pause_cycles = 0
                    elif (time.time() - self._last_success_time) <= settings.outage_grace_seconds:
                        # Failures span rows, BUT something fetched successfully very
                        # recently -> this is dead DATA (e.g. a CSV of CINs that 404),
                        # not an outage. Skip the row so the crawl keeps moving.
                        logger.warning(
                            f"[{state.name}] Skipping row {row_idx} — failing but fetches "
                            f"are healthy elsewhere (dead record, not an outage)."
                        )
                        state.checkpoint.failed_count += 1
                        checkpoint_manager.update_csv_progress(state.name, row_idx + 1, failed_delta=1)
                        self._current_row_attempts = 0
                        self._current_row_pause_cycles = 0
                    else:
                        # Systemic AND nothing has succeeded anywhere for a while ->
                        # real proxy/network OUTAGE. PAUSE and hold the SAME row; do
                        # NOT advance, so no rows are lost. Resumes the instant fetches
                        # work again (e.g. after you replace the proxies).
                        self._current_row_pause_cycles += 1
                        if (settings.max_pause_cycles > 0
                                and self._current_row_pause_cycles > settings.max_pause_cycles):
                            # Backstop for a genuinely dead run of rows: give up on
                            # this one so the crawl can make progress. Disabled by
                            # default (max_pause_cycles=0 -> hold indefinitely).
                            logger.error(
                                f"[{state.name}] Row {row_idx} still failing after "
                                f"{self._current_row_pause_cycles} pause cycles; skipping to make progress."
                            )
                            state.checkpoint.failed_count += 1
                            checkpoint_manager.update_csv_progress(state.name, row_idx + 1, failed_delta=1)
                            self._current_row_attempts = 0
                            self._current_row_pause_cycles = 0
                        else:
                            logger.error(
                                f"{self._global_consecutive_failures} consecutive fetch failures across rows — "
                                f"likely a proxy/network outage. Pausing {settings.failure_pause_seconds}s and "
                                f"holding row {row_idx} (cycle {self._current_row_pause_cycles}); nothing skipped."
                            )
                            await asyncio.sleep(settings.failure_pause_seconds)
                else:
                    # Success / skipped / exhausted -> reset breaker counters.
                    self._global_consecutive_failures = 0
                    self._current_row_attempts = 0
                    self._current_row_pause_cycles = 0
                    if result == "processed":
                        self._last_success_time = time.time()

                # Log progress periodically
                total_processed = sum(s.checkpoint.processed_count for s in self.csv_states.values())
                if total_processed > 0 and total_processed % 50 == 0:
                    self._log_overall_progress()
                
                # Checkpoint periodically
                if total_processed > 0 and total_processed % settings.checkpoint_interval == 0:
                    checkpoint_manager.save()
                    logger.debug("Checkpoint saved")
                
                # Small delay to prevent tight loop
                await asyncio.sleep(0.01)
            
            # Final checkpoint save
            checkpoint_manager.save()
            logger.info("Final checkpoint saved")
            
            return self._collect_final_stats()
        finally:
            await browser_manager.stop()

    def _signal_shutdown(self):
        """Signal handler for graceful shutdown."""
        logger.info("Shutdown signal received, finishing current row...")
        self._shutdown = True
        self._shutdown_event.set()

    def _log_overall_progress(self):
        """Log overall progress across all CSV files."""
        lines = ["\n" + "=" * 80, "OVERALL PROGRESS", "=" * 80]
        for state in self.csv_states.values():
            status = "PRIORITY" if state.is_priority else "regular"
            if state.name == self.active_priority_name:
                status += " (ACTIVE)"
            lines.append(
                f"  {state.name} [{status}]: "
                f"{state.checkpoint.current_row_index}/{state.checkpoint.total_rows} "
                f"({state.progress_pct:.1f}%) | "
                f"processed={state.checkpoint.processed_count}, "
                f"skipped={state.checkpoint.skipped_count}, "
                f"failed={state.checkpoint.failed_count}, "
                f"consec_fail={state.consecutive_failures}"
            )
        lines.append("=" * 80)
        logger.info("\n".join(lines))

    def _collect_final_stats(self) -> list[dict]:
        """Collect final statistics for all CSV files."""
        stats = []
        for state in self.csv_states.values():
            output_path = get_output_jsonl_path(state.path)
            stats.append({
                "csv_file": str(state.path),
                "output_file": str(output_path),
                "total_rows": state.checkpoint.total_rows,
                "processed": state.checkpoint.processed_count,
                "skipped": state.checkpoint.skipped_count,
                "failed": state.checkpoint.failed_count,
                "is_priority": state.is_priority,
            })
        return stats

    def shutdown(self):
        """Request graceful shutdown."""
        self._shutdown = True
        self._shutdown_event.set()


async def main():
    """Main entry point for CSV processing."""
    processor = ToflerCSVProcessor(max_concurrent=settings.csv_processing_concurrency)
    stats = await processor.process_all_csv_files()
    
    # Print summary
    print("\n" + "=" * 60)
    print("PROCESSING SUMMARY")
    print("=" * 60)
    total_processed = 0
    total_failed = 0
    for s in stats:
        priority_marker = " ★" if s.get("is_priority") else ""
        print(f"\nFile: {s['csv_file']}{priority_marker}")
        print(f"  Output: {s['output_file']}")
        print(f"  Total rows: {s['total_rows']}")
        print(f"  Processed: {s['processed']}")
        print(f"  Skipped: {s['skipped']}")
        print(f"  Failed: {s['failed']}")
        total_processed += s['processed']
        total_failed += s['failed']
    print(f"\n{'=' * 60}")
    print(f"TOTAL: Processed={total_processed}, Failed={total_failed}")
    print("=" * 60)

    # Push final output to S3 (fail-safe; no-op if S3 is disabled).
    try:
        from scraper.s3_uploader import s3_uploader
        s3_uploader.sync_output()
    except Exception as e:
        logger.error(f"Final S3 sync failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())