# Web Scraper System - Production Ready

A production-ready web scraping system for company data enrichment from tofler.in, with priority-based CSV processing, checkpoint/resume capability, and robust error handling.

## Features

- **Priority-based CSV processing** - Process high-priority files first (configurable)
- **Checkpoint/resume** - Automatic resume from last processed row after crashes/restarts
- **Graceful shutdown** - Handles SIGTERM/SIGINT, finishes current row before exiting
- **Structured JSON logging** - Machine-parseable logs with context
- **Proxy management** - Automatic rotation, health checks, failure categorization
- **Atomic writes** - Crash-safe JSONL/CSV output with temp-file rename
- **Docker production ready** - Multi-stage build, health checks, persistent volumes
- **Celery integration** - Distributed task queue with Redis backend

## Quick Start

### Local Development

```bash
# 1. Clone and enter directory
cd web_scraper_system

# 2. Copy environment template
cp .env.example .env

# 3. Edit .env with your settings (especially proxy list)
vim .env

# 4. Build and run with Docker Compose
docker-compose -f docker-compose.yml -f docker-compose.override.yml up --build
```

### Production Deployment

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Configure .env for production:
#    - REDIS_URL=redis://localhost:6379/0
#    - PROXY_LIST_PATH=proxyscrape_premium_http_proxies.txt
#    - PRIORITY_CSV_FILES=delhi,ROC-ANDHRA
#    - LOG_LEVEL=INFO
#    - LOG_FORMAT=json

# 3. Build and start all services
docker-compose up -d --build

# 4. Verify services are healthy
docker-compose ps
# All services should show "healthy" or "running"

# 5. Monitor via Flower UI
open http://localhost:5555
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| **Redis** | | |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| **Proxy** | | |
| `PROXY_LIST_PATH` | `proxyscrape_premium_http_proxies.txt` | Proxy file path |
| `PROXY_LIST` | (empty) | Comma-separated proxy list (alternative to file) |
| `PROXY_ENABLED` | `true` | Enable/disable proxies |
| `PROXY_HEALTH_CHECK` | `true` | Run periodic proxy health checks |
| `PROXY_HEALTH_CHECK_INTERVAL` | `3600` | Health check interval (seconds) |
| `PROXY_FALLBACK_DIRECT` | `true` | Allow direct connection when proxies fail |
| `PROXY_COOLDOWN_SECONDS` | `300` | Cooldown after proxy failure |
| `PROXY_MAX_CONSECUTIVE_FAILURES` | `3` | Max failures before permanent disable |
| `MAX_PROXY_ATTEMPTS_PER_URL` | `3` | Proxy attempts per URL |
| **Concurrency** | | |
| `MAX_CONCURRENT_BROWSERS` | `5` | Max browser contexts per worker |
| `CSV_PROCESSING_CONCURRENCY` | `3` | Parallel company scrapes per CSV |
| `REQUEST_DELAY_MIN` | `1.5` | Min delay between requests (seconds) |
| `REQUEST_DELAY_MAX` | `4.0` | Max delay between requests (seconds) |
| **Timeouts** | | |
| `PAGE_LOAD_TIMEOUT` | `30` | Page load timeout (seconds) |
| `SELECTOR_WAIT_TIMEOUT` | `20` | Selector wait timeout (seconds) |
| `NAVIGATION_TIMEOUT` | `30` | Navigation timeout (seconds) |
| **Retry** | | |
| `MAX_RETRIES` | `3` | Max retry attempts |
| `RETRY_BASE_DELAY` | `2.0` | Base delay for exponential backoff |
| `RETRY_MAX_DELAY` | `60.0` | Max retry delay |
| **Priority CSV** | | |
| `PRIORITY_CSV_FILES` | `delhi` | Comma-separated priority CSV files |
| `PRIORITY_FALLBACK_THRESHOLD` | `5` | Consecutive failures before fallback |
| `PRIORITY_RECHECK_INTERVAL` | `60` | Re-check priority file interval (seconds) |
| **Output & Logging** | | |
| `OUTPUT_DIR` | `./data` | Output directory |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `LOG_FORMAT` | `json` | Log format (json/text) |
| **Checkpointing** | | |
| `CHECKPOINT_DIR` | `./data/checkpoints` | Checkpoint directory |
| `CHECKPOINT_INTERVAL` | `100` | Save checkpoint every N records |
| **Shutdown** | | |
| `SHUTDOWN_TIMEOUT` | `30` | Max seconds to wait for in-flight ops |

## Project Structure

```
web_scraper_system/
├── companydata/              # Input CSV files (read-only in container)
│   ├── delhi.csv
│   ├── ROC-assam.csv
│   └── ...
├── data/                     # Persistent output (mounted volume)
│   ├── json/                 # JSONL output per CSV
│   ├── csv/                  # Consolidated CSV output
│   └── checkpoints/          # Checkpoint files for resume
├── logs/                     # Log files (mounted volume)
├── scraper/
│   ├── browser.py           # Browser management with graceful shutdown
│   ├── checkpoint.py        # Checkpoint/resume system
│   ├── csv_processor.py     # Priority CSV processor
│   ├── fetcher.py           # Page fetching with proxy retry
│   ├── parser.py            # HTML parsing (tofler.in extractors)
│   ├── pipeline.py          # Data pipeline with atomic writes
│   ├── proxy_manager.py     # Proxy pool with health checks
│   ├── tasks.py             # Celery tasks
│   ├── queue_manager.py     # Celery configuration
│   ├── exceptions.py        # Custom exceptions
│   └── utils/
│       ├── logger.py        # Structured JSON logging
│       └── user_agents.py   # Random user agent rotation
├── config/
│   ├── settings.py          # Pydantic settings from env
│   └── targets.yaml         # Target site configuration
├── scripts/
│   ├── process_companydata.py  # Direct CSV processor entry
│   └── run_once.py              # Legacy single-target runner
├── workers/
│   └── celery_worker.py     # Celery app entry point
├── Dockerfile               # Multi-stage production build
├── docker-compose.yml       # Production services
├── docker-compose.override.yml  # Development overrides
├── start.sh                 # Production startup script
├── requirements.txt         # Python dependencies
└── .env.example             # Environment template
```

## Running the Scraper

### Using Start Script (Recommended)

```bash
# Make executable
chmod +x start.sh

# Start all services (worker + beat + flower)
./start.sh all

# Run CSV processor once (direct, no Celery)
./start.sh csv-once

# Run CSV processor continuously
./start.sh csv

# Check status
./start.sh status             # Checkpoint progress
./start.sh health             # Health check

# View logs
./start.sh logs               # All services
./start.sh logs worker        # Worker only

# Stop/Restart
./start.sh stop
./start.sh restart
```

### Using Docker Compose Directly

```bash
# Start all services in background
docker-compose up -d

# View logs
docker-compose logs -f worker

# Run one-off CSV processing
docker-compose run --rm worker python scripts/process_companydata.py

# Run Celery task manually
docker-compose run --rm worker python -c "
from scraper.tasks import process_company_csv
result = process_company_csv.delay()
print(f'Task ID: {result.id}')
"

# Check task status
docker-compose run --rm worker python -c "
from scraper.tasks import process_company_csv
result = process_company_csv.AsyncResult('task-id-here')
print(result.status, result.result)
"
```

### Using Celery Commands

```bash
# Start worker
celery -A workers.celery_worker worker --loglevel=info --concurrency=5

# Start beat scheduler
celery -A workers.celery_worker beat --loglevel=info

# Start Flower monitoring
celery -A workers.celery_worker flower --port=5555

# Trigger full crawl
celery -A workers.celery_worker call scraper.tasks.run_full_crawl

# Process CSVs
celery -A workers.celery_worker call scraper.tasks.process_company_csv
```

## Monitoring & Operations

### Flower UI (Port 5555)

- Task status and history
- Worker status and stats
- Task retry/termination
- Real-time monitoring

### Key Metrics to Monitor

```bash
# Checkpoint status
./start.sh status

# Proxy statistics
docker-compose run --rm worker python -c "
from scraper.tasks import proxy_stats
import json
print(json.dumps(proxy_stats.delay().get(timeout=30), indent=2))
"

# Health check
./start.sh health
```

### Logs

Structured JSON logs (when `LOG_FORMAT=json`):
```json
{
  "timestamp": "2026-08-18T10:30:00.123456+05:30",
  "level": "INFO",
  "logger": "scraper.csv_processor",
  "module": "csv_processor",
  "function": "_process_next_row",
  "line": 280,
  "message": "[delhi] Scraping: COMPANY NAME (U12345DL2020PTC123456) -> https://...",
  "company": "COMPANY NAME",
  "cin": "U12345DL2020PTC123456",
  "url": "https://www.tofler.in/...",
  "csv": "delhi"
}
```

### Data Persistence

All data persists across container restarts via Docker volumes:

| Data | Location | Description |
|------|----------|-------------|
| Scraped JSONL | `./data/json/{csv_name}.jsonl` | One file per input CSV |
| Consolidated CSV | `./data/csv/{csv_name}.csv` | Merged output |
| Checkpoints | `./data/checkpoints/global_checkpoint.json` | Resume position per CSV |
| Logs | `./logs/scraper_YYYY-MM-DD.log` | Daily rotation, 14-day retention |

## Resume After Crash/Restart

The system automatically resumes from the last successful checkpoint:

1. **On startup**, reads checkpoint file
2. **For each CSV**, continues from `current_row_index`
3. **Skips already-processed** companies via deduplication (Redis or local file)

```bash
# Manual checkpoint reset (fresh start)
docker-compose run --rm worker python -c "
from scraper.checkpoint import checkpoint_manager
checkpoint_manager.clear_completed()
print('Checkpoint cleared')
"

# View current checkpoint
./start.sh status
```

## Graceful Shutdown

The scraper handles SIGTERM/SIGINT gracefully:

1. Receives shutdown signal
2. Stops accepting new rows
3. Finishes current in-flight scrape (up to `SHUTDOWN_TIMEOUT` seconds)
4. Saves final checkpoint
5. Closes browser contexts
6. Exits cleanly

```bash
# Graceful stop
./start.sh stop

# Or via Docker
docker-compose stop worker
```

## Proxy Management

Proxies are loaded from:
1. `PROXY_LIST_PATH` file (one per line: `user:pass@host:port`)
2. `PROXY_LIST` environment variable (comma-separated)

Features:
- Automatic rotation with shuffling
- Per-proxy failure categorization (connectivity, timeout, HTTP error, anti-bot)
- Cooldown period after failures
- Periodic health checks against httpbin.org
- Permanent disable after `PROXY_MAX_CONSECUTIVE_FAILURES`
- Direct connection fallback when all proxies cooling down

## Adding New Target Sites

1. Add entry to `config/targets.yaml`:
```yaml
targets:
  - name: mysite
    base_url: "https://example.com"
    start_urls:
      - "https://example.com/list"
    list_item_selector: ".item"
    fields:
      name: "h2 a"
      link: "h2 a::attr(href)"
    company_wait_for_selector: "#detail"
    company_fields:
      name: "COMPANY_NAME"
      # ... use special keys or CSS selectors
```

2. Add extractors in `scraper/parser.py` if needed (for special keys)

3. Run with: `celery -A workers.celery_worker call scraper.tasks.scrape_single_target '["mysite"]'`

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Redis connection failed | Check `REDIS_URL` in .env, ensure Redis is healthy |
| No proxies working | Check proxy file format, try `PROXY_FALLBACK_DIRECT=true` |
| Selector timeout | Increase `SELECTOR_WAIT_TIMEOUT`, check target site changes |
| Browser crashes | Reduce `MAX_CONCURRENT_BROWSERS`, check memory limits |
| CSV not found | Ensure `companydata/` is mounted, check file permissions |

### Debug Mode

```bash
# Enable debug logging
LOG_LEVEL=DEBUG ./start.sh csv-once

# Run with single concurrency
CSV_PROCESSING_CONCURRENCY=1 MAX_CONCURRENT_BROWSERS=1 ./start.sh csv-once
```

## Performance Tuning

| Setting | Recommendation |
|---------|----------------|
| `MAX_CONCURRENT_BROWSERS` | 3-5 per CPU core (memory intensive) |
| `CSV_PROCESSING_CONCURRENCY` | 2-4 (depends on proxy pool size) |
| `REQUEST_DELAY_MIN/MAX` | 1-5s (respect target site rate limits) |
| `CHECKPOINT_INTERVAL` | 50-200 (balance between I/O and resume granularity) |

## Security Notes

- Never commit `.env` or proxy files with credentials
- Run containers as non-root user (configured in Dockerfile)
- Use Docker secrets for sensitive values in production
- Rotate proxy credentials regularly
- Monitor for unusual traffic patterns

## License

Internal use only.