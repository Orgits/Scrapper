# SYSTEM ARCHITECTURE

## Documentation Metadata

| Field | Value |
|-------|-------|
| **Documentation Purpose** | Comprehensive technical architecture documentation for the Web Scraper System |
| **Project Name** | Web Scraper System |
| **Documentation Date** | 2026-08-15 |
| **Current Implementation Status** | Production-ready, actively scraping Tofler.in with 4-page pagination, 6 concurrent company scrapes per page, JSONL + CSV output |
| **Inspection Scope** | Complete source code, configuration, dependencies, Docker setup, Celery orchestration, browser automation, proxy management, data pipeline |
| **Important Limitations** | • Only one target (Tofler) configured in `targets.yaml`<br>• CAPTCHA solving not integrated (placeholder only)<br>• Single Redis instance (no HA)<br>• No metrics/monitoring beyond Flower dashboard<br>• No automated testing suite<br>• Secrets must not be committed - `.env` is gitignored |

---

## 1. PROJECT OVERVIEW

### 1.1 What the System Does

The Web Scraper System is a **horizontally scalable, 24×7 web scraping platform** built for heavy workloads. It uses **Playwright-driven headless Chromium** with stealth patches, rotating proxies per browser context, and config-driven CSS selector extraction to scrape target websites. The system is designed to run continuously via Celery Beat scheduling, with automatic retry, deduplication, and crash-safe incremental JSONL output.

### 1.2 Main Purpose

- **Primary**: Scrape company directory data from Tofler.in (Indian corporate registry)
- **Secondary**: Provide a reusable framework for adding new target sites via YAML configuration only
- **Operational**: Enable horizontal scaling via Celery worker replicas without code changes

### 1.3 Primary Use Case

Scrape all companies listed on Tofler's Delhi company directory (120 companies across 4 pages, 30 per page), extracting:
- Listing fields: company name, link, incorporation year, industry, status
- Detail fields: CIN, registered address, authorized/paid-up capital, email, directors table

### 1.4 Overall Architecture

```mermaid
flowchart TB
    subgraph "Scheduling Layer"
        BEAT[Celery Beat<br/>Hourly cron]
    end

    subgraph "Message Layer"
        REDIS[(Redis<br/>Broker + Result Backend<br/>+ Dedup Set)]
    end

    subgraph "Worker Layer"
        W1[Worker 1<br/>Browser + Proxy]
        W2[Worker 2<br/>Browser + Proxy]
        W3[Worker 3<br/>Browser + Proxy]
    end

    subgraph "Data Layer"
        JSONL[(JSONL Files<br/>Incremental, crash-safe)]
        CSV[(CSV Files<br/>Daily rollup)]
    end

    BEAT -->|run_full_crawl| REDIS
    REDIS -->|scrape_single_target| W1
    REDIS -->|scrape_single_target| W2
    REDIS -->|scrape_single_target| W3
    W1 -->|pipeline.save| JSONL
    W2 -->|pipeline.save| JSONL
    W3 -->|pipeline.save| JSONL
    JSONL -.->|consolidate_all| CSV
```

### 1.5 Major Components

| Component | Module | Responsibility |
|-----------|--------|----------------|
| **Configuration** | `config/settings.py`, `config/targets.yaml` | Pydantic settings + YAML target definitions |
| **Browser Automation** | `scraper/browser.py` | Playwright browser lifecycle, stealth context factory |
| **Proxy Management** | `scraper/proxy_manager.py` | Static pool from `.env`, health checks, cooldown, rotation |
| **Page Fetching** | `scraper/fetcher.py` | Navigation, retry, block detection, proxy error handling |
| **HTML Parsing** | `scraper/parser.py` | Generic CSS selector extraction + special field handlers |
| **Data Pipeline** | `scraper/pipeline.py` | JSONL append, Redis/local dedup, CSV consolidation |
| **Task Orchestration** | `scraper/tasks.py`, `scraper/queue_manager.py` | Celery task definitions, Beat schedule, concurrency config |
| **Worker Entry** | `workers/celery_worker.py` | Celery app import + task registration |
| **Utilities** | `scraper/utils/` | User agents, logging |

### 1.6 Technologies Used

| Category | Technology | Version |
|----------|------------|---------|
| **Browser Automation** | Playwright (Chromium) | 1.47.0 |
| **Stealth** | playwright-stealth | 1.0.6 |
| **Task Queue** | Celery + Redis | 5.4.0 / 7-alpine |
| **HTML Parsing** | BeautifulSoup4 + lxml | 4.12.3 / 5.3.0 |
| **Configuration** | Pydantic Settings + YAML | 2.9.2 / 6.0.2 |
| **Retry Logic** | tenacity | 9.0.0 |
| **Logging** | loguru | 0.7.2 |
| **User Agents** | fake-useragent | 1.5.1 |
| **Monitoring** | Flower | 2.0.1 |
| **Containerization** | Docker + Docker Compose | 3.9 |

### 1.7 External Services

| Service | Purpose | Configuration |
|---------|---------|---------------|
| **Redis** | Celery broker, result backend, dedup set | `REDIS_URL` in `.env` |
| **Proxy Provider** | Rotating proxy IPs (optional) | `PROXY_PROVIDER_API_URL`, `PROXY_PROVIDER_API_KEY` |
| **CAPTCHA Solver** | Interactive CAPTCHA solving (placeholder) | `CAPTCHA_SOLVER_PROVIDER`, `CAPTCHA_SOLVER_API_KEY` |
| **Target Site** | Tofler.in (https://www.tofler.in) | Configured in `targets.yaml` |

### 1.8 High-Level Workflow

```mermaid
sequenceDiagram
    participant Beat as Celery Beat
    participant Redis as Redis Broker
    participant Worker as Worker Process
    participant Browser as BrowserManager
    participant Proxy as ProxyManager
    participant Fetcher as fetch_page()
    participant Parser as Parser
    participant Pipeline as DataPipeline
    participant JSONL as JSONL File

    Beat->>Redis: run_full_crawl() task
    Redis->>Worker: scrape_single_target("tofler")
    Worker->>Browser: start()
    loop For each page (1..max_pages)
        Worker->>Fetcher: fetch_page(listing_url)
        Fetcher->>Proxy: get_proxy() → health check
        Proxy-->>Fetcher: working proxy
        Fetcher->>Browser: new_stealth_context(proxy)
        Browser-->>Fetcher: context + page
        Fetcher->>Target: page.goto(listing_url)
        Target-->>Fetcher: HTML
        Fetcher-->>Worker: listing HTML
        Worker->>Parser: parse_listing_page()
        Parser-->>Worker: 30 company records
        par Concurrent company scraping (semaphore=6)
            Worker->>Fetcher: fetch_page(company_url)
            Fetcher->>Proxy: get_proxy() → health check
            Proxy-->>Fetcher: working proxy
            Fetcher->>Browser: new_stealth_context(proxy)
            Browser-->>Fetcher: context + page
            Fetcher->>Target: page.goto(company_url)
            Target-->>Fetcher: HTML
            Fetcher-->>Worker: company HTML
            Worker->>Parser: parse_company_page()
            Parser-->>Worker: detail data
            Worker->>Pipeline: save(record, unique_key)
            Pipeline->>Redis: SADD seen_urls
            Pipeline->>JSONL: append JSON line
        end
    end
    Worker->>Browser: stop()
    Redis->>Worker: consolidate_all (daily)
    Worker->>Pipeline: consolidate_to_csv()
    Pipeline->>JSONL: read all lines
    Pipeline->>CSV: write CSV
```

---

## 2. COMPLETE PROJECT STRUCTURE

```text
web_scraper_system/
├── config/
│   ├── __init__.py
│   ├── settings.py           # Pydantic Settings (env-driven config)
│   ├── settings.pyc          # cached
│   └── targets.yaml          # Target site definitions (selectors, pagination)
├── data/
│   ├── json/
│   │   ├── 2026-08-14.jsonl  # Daily JSONL output (incremental)
│   │   ├── 2026-08-15.jsonl
│   │   └── .seen_keys        # Local dedup fallback (if Redis unavailable)
│   └── csv/
│       └── (daily CSV rollups from consolidate_to_csv)
├── docker-compose.yml        # Multi-service orchestration
├── Dockerfile                # Playwright Chromium base image
├── logs/
│   ├── scraper_2026-08-14.log
│   └── scraper_2026-08-15.log
├── proxyscrape_premium_http_proxies.txt  # Backup proxy list (unused)
├── README.md                 # Project documentation
├── requirements.txt          # Python dependencies
├── scripts/
│   ├── run_once.py           # Standalone test runner (no Celery/Redis)
│   └── crontab.txt           # System cron alternative to Celery Beat
├── scraper/
│   ├── __init__.py
│   ├── browser.py            # BrowserManager - Playwright lifecycle + stealth contexts
│   ├── fetcher.py            # fetch_page() - navigation, retry, block detection
│   ├── parser.py             # parse_listing_page(), parse_company_page()
│   ├── pipeline.py           # DataPipeline - JSONL + Redis dedup + CSV
│   ├── proxy_manager.py      # ProxyManager - static pool, health check, cooldown
│   ├── queue_manager.py      # Celery app + Beat schedule
│   ├── tasks.py              # Celery task definitions (_scrape_target, scrape_single_target, run_full_crawl, consolidate_all)
│   └── utils/
│       ├── __init__.py
│       ├── logger.py         # loguru setup (stdout + daily rotating file)
│       └── user_agents.py    # fake-useragent wrapper
├── workers/
│   ├── __init__.py
│   └── celery_worker.py      # Celery worker entry point (imports tasks)
└── .env / .env.example       # Environment variables (secrets + config)
```

---

## 3. CONFIGURATION SYSTEM

### 3.1 Settings (`config/settings.py`)

```python
class Settings(BaseSettings):
    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Proxies (Option A: static list, Option B: provider API)
    proxy_list: str = ""
    proxy_provider_api_url: str = ""
    proxy_provider_api_key: str = ""
    proxy_username: str = ""
    proxy_password: str = ""

    # CAPTCHA (placeholder)
    captcha_solver_provider: str = ""
    captcha_solver_api_key: str = ""

    # Concurrency / pacing
    max_concurrent_browsers: int = 10
    request_delay_min: float = 0.3
    request_delay_max: float = 0.8

    # Output
    output_dir: Path = Path("./data")
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
```

**Key behaviors:**
- Loads from `.env` at module import time
- Creates output directories (`data/json`, `data/csv`, `logs`) on import
- `max_concurrent_browsers` maps to Celery `worker_concurrency`
- `request_delay_min/max` control randomized delay between requests

### 3.2 Target Definitions (`config/targets.yaml`)

```yaml
targets:
  - name: tofler
    base_url: "https://www.tofler.in"
    start_urls:
      - "https://www.tofler.in/companylist/delhi/"
    list_item_selector: "table tr:has(td)"
    fields:
      company_name: "td:nth-child(1) a"
      company_link: "td:nth-child(1) a::attr(href)"
      incorporation_year: "td:nth-child(2)"
      industry: "td:nth-child(3)"
      status: "td:nth-child(4)"
    company_wait_for_selector: "#registered-details-module"
    company_fields:
      company_name: "#overview-module h1"
      cin: "#registered-details-module .registered_box_wrapper .flex-col.gap-2:nth-child(1) span.text-base.text-dark.font-semibold"
      status: "#overview-module .circular_point + .success"
      registered_address: "REGISTERED_ADDRESS"
      authorized_capital: "...nth-child(5)..."
      paid_up_capital: "...nth-child(4)..."
      industry: ".company_tag_item"
      email: "...nth-child(3)..."
      directors: "DIRECTORS_TABLE"
    pagination:
      type: "url_pattern"
      url_pattern: "{base_url}/companylist/delhi/pg-{page}"
      max_pages: 4
    wait_for_selector: "table tr:has(td)"
    rate_limit_seconds: 3
```

**Selector syntax:**
- Text content: `"css selector"`
- Attribute: `"css selector::attr(attribute_name)"`
- Special keys: `"DIRECTORS_TABLE"`, `"REGISTERED_ADDRESS"` (handled in `parser.py`)

---

## 4. CORE MODULES

### 4.1 Browser Manager (`scraper/browser.py`)

**Class**: `BrowserManager`

```python
class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def start():
        # Launches single Chromium process per worker
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        )

    async def stop():
        # Graceful shutdown
        await self._browser.close()
        await self._playwright.stop()

    async def new_stealth_context() -> Tuple[BrowserContext, Optional[str]]:
        # 1. Get proxy from ProxyManager
        proxy_url = proxy_manager.get_proxy()
        # 2. Convert to Playwright format
        proxy_cfg = proxy_manager.to_playwright_format(proxy_url) if proxy_url else None
        # 3. Create isolated context with unique fingerprint
        context = await self._browser.new_context(
            proxy=proxy_cfg,
            user_agent=random_user_agent(),      # random UA
            viewport=random.choice(VIEWPORTS),   # random viewport
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        page = await context.new_page()
        await stealth_async(page)  # playwright-stealth patches
        return context, proxy_url
```

**Key Design Points:**
- **One browser process per worker** (launched on `start()`, closed on `stop()`)
- **New context per request** → unique IP, UA, viewport, cookies, fingerprint
- **Stealth patches**: `navigator.webdriver`, plugin/language fingerprints, etc.
- **Thread-safe**: Single instance `browser_manager` shared across tasks

### 4.2 Proxy Manager (`scraper/proxy_manager.py`)

**Class**: `ProxyManager`

```python
class ProxyManager:
    def __init__(self, cooldown_seconds=300, health_check_timeout=10):
        # Load static proxies from settings.proxy_list (comma-separated)
        self._static_proxies = [p.strip() for p in settings.proxy_list.split(",") if p.strip()]
        self._pool = list(self._static_proxies)
        self._cooldown = {}           # proxy -> expiry timestamp
        self._failed_proxies = set()  # permanently failed (health check)
        self._verified_proxies = set() # passed health check
        self._lock = threading.Lock()
```

**Core Methods:**

| Method | Behavior |
|--------|----------|
| `get_proxy()` | Returns random healthy proxy; health-checks each candidate via `http://httpbin.org/ip`; caches verified proxies; shuffles for rotation |
| `mark_bad(proxy)` | Adds to cooldown (5 min), failed set, removes from verified |
| `_health_check(proxy)` | HTTP GET to `http://httpbin.org/ip` via proxy; validates JSON response has `origin` field; 10s timeout |
| `to_playwright_format(proxy_url)` | Converts `http://user:pass@host:port` → `{"server": "http://host:port", "username": "user", "password": "pass"}` |

**Flow:**
```mermaid
flowchart TD
    GET[get_proxy()] --> LOCK{Lock}
    LOCK --> USABLE[Filter usable: not in cooldown, not failed]
    USABLE --> EMPTY{Any usable?}
    EMPTY -->|No| ERROR[Log error, return None]
    EMPTY -->|Yes| SHUFFLE[Shuffle list]
    SHUFFLE --> HEALTH{Health check passed?}
    HEALTH -->|Yes| RETURN[Return proxy]
    HEALTH -->|No| MARK_FAILED[Add to failed, try next]
    MARK_FAILED --> HEALTH
```

### 4.3 Fetcher (`scraper/fetcher.py`)

**Function**: `fetch_page(url, wait_for_selector) -> str`

```python
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((PlaywrightTimeout, FetchBlocked, ProxyConnectionError))
)
async def fetch_page(url, wait_for_selector=None):
    context, proxy_used = await browser_manager.new_stealth_context()
    try:
        page = context.pages[0] or await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        
        if wait_for_selector:
            await page.wait_for_selector(wait_for_selector, timeout=10000)
        
        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))
        
        html = await page.content()
        
        # Block detection
        if any(marker in html.lower() for marker in BLOCK_MARKERS):
            if proxy_used: proxy_manager.mark_bad(proxy_used)
            raise FetchBlocked(f"Block/interstitial detected: {url}")
        
        return html
    
    except PlaywrightTimeout:
        if proxy_used: proxy_manager.mark_bad(proxy_used)
        raise
    except PlaywrightError as e:
        if _is_proxy_connection_error(e):  # ERR_TUNNEL_CONNECTION_FAILED, etc.
            if proxy_used: proxy_manager.mark_bad(proxy_used)
            raise ProxyConnectionError(f"Proxy connection failed: {e}")
        raise
    finally:
        await context.close()
```

**Exception Hierarchy:**
- `FetchBlocked` → Block/interstitial page detected (retried with new proxy)
- `ProxyConnectionError` → Tunnel/connection failure (retried with new proxy)
- `PlaywrightTimeout` → Navigation/selector timeout (retried with new proxy)

**Retry Policy**: 5 attempts, exponential backoff (1-10s), only on specific exceptions

### 4.4 Parser (`scraper/parser.py`)

**Functions:**

| Function | Purpose |
|----------|---------|
| `parse_listing_page(html, list_item_selector, fields)` | Extracts multiple records from listing page using CSS selectors |
| `parse_company_page(html, fields)` | Extracts single record from detail page, handles special keys |
| `_extract_directors(soup)` | Parses `#people-module table` → list of director dicts |
| `_extract_registered_address(soup)` | Regex extracts address from `#overview-module .moreContent` |

**Selector Processing:**
```python
# In parse_listing_page / parse_company_page
if "::attr(" in selector:
    sel, attr = selector.split("::attr(")
    attr = attr.rstrip(")")
    el = card.select_one(sel.strip())
    record[key] = el.get(attr) if el else None
else:
    el = card.select_one(selector.strip())
    record[key] = el.get_text(strip=True) if el else None

# Special keys in company_fields
if selector == "DIRECTORS_TABLE":
    record[key] = _extract_directors(soup)
elif selector == "REGISTERED_ADDRESS":
    record[key] = _extract_registered_address(soup)
```

### 4.5 Data Pipeline (`scraper/pipeline.py`)

**Class**: `DataPipeline`

```python
class DataPipeline:
    def __init__(self):
        self.json_path = Path(settings.output_dir) / "json" / f"{date.today()}.jsonl"
        self._local_seen = set()
        if not REDIS_AVAILABLE:
            self._load_local_seen()  # from .seen_keys file

    def is_duplicate(unique_key) -> bool:
        if REDIS_AVAILABLE:
            return _redis.sismember("scraper:seen_urls", unique_key)
        return unique_key in self._local_seen

    def save(record, unique_key):
        if is_duplicate(unique_key): return
        if REDIS_AVAILABLE:
            _redis.sadd("scraper:seen_urls", unique_key)
        else:
            self._local_seen.add(unique_key)
            self._save_local_seen()
        # Append to daily JSONL (crash-safe)
        with open(self.json_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def consolidate_to_csv(jsonl_path=None):
        # Read JSONL, collect all fieldnames, write CSV with sorted columns
```

**Deduplication Strategy:**
- **Primary**: Redis SET `scraper:seen_urls` (atomic, shared across workers)
- **Fallback**: Local file `.seen_keys` (one key per line) when Redis unavailable
- **Unique Key**: Preference order: `cin` → `company_link` → `{target}:{page}:{idx}`

**Output Files:**
- `data/json/YYYY-MM-DD.jsonl` - One JSON object per line, appended instantly
- `data/csv/YYYY-MM-DD.csv` - Generated by `consolidate_to_csv()`, all fields as columns

### 4.6 Tasks (`scraper/tasks.py`)

**Internal Async Function**: `_scrape_target(target)`

```python
async def _scrape_target(target):
    await browser_manager.start()
    try:
        page_num = 1
        max_pages = target["pagination"]["max_pages"]
        base_url = target["base_url"]
        pagination = target["pagination"]
        
        while page_num <= max_pages:
            # Build URL for this page
            if pagination["type"] == "url_pattern":
                url = pagination["url_pattern"].format(base_url=base_url, page=page_num)
            elif page_num == 1:
                url = target["start_urls"][0]
            else:
                break
            
            # Fetch listing page
            html = await fetch_page(url, wait_for_selector=target.get("wait_for_selector"))
            records = parse_listing_page(html, target["list_item_selector"], target["fields"])
            
            # Concurrent company scraping (semaphore = 6)
            semaphore = asyncio.Semaphore(6)
            
            async def scrape_company(record, idx):
                async with semaphore:
                    company_link = record.get("company_link") or record.get("link")
                    if not company_link: return record
                    
                    company_url = urljoin(base_url, company_link)
                    try:
                        company_html = await fetch_page(company_url, 
                            wait_for_selector=target.get("company_wait_for_selector"))
                        company_data = parse_company_page(company_html, target.get("company_fields", {}))
                        record.update(company_data)
                    except Exception as e:
                        logger.warning(f"Failed to scrape {company_url}: {e}")
                    
                    unique_key = record.get("cin") or record.get("company_link") or f"{target['name']}:{page_num}:{idx}"
                    record["_source"] = target["name"]
                    pipeline.save(record, unique_key)
                    return record
            
            tasks = [scrape_company(r, i) for i, r in enumerate(records)]
            await asyncio.gather(*tasks)
            
            logger.info(f"[{target['name']}] page {page_num}: {len(records)} records")
            page_num += 1
    finally:
        await browser_manager.stop()
```

**Celery Tasks:**

| Task | Signature | Description |
|------|-----------|-------------|
| `scrape_single_target` | `(target_name: str)` | Runs `_scrape_target` for one target; retries 2x on failure (60s delay) |
| `run_full_crawl` | `()` | Fans out `scrape_single_target.delay()` for all targets in `targets.yaml` |
| `consolidate_all` | `()` | Calls `pipeline.consolidate_to_csv()` to roll JSONL → CSV |

### 4.7 Queue Manager (`scraper/queue_manager.py`)

```python
celery_app = Celery("scraper", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    worker_concurrency=settings.max_concurrent_browsers,  # 10
    worker_prefetch_multiplier=1,     # Don't over-fetch heavy browser tasks
    task_acks_late=True,              # Re-queue if worker dies mid-task
    task_reject_on_worker_lost=True,
)

celery_app.conf.beat_schedule = {
    "run-full-crawl-hourly": {
        "task": "scraper.tasks.run_full_crawl",
        "schedule": crontab(minute=0),      # Every hour at :00
    },
    "consolidate-csv-daily": {
        "task": "scraper.tasks.consolidate_all",
        "schedule": crontab(hour=23, minute=55),  # Daily at 23:55
    },
}
```

### 4.8 Worker Entry (`workers/celery_worker.py`)

```python
from scraper.queue_manager import celery_app
from scraper import tasks  # noqa: F401  (registers tasks)

app = celery_app
```

**Commands:**
- Worker: `celery -A workers.celery_worker worker --loglevel=info --concurrency=5`
- Beat: `celery -A workers.celery_worker beat --loglevel=info`
- Flower: `celery -A workers.celery_worker flower --port=5555`

---

## 5. DATA FLOW

### 5.1 Complete Data Flow (Single Company)

```mermaid
sequenceDiagram
    autonumber
    participant Task as _scrape_target()
    participant Fetch as fetch_page()
    participant Browser as BrowserManager
    participant Proxy as ProxyManager
    participant Parse as Parser
    participant Pipe as DataPipeline
    participant Redis as Redis
    participant File as JSONL File

    Task->>Fetch: fetch_page(listing_url)
    Fetch->>Proxy: get_proxy()
    Proxy-->>Fetch: proxy_url (health-checked)
    Fetch->>Browser: new_stealth_context(proxy_url)
    Browser-->>Fetch: context, page
    Fetch->>Target: page.goto(listing_url)
    Target-->>Fetch: listing HTML
    Fetch-->>Task: HTML string
    Task->>Parse: parse_listing_page(html, selectors)
    Parse-->>Task: List[dict] (30 companies)
    
    par For each company (max 6 concurrent)
        Task->>Fetch: fetch_page(company_url)
        Fetch->>Proxy: get_proxy() [new proxy]
        Proxy-->>Fetch: proxy_url
        Fetch->>Browser: new_stealth_context(proxy_url)
        Browser-->>Fetch: context, page
        Fetch->>Target: page.goto(company_url)
        Target-->>Fetch: detail HTML
        Fetch-->>Task: HTML string
        Task->>Parse: parse_company_page(html, company_fields)
        Parse-->>Task: dict (CIN, directors, address, etc.)
        Task->>Task: record.update(company_data)
        Task->>Pipe: save(record, unique_key)
        Pipe->>Redis: SADD scraper:seen_urls unique_key
        Pipe->>File: append json.dumps(record) + "\n"
    end
```

### 5.2 Data Structures

**Listing Record (before detail scrape):**
```json
{
  "company_name": "OMANSH ENTERPRISES LIMITED",
  "company_link": "/omansh-enterprises-limited/company/L01100DL1974PLC241646",
  "incorporation_year": "1974",
  "industry": "Agriculture and Allied Activities",
  "status": "Active"
}
```

**Complete Record (after detail scrape):**
```json
{
  "company_name": "OMANSH ENTERPRISES LIMITED",
  "company_link": "/omansh-enterprises-limited/company/L01100DL1974PLC241646",
  "incorporation_year": "1974",
  "industry": "Agriculture and Allied Activities",
  "status": "Active",
  "cin": "L01100DL1974PLC241646",
  "registered_address": "B-507, 5th Floor, Statesman House, Barakhamba Road...",
  "authorized_capital": "₹ 3.5  Cr",
  "paid_up_capital": "₹ 3.5  Cr",
  "email": "omanshwork@gmail.com",
  "directors": [
    {"director_name": "Rajiv Vashisht", "din": "02985977", "designation": "Director", "tenure": "2 years"},
    ...
  ],
  "_source": "tofler"
}
```

### 5.3 JSONL Output Format

- **File**: `data/json/YYYY-MM-DD.jsonl`
- **Encoding**: UTF-8, `ensure_ascii=False`
- **Line format**: One valid JSON object per line, newline-terminated
- **Append mode**: `open(path, "a")` - crash-safe, no buffering issues

### 5.4 CSV Consolidation

- **Trigger**: Daily at 23:55 via Celery Beat (`consolidate_all` task)
- **Input**: `data/json/YYYY-MM-DD.jsonl`
- **Output**: `data/csv/YYYY-MM-DD.csv`
- **Columns**: Union of all keys across records, sorted alphabetically
- **Header**: Written via `csv.DictWriter`

---

## 6. CONCURRENCY & SCALING

### 6.1 Concurrency Levels

| Level | Configuration | Value | Purpose |
|-------|---------------|-------|---------|
| **Worker Processes** | `docker-compose.yml` replicas | 3 | Horizontal scale |
| **Task Concurrency** | `--concurrency=5` / `worker_concurrency=10` | 5-10 | Tasks per worker |
| **Company Semaphore** | `asyncio.Semaphore(6)` in `_scrape_target` | 6 | Concurrent company scrapes per page |
| **Browser Contexts** | One per `fetch_page()` call | N | Isolated IP/fingerprint per request |

### 6.2 Scaling Strategy

```mermaid
flowchart LR
    subgraph "Horizontal Scale (add workers)"
        R3[Replicas: 3] --> R5[Replicas: 5] --> R10[Replicas: 10+]
    end
    
    subgraph "Vertical Scale (per worker)"
        C5[Concurrency: 5] --> C10[Concurrency: 10] --> C20[Concurrency: 20]
    end
    
    subgraph "Task-Level"
        S6[Semaphore: 6] --> S12[Semaphore: 12]
    end
```

**To increase throughput:**
1. **More workers**: Increase `replicas` in `docker-compose.yml`
2. **Higher concurrency**: Increase `--concurrency` and `max_concurrent_browsers`
3. **More parallel companies**: Increase semaphore in `tasks.py` (limited by proxy pool size)
4. **Split targets**: Add multiple Beat schedules with different frequencies

### 6.3 Resource Constraints

| Resource | Limiting Factor | Mitigation |
|----------|----------------|------------|
| **Proxies** | Pool size (~100 static) | Use provider API for unlimited IPs |
| **Browser Memory** | ~200-500MB per context | Limit concurrency; restart browser periodically |
| **CPU** | Chromium rendering | Horizontal scaling across hosts |
| **Redis** | Single instance | Redis Cluster / ElastiCache for HA |

---

## 7. ERROR HANDLING & RETRY LOGIC

### 7.1 Retry Matrix

| Failure Point | Exception | Retry Attempts | Backoff | Proxy Action |
|---------------|-----------|----------------|---------|--------------|
| Navigation timeout | `PlaywrightTimeout` | 5 | 1-10s exp | `mark_bad()` |
| Block page detected | `FetchBlocked` | 5 | 1-10s exp | `mark_bad()` |
| Proxy tunnel failure | `ProxyConnectionError` | 5 | 1-10s exp | `mark_bad()` (immediate) |
| Selector timeout | `PlaywrightTimeout` | 5 | 1-10s exp | `mark_bad()` |
| Detail scrape error | Generic `Exception` | 0 (logged, continues) | - | - |
| Task failure | Any | 2 (Celery) | 60s fixed | - |

### 7.2 Proxy Failure Handling

```mermaid
flowchart TD
    FETCH[fetch_page] --> NAV[page.goto]
    NAV -->|Timeout| TIMEOUT[PlaywrightTimeout]
    NAV -->|Proxy Error| PROXY_ERR[PlaywrightError]
    NAV -->|Success| CHECK[Block detection]
    CHECK -->|Block found| BLOCKED[FetchBlocked]
    CHECK -->|Clean| RETURN[Return HTML]
    
    TIMEOUT --> MARK[proxy_manager.mark_bad]
    PROXY_ERR --> IS_PROXY{_is_proxy_connection_error?}
    IS_PROXY -->|Yes| MARK
    IS_PROXY -->|No| RERAISE[Raise original]
    BLOCKED --> MARK
    MARK --> RETRY[Tenantcity retry]
    RETRY --> FETCH[New proxy via get_proxy]
```

### 7.3 Task-Level Retry (Celery)

```python
@celery_app.task(name="scraper.tasks.scrape_single_target", bind=True, max_retries=2)
def scrape_single_target(self, target_name: str):
    try:
        asyncio.run(_scrape_target(target))
    except Exception as exc:
        logger.error(f"[{target_name}] scrape failed: {exc}")
        raise self.retry(exc=exc, countdown=60)  # 60s delay, max 2 retries
```

- `task_acks_late=True`: Task acknowledged only after completion
- `task_reject_on_worker_lost=True`: Re-queued if worker crashes
- `worker_prefetch_multiplier=1`: No task hoarding (browser tasks are heavy)

---

## 8. DEPLOYMENT ARCHITECTURE

### 8.1 Docker Compose Services

```yaml
services:
  redis:
    image: redis:7-alpine
    volumes: [redis_data:/data]

  worker:
    build: .
    replicas: 3
    command: celery -A workers.celery_worker worker --loglevel=info --concurrency=5
    env_file: .env
    volumes: [./data:/app/data, ./logs:/app/logs]
    depends_on: [redis]

  beat:
    build: .
    command: celery -A workers.celery_worker beat --loglevel=info
    env_file: .env
    volumes: [./data:/app/data, ./logs:/app/logs]
    depends_on: [redis]

  flower:
    build: .
    command: celery -A workers.celery_worker flower --port=5555
    ports: ["5555:5555"]
    env_file: .env
    depends_on: [redis]
```

### 8.2 Dockerfile

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium
COPY . .
RUN mkdir -p data/json data/csv logs
CMD ["celery", "-A", "workers.celery_worker", "worker", "--loglevel=info"]
```

### 8.3 Deployment Options

| Platform | Approach |
|----------|----------|
| **Bare EC2** | `docker compose up -d` |
| **ECS/Fargate** | Push image to ECR, define task definitions per service, use ElastiCache for Redis |
| **Kubernetes** | Deploy as Deployments (worker), CronJob (beat), Service (flower); use external Redis |
| **GCP** | Compute Engine or GKE (Cloud Run unsuitable for long-running browsers); Memorystore for Redis |

---

## 9. ENVIRONMENT VARIABLES

### 9.1 Required Variables (`.env`)

| Variable | Description | Example |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection string | `redis://redis:6379/0` |
| `PROXY_LIST` | Comma-separated static proxies | `http://user:pass@host:port,...` |
| `PROXY_PROVIDER_API_URL` | Rotating proxy provider endpoint | `https://api.proxyscrape.com` |
| `PROXY_PROVIDER_API_KEY` | Provider API authentication | `sk-xxxxx` |
| `PROXY_USERNAME` | Fallback username for provider proxies | `35jovid14xin` |
| `PROXY_PASSWORD` | Fallback password for provider proxies | `28168810vcfgaui` |
| `CAPTCHA_SOLVER_PROVIDER` | CAPTCHA service (placeholder) | `2captcha` |
| `CAPTCHA_SOLVER_API_KEY` | CAPTCHA service API key | `xxxxx` |

### 9.2 Tuning Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_CONCURRENT_BROWSERS` | 10 | Celery worker concurrency |
| `REQUEST_DELAY_MIN` | 0.3 | Minimum random delay between requests (seconds) |
| `REQUEST_DELAY_MAX` | 0.8 | Maximum random delay between requests (seconds) |
| `OUTPUT_DIR` | `./data` | Output directory for JSONL/CSV |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |

---

## 10. LOGGING & MONITORING

### 10.1 Logging Configuration (`scraper/utils/logger.py`)

```python
logger.remove()
logger.add(sys.stdout, level=settings.log_level,
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}")
logger.add("logs/scraper_{time:YYYY-MM-DD}.log", rotation="00:00",
           retention="14 days", level=settings.log_level)
```

**Outputs:**
- **Stdout**: Real-time console (Docker logs)
- **File**: `logs/scraper_YYYY-MM-DD.log` (daily rotation, 14-day retention)

### 10.2 Key Log Events

| Event | Level | Context |
|-------|-------|---------|
| Browser launched/closed | INFO | `BrowserManager.start/stop` |
| Proxy health check passed/failed | INFO/WARNING | `ProxyManager._health_check` |
| Proxy marked bad (cooldown) | WARNING | `ProxyManager.mark_bad` / `fetch_page` |
| Block page detected | WARNING | `fetch_page` |
| Company scrape start | INFO | `scrape_company` |
| Company scrape failure | WARNING | `scrape_company` exception |
| Page completed | INFO | `_scrape_target` page summary |
| JSONL save (implicit) | - | `pipeline.save` (no log by default) |
| CSV consolidation | INFO | `consolidate_to_csv` |

### 10.3 Flower Dashboard

- **URL**: `http://<host>:5555`
- **Features**: Task status, throughput, failures, worker stats, task history
- **Authentication**: None by default (add `--basic-auth` in production)

---

## 11. EXTENDING THE SYSTEM

### 11.1 Adding a New Target Site

1. **Add entry to `config/targets.yaml`**:
```yaml
  - name: new_site
    base_url: "https://example.com"
    start_urls:
      - "https://example.com/list"
    list_item_selector: ".item"
    fields:
      name: "h3.title"
      link: "a::attr(href)"
    company_wait_for_selector: "#detail"
    company_fields:
      name: "h1"
      email: ".contact::attr(href)"
    pagination:
      type: "url_pattern"
      url_pattern: "{base_url}/list?page={page}"
      max_pages: 10
```

2. **Test locally**: `python scripts/run_once.py new_site`

3. **Deploy**: No code changes needed; Beat will pick it up on next hourly run

### 11.2 Pagination Types

| Type | Configuration | Behavior |
|------|---------------|----------|
| `url_pattern` | `url_pattern: "{base}/pg-{page}"`, `max_pages: N` | Constructs URLs directly |
| `next_selector` | `next_selector: "a.next::attr(href)"` | Extracts next link from page (TODO in code) |

### 11.3 Special Field Extractors

Add in `parser.py`:
```python
# In parse_company_page()
if selector == "MY_CUSTOM_KEY":
    record[key] = _extract_my_custom(soup)

def _extract_my_custom(soup):
    # Custom extraction logic
    return value
```

---

## 12. KNOWN LIMITATIONS & FUTURE WORK

### 12.1 Current Limitations

| Area | Limitation | Impact |
|------|------------|--------|
| **CAPTCHA** | No solving integration | Blocks on interactive challenges |
| **Redis HA** | Single instance | SPOF for dedup/task queue |
| **Monitoring** | Only Flower dashboard | No alerting, no metrics export |
| **Testing** | No automated tests | Regression risk |
| **Proxy API** | Static list only (provider code present but unused) | Manual proxy management |
| **Pagination** | `next_selector` not implemented | Only `url_pattern` works |
| **Rate Limiting** | Per-target `rate_limit_seconds` unused | No adaptive throttling |
| **Data Validation** | No schema validation | Malformed data possible |

### 12.2 Recommended Enhancements

1. **CAPTCHA Integration**: Wire 2Captcha/Anti-Captcha at `BLOCK_MARKERS` detection point in `fetcher.py`
2. **Redis HA**: Migrate to Redis Cluster or managed service (ElastiCache, Memorystore)
3. **Metrics**: Add Prometheus exporter + Grafana dashboards
4. **Tests**: Unit tests for parser, pipeline; integration tests for fetcher
5. **Schema Validation**: Add Pydantic models for record validation before save
6. **Adaptive Rate Limiting**: Implement token bucket per domain
7. **Proxy Provider API**: Complete the dynamic pool refresh logic
8. **Graceful Shutdown**: Handle SIGTERM in worker to finish current task

---

## 13. FILE REFERENCE MAP

### 13.1 Import Dependency Graph

```mermaid
flowchart TD
    SETTINGS[config/settings.py] --> PROXY[proxy_manager.py]
    SETTINGS --> FETCHER[fetcher.py]
    SETTINGS --> PIPELINE[pipeline.py]
    SETTINGS --> QUEUE[queue_manager.py]
    SETTINGS --> LOGGER[utils/logger.py]
    
    PROXY --> BROWSER[browser.py]
    PROXY --> FETCHER
    
    BROWSER --> FETCHER
    BROWSER --> TASKS[tasks.py]
    
    FETCHER --> TASKS
    PARSER[parser.py] --> TASKS
    PIPELINE --> TASKS
    LOGGER --> ALL[All modules]
    
    QUEUE --> CELERY_WORKER[workers/celery_worker.py]
    TASKS --> CELERY_WORKER
```

### 13.2 Key Entry Points

| Entry Point | Command | Purpose |
|-------------|---------|---------|
| Worker | `celery -A workers.celery_worker worker` | Process scrape tasks |
| Beat | `celery -A workers.celery_worker beat` | Schedule hourly crawl |
| Flower | `celery -A workers.celery_worker flower` | Monitoring dashboard |
| Local Test | `python scripts/run_once.py tofler` | Single-run without Celery |
| Consolidate | `python -c "from scraper.pipeline import pipeline; pipeline.consolidate_to_csv()"` | Manual CSV rollup |

---

*End of SYSTEM_ARCHITECTURE.md*