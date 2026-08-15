"""
Entry point for a Celery worker process.

Run directly:
    celery -A workers.celery_worker worker --loglevel=info --concurrency=5

Run the scheduler (fires run_full_crawl / consolidate_all on schedule):
    celery -A workers.celery_worker beat --loglevel=info

Both commands are also wired into docker-compose.yml as separate services.
"""
from scraper.queue_manager import celery_app
from scraper import tasks  # noqa: F401  (import registers tasks with celery_app)

app = celery_app
