"""
Celery tasks for web scraping.

Includes:
- Legacy listing-based scraping (for targets with listing pages)
- CSV-based company enrichment (for tofler.in)
- Consolidation tasks
"""
import asyncio
import time
from pathlib import Path
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from config.settings import settings
from scraper.queue_manager import celery_app
from scraper.browser import browser_manager
from scraper.fetcher import fetch_page_with_retry
from scraper.parser import parse_listing_page, parse_company_page
from scraper.pipeline import pipeline, multi_pipeline
from scraper.exceptions import FetchFailedError, scrape_result
from scraper.csv_processor import ToflerCSVProcessor
from scraper.checkpoint import checkpoint_manager
from scraper.utils.logger import get_logger


logger = get_logger("tasks")

TARGETS_FILE = Path(__file__).parent.parent / "config" / "targets.yaml"


def _load_targets() -> list[dict]:
    return yaml.safe_load(TARGETS_FILE.read_text())["targets"]


async def _scrape_target(target: dict):
    """Legacy listing-based scraping for targets with pagination."""
    scrape_result.start_time = time.time()
    await browser_manager.start()
    try:
        page_num = 1
        max_pages = target.get("pagination", {}).get("max_pages", 1)
        base_url = target.get("base_url", "https://www.tofler.in")
        pagination_config = target.get("pagination", {})
        pagination_type = pagination_config.get("type", "next_selector")

        # For url_pattern pagination, we'll construct URLs directly
        if pagination_type == "url_pattern":
            url_pattern = pagination_config.get("url_pattern")
            start_urls = target.get("start_urls", [])
            base_listing_url = start_urls[0] if start_urls else url_pattern.format(base_url=base_url, page=1)
        else:
            base_listing_url = target["start_urls"][0]

        while page_num <= max_pages:
            # Construct URL for this page
            if pagination_type == "url_pattern":
                url = url_pattern.format(base_url=base_url, page=page_num)
            elif page_num == 1:
                url = base_listing_url
            else:
                url = None

            if not url:
                break

            logger.info(f"[{target['name']}] Fetching page {page_num}/{max_pages}: {url}")
            
            try:
                html, proxy_used = await fetch_page_with_retry(
                    url, 
                    wait_for_selector=target.get("wait_for_selector"),
                    target_name=target['name']
                )
                
                if proxy_used is None:
                    scrape_result.direct_fallbacks += 1
                else:
                    scrape_result.successful_proxies += 1
                
                records = parse_listing_page(html, target["list_item_selector"], target["fields"])
                scrape_result.pages_scraped += 1

            except FetchFailedError as e:
                logger.error(f"[{target['name']}] Page {page_num} failed after all retries: {e}")
                scrape_result.add_failed_url(
                    url=url,
                    error=f"{type(e.last_error).__name__}: {e.last_error}",
                    attempts=e.attempts,
                    proxies_tried=len(e.failed_proxies)
                )
                scrape_result.proxy_failures += len(e.failed_proxies)
                page_num += 1
                continue
            except Exception as e:
                logger.error(f"[{target['name']}] Unexpected error on page {page_num}: {type(e).__name__}: {e}")
                scrape_result.add_failed_url(
                    url=url,
                    error=f"{type(e).__name__}: {e}",
                    attempts=1,
                    proxies_tried=0
                )
                page_num += 1
                continue

            # Process companies concurrently
            semaphore = asyncio.Semaphore(settings.max_concurrent_browsers)

            async def scrape_company(record: dict, idx: int):
                async with semaphore:
                    company_link = record.get("company_link") or record.get("link")
                    if not company_link:
                        return record

                    company_url = urljoin(base_url, company_link)
                    logger.info(f"[{target['name']}] Scraping company: {company_url}")
                    
                    try:
                        company_html, proxy_used = await fetch_page_with_retry(
                            company_url,
                            wait_for_selector=target.get("company_wait_for_selector"),
                            target_name=target['name']
                        )
                        
                        if proxy_used is None:
                            scrape_result.direct_fallbacks += 1
                        else:
                            scrape_result.successful_proxies += 1
                        
                        company_data = parse_company_page(
                            company_html, 
                            target.get("company_fields", {}), 
                            target["name"]
                        )
                        record.update(company_data)
                        scrape_result.companies_scraped += 1

                    except FetchFailedError as e:
                        logger.warning(f"[{target['name']}] Company failed after retries: {company_url} - {e}")
                        scrape_result.add_failed_url(
                            url=company_url,
                            error=f"{type(e.last_error).__name__}: {e.last_error}",
                            attempts=e.attempts,
                            proxies_tried=len(e.failed_proxies)
                        )
                        scrape_result.proxy_failures += len(e.failed_proxies)
                    except Exception as e:
                        logger.warning(f"[{target['name']}] Company scrape error: {company_url} - {type(e).__name__}: {e}")
                        scrape_result.add_failed_url(
                            url=company_url,
                            error=f"{type(e).__name__}: {e}",
                            attempts=1,
                            proxies_tried=0
                        )

                    unique_key = record.get("cin") or record.get("company_link") or f"{target['name']}:{page_num}:{idx}"
                    record["_source"] = target["name"]
                    pipeline.save(record, unique_key)
                    return record

            # Run company scraping concurrently
            tasks = [scrape_company(record, i) for i, record in enumerate(records)]
            await asyncio.gather(*tasks)

            logger.info(f"[{target['name']}] page {page_num}: {len(records)} records")
            page_num += 1

    finally:
        scrape_result.end_time = time.time()
        logger.info(scrape_result.summary())
        await browser_manager.stop()


async def _process_csv_files():
    """Process CSV files with checkpointing and priority scheduling."""
    processor = ToflerCSVProcessor(max_concurrent=settings.csv_processing_concurrency)
    stats = await processor.process_all_csv_files()
    
    # Log final stats
    total_processed = sum(s['processed'] for s in stats)
    total_failed = sum(s['failed'] for s in stats)
    logger.info(f"CSV processing complete: {total_processed} processed, {total_failed} failed")

    # Push the final output to S3 (fail-safe).
    try:
        from scraper.s3_uploader import s3_uploader
        s3_uploader.sync_output()
    except Exception as e:
        logger.error(f"Final S3 sync failed: {e}")

    return stats


@celery_app.task(name="scraper.tasks.scrape_single_target", bind=True, max_retries=2)
def scrape_single_target(self, target_name: str):
    """Legacy task: scrape a single target with listing pagination."""
    targets = _load_targets()
    target = next((t for t in targets if t["name"] == target_name), None)
    if target is None:
        available = [t["name"] for t in targets]
        raise ValueError(
            f"Unknown target '{target_name}'. Available targets: {available}"
        )
    try:
        asyncio.run(_scrape_target(target))
    except Exception as exc:
        logger.error(f"[{target_name}] scrape failed: {exc}")
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="scraper.tasks.process_company_csv", bind=True, max_retries=3)
def process_company_csv(self):
    """
    Process all CSV files in companydata/ directory with:
    - Priority-based scheduling (configurable via PRIORITY_CSV_FILES)
    - Checkpoint/resume capability
    - Per-company failure tracking in checkpoints
    - Graceful shutdown handling
    """
    try:
        stats = asyncio.run(_process_csv_files())
        return {
            "status": "completed",
            "stats": stats,
        }
    except Exception as exc:
        logger.error(f"CSV processing failed: {exc}")
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="scraper.tasks.run_full_crawl")
def run_full_crawl():
    """Fan out one Celery task per configured target for parallel execution."""
    for target in _load_targets():
        scrape_single_target.delay(target["name"])


@celery_app.task(name="scraper.tasks.consolidate_all")
def consolidate_all():
    """Consolidate all JSONL files to CSV, then push them to S3."""
    results = multi_pipeline.consolidate_all()
    from scraper.s3_uploader import s3_uploader
    s3_uploader.sync_output()
    return {"consolidated": {k: str(v) if v else None for k, v in results.items()}}


@celery_app.task(name="scraper.tasks.sync_to_s3")
def sync_to_s3():
    """Push scraped output (data/json/*.jsonl + data/csv/*.csv) to S3.

    Runs on the beat schedule so results are continuously backed up off the box
    while the scraper keeps running. Fail-safe: never raises."""
    from scraper.s3_uploader import s3_uploader
    if not s3_uploader.enabled:
        return {"status": "disabled"}
    try:
        return {"status": "ok", **s3_uploader.sync_output()}
    except Exception as e:
        logger.error(f"sync_to_s3 failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task(name="scraper.tasks.checkpoint_status")
def checkpoint_status():
    """Get current checkpoint status for monitoring."""
    checkpoint = checkpoint_manager.load()
    return {
        "active_csv": checkpoint.active_csv,
        "completed_files": checkpoint.completed_files,
        "csv_checkpoints": {
            name: {
                "current_row": cp.current_row_index,
                "total_rows": cp.total_rows,
                "progress_pct": cp.progress_pct,
                "processed": cp.processed_count,
                "failed": cp.failed_count,
            }
            for name, cp in checkpoint.csv_checkpoints.items()
        },
    }


@celery_app.task(name="scraper.tasks.proxy_stats")
def proxy_stats():
    """Get proxy pool statistics."""
    from scraper.proxy_manager import proxy_manager
    return proxy_manager.get_stats()


@celery_app.task(name="scraper.tasks.health_check")
def health_check():
    """Health check endpoint for monitoring."""
    from scraper.browser import browser_manager
    from scraper.proxy_manager import proxy_manager
    return {
        "status": "healthy",
        "browser_active_contexts": browser_manager.get_active_context_count(),
        "shutting_down": browser_manager.is_shutting_down(),
        "proxy_stats": proxy_manager.get_stats(),
    }