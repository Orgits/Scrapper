from celery import Celery
from celery.schedules import crontab

from config.settings import settings

celery_app = Celery("scraper", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    worker_concurrency=settings.max_concurrent_browsers,
    worker_prefetch_multiplier=1,     # browser tasks are heavy - don't over-fetch
    task_acks_late=True,               # re-queue a task if its worker dies mid-scrape
    task_reject_on_worker_lost=True,
)

# Runs the crawl on a schedule from inside the container/pod itself, so
# "24x7" doesn't depend on the host machine having a system cron —
# this keeps working identically on Docker, ECS, Kubernetes, bare EC2, etc.
celery_app.conf.beat_schedule = {
    # Legacy listing-page crawl is DISABLED — we scrape from the CSVs
    # (csv-processor service), so this must not compete for browsers/proxies.
    # "run-full-crawl-hourly": {
    #     "task": "scraper.tasks.run_full_crawl",
    #     "schedule": crontab(minute=0),          # every hour, on the hour
    # },
    "consolidate-csv-daily": {
        "task": "scraper.tasks.consolidate_all",
        "schedule": crontab(hour=23, minute=55),
    },
    # Continuously back up scraped output to S3 while the crawl runs.
    "sync-to-s3": {
        "task": "scraper.tasks.sync_to_s3",
        "schedule": max(60, settings.s3_sync_interval_minutes * 60),
    },
}
