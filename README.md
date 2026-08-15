# Web Scraper System

Heavy-workload, 24x7 web scraper: rotating-proxy stealth browser automation,
config-driven per-site parsing, JSON + CSV output, horizontally scalable via
Celery workers, deployable as containers on any cloud.

## Why Playwright instead of Puppeteer

Puppeteer is JS-only. **Playwright** is its direct Python equivalent (built
by the same original team) — same headless-Chromium automation model,
network interception, proxy-per-context support — so you get Puppeteer-style
control without leaving Python. `playwright-stealth` patches the common
automation fingerprints (`navigator.webdriver`, missing plugins, etc.).

## Architecture

```
                        ┌─────────────────┐
                        │   Celery Beat    │  fires run_full_crawl()
                        │  (scheduler)     │  every hour, 24x7
                        └────────┬─────────┘
                                 │ pushes tasks
                                 ▼
                        ┌─────────────────┐
                        │  Redis (broker + │
                        │  dedup set)      │
                        └────────┬─────────┘
                                 │ tasks pulled
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        ┌──────────┐       ┌──────────┐       ┌──────────┐
        │ Worker 1 │       │ Worker 2 │       │ Worker 3 │   (scale replicas
        │          │       │          │       │          │    for heavier load)
        │ Browser  │       │ Browser  │       │ Browser  │
        │ (Chromium│       │ (Chromium│       │ (Chromium│
        │ + stealth│       │ + stealth│       │ + stealth│
        │ + proxy) │       │ + proxy) │       │ + proxy) │
        └────┬─────┘       └────┬─────┘       └────┬─────┘
             │                  │                  │
             └──────────────────┼──────────────────┘
                                 ▼
                        ┌─────────────────┐
                        │  DataPipeline    │
                        │  JSONL (instant, │
                        │  crash-safe) →   │
                        │  CSV (rolled up) │
                        └─────────────────┘
```

Each worker requests its own proxy + fingerprint per task from
`ProxyManager` / `BrowserManager`, so concurrent scrapes exit through
different IPs and never share cookies.

## Project layout

```
web_scraper_system/
├── config/
│   ├── settings.py       # env-driven settings (pydantic)
│   └── targets.yaml      # one entry per site - selectors, pagination
├── scraper/
│   ├── browser.py         # Playwright browser + stealth context factory
│   ├── proxy_manager.py   # rotation, cooldown, provider-API support
│   ├── fetcher.py         # navigate + retry + block detection
│   ├── parser.py          # generic CSS-selector extractor
│   ├── pipeline.py        # JSONL write + Redis dedup + CSV rollup
│   ├── queue_manager.py   # Celery app + beat schedule (the "cronjob")
│   └── tasks.py           # the actual Celery task definitions
├── workers/celery_worker.py
├── scripts/
│   ├── run_once.py        # local test run, no Celery/Redis needed
│   └── crontab.txt        # bare-VM alternative to Celery Beat
├── data/{json,csv}/       # output lands here
├── Dockerfile
├── docker-compose.yml     # redis + worker(s) + beat + flower dashboard
└── .env.example
```

## Setup

```bash
cp .env.example .env          # fill in your proxy provider + credentials
docker compose up --build -d  # redis, 3 workers, beat scheduler, flower
```

Flower (task monitoring dashboard) is then at `http://<host>:5555`.

To test a single target locally before trusting it to the scheduler:
```bash
pip install -r requirements.txt
playwright install --with-deps chromium
python scripts/run_once.py example_site
```

## Adding a target site

Add an entry to `config/targets.yaml` with the listing container selector
and field selectors — no code changes needed. Pagination's `next_selector`
resolution is left as a marked TODO in `scraper/tasks.py::_scrape_target`
since how "next page" works (query param vs. link vs. infinite scroll)
differs per site.

## How 24x7 works

`queue_manager.py` sets a **Celery Beat** schedule that fires `run_full_crawl`
every hour from inside the container itself — this is the "cronjob," but it
lives in your app instead of the host OS, so it behaves identically whether
you deploy on a bare EC2 box, ECS/Fargate, or Kubernetes. `scripts/crontab.txt`
is included as a plain-system-cron alternative if you'd rather run this on a
single VM without Docker.

`task_acks_late=True` + `max_retries=2` mean a task is only marked done after
it succeeds, and a worker crashing mid-scrape gets its task re-queued instead
of losing that batch.

## Rotating IPs

`ProxyManager` supports either a static comma-separated proxy list you
manage yourself, or a rotating-proxy provider (Bright Data, Oxylabs,
Smartproxy, IPRoyal are the common ones) that hands out a new IP per
request — the latter is what you want for heavy sustained volume, since you
don't have to source/replace IPs yourself. A proxy that trips a block gets
cooled down for 5 minutes rather than removed permanently.

## Bot-detection handling

What's implemented: stealth patches (`playwright-stealth`), rotating
user-agent + viewport + proxy per context, randomized human-like delays and
scroll behaviour, and automatic retry-on-new-IP when a block/interstitial
page is detected. This covers most fingerprint- and reputation-based bot
detection.

What needs a plug-in: sites that gate content behind an **interactive
CAPTCHA challenge** (reCAPTCHA/hCaptcha image puzzles) need a solving
service wired in — 2Captcha, Anti-Captcha, and CapMonster all expose a
similar submit-the-challenge-token / poll-for-solution API. The integration
point is marked with a comment in `scraper/fetcher.py`. `.env.example` has
placeholder config for this.

## Scaling for heavier workload

- Bump `docker-compose.yml`'s `worker` `replicas` and/or `--concurrency`
- Split `config/targets.yaml` across multiple Beat schedules if some sites
  need to run more/less often than others
- Swap the Redis dedup set for a Postgres table if you need to query scraped
  history, not just avoid duplicates

## Cloud deployment options

- **AWS EC2**: install Docker, `docker compose up -d`, done — simplest.
- **AWS ECS/Fargate**: push the built image to ECR, define one task per
  service in `docker-compose.yml` (redis can also be ElastiCache instead).
- **GCP**: Cloud Run for the workers won't work well (needs long-running
  browser processes) — use Compute Engine or GKE instead, with Memorystore
  for Redis.

## A note on compliance

Check the target site's terms of service and `robots.txt`, keep request
rates reasonable, and avoid scraping personal/sensitive data — this varies
by jurisdiction and by site, so it's worth a quick legal check for anything
you're running at real scale.
