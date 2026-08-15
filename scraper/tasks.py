import asyncio
from pathlib import Path
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from scraper.queue_manager import celery_app
from scraper.browser import browser_manager
from scraper.fetcher import fetch_page
from scraper.parser import parse_listing_page, parse_company_page
from scraper.pipeline import pipeline
from scraper.utils.logger import logger

TARGETS_FILE = Path(__file__).parent.parent / "config" / "targets.yaml"


def _load_targets() -> list[dict]:
    return yaml.safe_load(TARGETS_FILE.read_text())["targets"]


async def _scrape_target(target: dict):
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

            html = await fetch_page(url, wait_for_selector=target.get("wait_for_selector"))
            records = parse_listing_page(html, target["list_item_selector"], target["fields"])

            # Process companies concurrently (max 6 at a time for 3x speed)
            semaphore = asyncio.Semaphore(6)

            async def scrape_company(record: dict, idx: int):
                async with semaphore:
                    company_link = record.get("company_link") or record.get("link")
                    if not company_link:
                        return record

                    company_url = urljoin(base_url, company_link)
                    logger.info(f"[{target['name']}] Scraping company: {company_url}")
                    try:
                        company_html = await fetch_page(company_url, wait_for_selector=target.get("company_wait_for_selector"))
                        company_data = parse_company_page(company_html, target.get("company_fields", {}))
                        record.update(company_data)
                    except Exception as e:
                        logger.warning(f"[{target['name']}] Failed to scrape company {company_url}: {e}")

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
        await browser_manager.stop()


@celery_app.task(name="scraper.tasks.scrape_single_target", bind=True, max_retries=2)
def scrape_single_target(self, target_name: str):
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


@celery_app.task(name="scraper.tasks.run_full_crawl")
def run_full_crawl():
    """Fans out one Celery task per configured target so they run in parallel
    across whatever workers are online — this is the horizontal-scale knob
    for heavy workloads: add more worker replicas, not more code."""
    for target in _load_targets():
        scrape_single_target.delay(target["name"])


@celery_app.task(name="scraper.tasks.consolidate_all")
def consolidate_all():
    pipeline.consolidate_to_csv()
