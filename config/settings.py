from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Redis - broker + result backend for Celery, also used for dedup set
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- Proxy Configuration ---
    proxy_list: str = Field(default="", alias="PROXY_LIST")
    proxy_list_path: str = Field(default="proxyscrape_premium_http_proxies.txt", alias="PROXY_LIST_PATH")
    # Last-resort pool: activated automatically ONLY when every primary proxy is
    # permanently disabled (subscription/trial lapsed or the list went fully
    # stale). Free proxies are unreliable, so this just keeps the crawl limping
    # until you replace the premium list.
    proxy_fallback_list_path: str = Field(default="", alias="PROXY_FALLBACK_LIST_PATH")
    proxy_enabled: bool = Field(default=True, alias="PROXY_ENABLED")
    proxy_health_check: bool = Field(default=True, alias="PROXY_HEALTH_CHECK")
    proxy_fallback_direct: bool = Field(default=True, alias="PROXY_FALLBACK_DIRECT")
    proxy_cooldown_seconds: int = Field(default=300, alias="PROXY_COOLDOWN_SECONDS")
    proxy_max_consecutive_failures: int = Field(default=3, alias="PROXY_MAX_CONSECUTIVE_FAILURES")
    proxy_health_check_interval: int = Field(default=3600, alias="PROXY_HEALTH_CHECK_INTERVAL")
    max_proxy_attempts_per_url: int = Field(default=3, alias="MAX_PROXY_ATTEMPTS_PER_URL")

    # Captcha solver (optional)
    captcha_solver_provider: str = Field(default="", alias="CAPTCHA_SOLVER_PROVIDER")
    captcha_solver_api_key: str = Field(default="", alias="CAPTCHA_SOLVER_API_KEY")

    # Concurrency / pacing
    max_concurrent_browsers: int = Field(default=5, alias="MAX_CONCURRENT_BROWSERS")
    request_delay_min: float = Field(default=1.5, alias="REQUEST_DELAY_MIN")
    request_delay_max: float = Field(default=4.0, alias="REQUEST_DELAY_MAX")
    csv_processing_concurrency: int = Field(default=3, alias="CSV_PROCESSING_CONCURRENCY")

    # Request timeouts (seconds)
    page_load_timeout: int = Field(default=30, alias="PAGE_LOAD_TIMEOUT")
    selector_wait_timeout: int = Field(default=20, alias="SELECTOR_WAIT_TIMEOUT")
    navigation_timeout: int = Field(default=30, alias="NAVIGATION_TIMEOUT")

    # Retry configuration
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    retry_base_delay: float = Field(default=2.0, alias="RETRY_BASE_DELAY")
    retry_max_delay: float = Field(default=60.0, alias="RETRY_MAX_DELAY")

    # Circuit breaker: when fetches fail en masse (e.g. proxies expired /
    # network down), PAUSE and keep retrying the SAME row instead of burning
    # through rows as failures — so replacing proxies resumes with zero gaps.
    #
    # A genuine tofler 404 raises the SAME FetchFailedError as an outage, so we
    # tell them apart by scope: a failure isolated to ONE row (its neighbours
    # succeed) is a real bad row -> skip after row_max_attempts; failures that
    # SPAN consecutive rows are an outage -> pause + hold, never advance.
    row_max_attempts: int = Field(default=4, alias="ROW_MAX_ATTEMPTS")
    failure_pause_seconds: int = Field(default=120, alias="FAILURE_PAUSE_SECONDS")
    # An outage means NOTHING is fetching. If some row succeeded within this many
    # seconds, then failures spanning rows are dead DATA (e.g. a CSV of CINs that
    # 404), not an outage -> skip them instead of holding. Only when nothing has
    # succeeded anywhere for this long do we treat it as a real outage and hold.
    outage_grace_seconds: int = Field(default=180, alias="OUTAGE_GRACE_SECONDS")
    # Backstop: after this many consecutive pause cycles still failing the SAME
    # row during a suspected outage, skip it to guarantee forward progress (so a
    # CSV full of genuinely-dead rows can never permanently wedge the crawl).
    # Default 150 x 120s ~= 5 hours of holding one row before giving up: any
    # realistic proxy outage (you replace the proxies same day) loses ZERO rows;
    # only an unattended multi-hour outage trickles a few skips, all recoverable
    # by re-running the crawl (the Redis dedup set stores only successes, so a
    # re-run retries exactly the gaps). Set to 0 to hold INDEFINITELY (never
    # auto-skip) if you prefer guaranteed zero-gap over guaranteed progress.
    max_pause_cycles: int = Field(default=150, alias="MAX_PAUSE_CYCLES")

    # Priority CSV processing
    priority_csv_files: str = Field(default="delhi", alias="PRIORITY_CSV_FILES")
    priority_fallback_threshold: int = Field(default=5, alias="PRIORITY_FALLBACK_THRESHOLD")
    priority_recheck_interval: int = Field(default=60, alias="PRIORITY_RECHECK_INTERVAL")

    # Output
    output_dir: Path = Field(default=Path("./data"), alias="OUTPUT_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")

    # --- S3 upload (results are pushed here so they survive the box) ---
    # Credentials come from boto3's default chain: an EC2 IAM role in prod,
    # or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars locally.
    s3_enabled: bool = Field(default=False, alias="S3_ENABLED")
    s3_bucket: str = Field(default="", alias="S3_BUCKET")
    s3_prefix: str = Field(default="scraper", alias="S3_PREFIX")
    aws_region: str = Field(default="", alias="AWS_REGION")
    s3_sync_interval_minutes: int = Field(default=5, alias="S3_SYNC_INTERVAL_MINUTES")
    # Split each CSV's output into chunk files of this many records (e.g. Delhi's
    # ~450k results -> part-00001.jsonl, part-00002.jsonl, ... of 10k each).
    chunk_size: int = Field(default=10000, alias="CHUNK_SIZE")
    # Cross-account writes: set to "bucket-owner-full-control" ONLY if the target
    # bucket still has ACLs enabled, so the bucket owner (other account) can read
    # what we write. Leave empty when the bucket uses "Bucket owner enforced".
    s3_acl: str = Field(default="", alias="S3_ACL")

    # Checkpointing
    checkpoint_dir: Path = Field(default=Path("./data/checkpoints"), alias="CHECKPOINT_DIR")
    checkpoint_interval: int = Field(default=100, alias="CHECKPOINT_INTERVAL")

    # Graceful shutdown
    shutdown_timeout: int = Field(default=30, alias="SHUTDOWN_TIMEOUT")

    # Test mode - limit total companies to scrape (0 = unlimited)
    test_limit: int = Field(default=0, alias="TEST_LIMIT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


settings = Settings()
(settings.output_dir / "json").mkdir(parents=True, exist_ok=True)
(settings.output_dir / "csv").mkdir(parents=True, exist_ok=True)
(settings.checkpoint_dir).mkdir(parents=True, exist_ok=True)
Path("logs").mkdir(parents=True, exist_ok=True)