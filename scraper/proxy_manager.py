"""
Production-ready Proxy Manager with:
- Multiple proxy sources (file, env var)
- Detailed failure categorization
- Metrics and statistics
- Thread-safe operations
- Automatic proxy rotation
- Health checks with configurable targets
"""
import random
import time
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from dataclasses import dataclass, field

import requests

from config.settings import settings
from scraper.utils.logger import get_logger


logger = get_logger("proxy_manager")


@dataclass
class ProxyStats:
    """Statistics for a single proxy."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    connectivity_failures: int = 0
    timeouts: int = 0
    http_errors: int = 0
    anti_bot_detected: int = 0
    last_used: float = 0
    last_success: float = 0
    last_failure: float = 0
    consecutive_failures: int = 0
    permanently_disabled: bool = False


class ProxyManager:
    """
    Resilient proxy pool for long-running scraping.

    Features:
    - Loads proxies from file and/or environment variable
    - Separates proxy connectivity failures from target-specific failures
    - Tracks detailed statistics per proxy
    - Permanently disables only after configurable consecutive failures
    - Resets failure counter on successful target request
    - Cooldown period before retrying a failed proxy
    - Periodic health re-check
    - Masks credentials in all logs
    - Thread-safe operations
    """

    def __init__(
        self,
        cooldown_seconds: int = None,
        max_consecutive_failures: int = None,
        health_check_timeout: int = None,
    ):
        self._cooldown_seconds = cooldown_seconds or settings.proxy_cooldown_seconds
        self._max_consecutive_failures = max_consecutive_failures or settings.proxy_max_consecutive_failures
        self._health_check_timeout = health_check_timeout or 10
        self._health_check_interval = settings.proxy_health_check_interval
        self._fallback_direct = settings.proxy_fallback_direct
        self._enabled = settings.proxy_enabled

        # Load proxies from multiple sources
        self._static_proxies = self._load_all_proxies()
        self._pool: List[str] = list(self._static_proxies)
        self._primary_set = set(self._static_proxies)

        # Last-resort fallback pool (e.g. free proxies), kept separate and only
        # merged into the pool once the primary pool is fully exhausted.
        self._fallback_proxies: List[str] = []
        if settings.proxy_fallback_list_path:
            fb = self._load_proxies_from_file(settings.proxy_fallback_list_path)
            # Don't duplicate anything already in the primary pool.
            self._fallback_proxies = [p for p in fb if p not in self._primary_set]
        self._fallback_activated = False

        # Proxy state tracking
        self._cooldown: Dict[str, float] = {}          # proxy -> cooldown expiry timestamp
        self._stats: Dict[str, ProxyStats] = {p: ProxyStats() for p in self._pool}
        self._last_health_check: Dict[str, float] = {p: time.time() for p in self._pool}
        self._lock = threading.Lock()

        if not self._pool:
            logger.warning("Proxy list is empty or disabled - running without proxies (direct connection)")
        else:
            logger.info(f"Loaded {len(self._pool)} proxies")
        if self._fallback_proxies:
            logger.info(f"{len(self._fallback_proxies)} fallback proxies on standby (used only if the primary pool dies)")

    def _load_all_proxies(self) -> List[str]:
        """Load proxies from file and environment variable."""
        proxies = []

        # 1. From file
        if settings.proxy_list_path:
            file_proxies = self._load_proxies_from_file(settings.proxy_list_path)
            proxies.extend(file_proxies)
            logger.debug(f"Loaded {len(file_proxies)} proxies from file")

        # 2. From environment variable (comma-separated)
        if settings.proxy_list:
            env_proxies = self._load_proxies_from_env(settings.proxy_list)
            proxies.extend(env_proxies)
            logger.debug(f"Loaded {len(env_proxies)} proxies from env var")

        # Deduplicate while preserving order
        seen = set()
        unique_proxies = []
        for p in proxies:
            if p not in seen:
                seen.add(p)
                unique_proxies.append(p)

        return unique_proxies

    @staticmethod
    def _load_proxies_from_file(filepath: str) -> List[str]:
        """Load proxies from a file, one per line. Supports user:pass@host:port format."""
        proxies = []
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"Proxy file not found: {filepath}")
            return proxies

        try:
            content = path.read_text().strip()
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    if not line.startswith(("http://", "https://", "socks5://")):
                        line = f"http://{line}"
                    proxies.append(line)
        except Exception as e:
            logger.error(f"Failed to load proxies from {filepath}: {e}")

        return proxies

    @staticmethod
    def _load_proxies_from_env(proxy_string: str) -> List[str]:
        """Load proxies from comma-separated environment variable."""
        proxies = []
        for part in proxy_string.split(","):
            line = part.strip()
            if line and not line.startswith("#"):
                if not line.startswith(("http://", "https://", "socks5://")):
                    line = f"http://{line}"
                proxies.append(line)
        return proxies

    @staticmethod
    def _mask_proxy(proxy: str) -> str:
        """Mask password in proxy string for safe logging."""
        parsed = urlparse(proxy)
        if parsed.password:
            return proxy.replace(parsed.password, "***")
        return proxy

    def _parse_proxy(self, proxy: str) -> Optional[dict]:
        """Parse proxy URL into components."""
        try:
            parsed = urlparse(proxy)
            if not parsed.hostname or not parsed.port:
                return None
            return {
                "scheme": parsed.scheme,
                "host": parsed.hostname,
                "port": parsed.port,
                "username": parsed.username,
                "password": parsed.password,
            }
        except Exception:
            return None

    def _health_check_single(self, proxy: str) -> bool:
        """Test proxy connectivity via http://httpbin.org/ip. Returns True if proxy works."""
        parsed = self._parse_proxy(proxy)
        if not parsed:
            logger.warning(f"Proxy connectivity check: invalid format {self._mask_proxy(proxy)}")
            return False

        proxy_auth = ""
        if parsed["username"] and parsed["password"]:
            proxy_auth = f"{parsed['username']}:{parsed['password']}@"

        proxy_url = f"{parsed['scheme']}://{proxy_auth}{parsed['host']}:{parsed['port']}"
        proxies = {"http": proxy_url, "https": proxy_url}

        try:
            resp = requests.get(
                "http://httpbin.org/ip",
                proxies=proxies,
                timeout=self._health_check_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if "origin" in data:
                logger.debug(f"Proxy health check passed: {self._mask_proxy(proxy)} -> {data['origin']}")
                return True
        except Exception as e:
            logger.debug(f"Proxy health check failed: {self._mask_proxy(proxy)} - {e}")

        return False

    def _maybe_activate_fallback(self) -> bool:
        """Merge the fallback pool in once every primary proxy is permanently
        disabled (trial/subscription lapsed or the list went fully stale).
        Returns True if it activated the fallback this call."""
        if self._fallback_activated or not self._fallback_proxies:
            return False
        with self._lock:
            primaries = [p for p in self._pool if p in self._primary_set]
            # Activate when there is no primary left OR all primaries are dead.
            primary_dead = (not primaries) or all(
                self._stats[p].permanently_disabled for p in primaries
            )
            if not primary_dead:
                return False
            added = 0
            now = time.time()
            for p in self._fallback_proxies:
                if p not in self._stats:
                    self._pool.append(p)
                    self._stats[p] = ProxyStats()
                    self._last_health_check[p] = now
                    added += 1
            self._fallback_activated = True
        logger.error(
            f"Primary proxy pool exhausted — activated {added} FALLBACK proxies. "
            f"Replace the premium list soon; free proxies are best-effort only."
        )
        return True

    def _get_available_proxies(self) -> List[str]:
        """Get list of proxies that are not cooling down and not permanently disabled."""
        now = time.time()
        with self._lock:
            available = [
                p for p in self._pool
                if not self._stats[p].permanently_disabled
                and self._cooldown.get(p, 0) <= now
            ]
        return available

    def _wait_for_next_available(self) -> Optional[str]:
        """Wait for the earliest proxy cooldown to expire and return it."""
        with self._lock:
            cooling_proxies = {
                p: expiry for p, expiry in self._cooldown.items()
                if not self._stats[p].permanently_disabled and expiry > time.time()
            }

        if not cooling_proxies:
            return None

        earliest_expiry = min(cooling_proxies.values())
        wait_time = earliest_expiry - time.time()

        logger.warning(
            f"All {len(cooling_proxies)} proxies cooling down. "
            f"Earliest available in {wait_time:.1f}s. Falling back to direct."
        )
        return None

    def _is_due_for_health_check(self, proxy: str) -> bool:
        """Check if proxy needs periodic health re-check."""
        last_check = self._last_health_check.get(proxy, 0)
        return (time.time() - last_check) >= self._health_check_interval

    def mark_proxy_failure(
        self,
        proxy: str,
        failure_type: str = "target",
        status_code: Optional[int] = None,
        error: Optional[str] = None
    ):
        """
        Record a proxy failure with detailed categorization.

        failure_type:
        - "connectivity": Proxy tunnel/connection failed (DNS, TCP, auth)
        - "timeout": Request timed out
        - "http_error": HTTP 4xx/5xx from target
        - "anti_bot": Challenge/interstitial page detected
        - "target": Generic target request failure
        """
        masked = self._mask_proxy(proxy)
        now = time.time()

        with self._lock:
            self._cooldown[proxy] = now + self._cooldown_seconds
            stats = self._stats[proxy]
            stats.total_requests += 1
            stats.failed_requests += 1
            stats.consecutive_failures += 1
            stats.last_used = now
            stats.last_failure = now

            if failure_type == "connectivity":
                stats.connectivity_failures += 1
            elif failure_type == "timeout":
                stats.timeouts += 1
            elif failure_type == "http_error":
                stats.http_errors += 1
            elif failure_type == "anti_bot":
                stats.anti_bot_detected += 1

        # Log with clear categorization
        log_messages = {
            "connectivity": f"Proxy connectivity failure: {masked}",
            "timeout": f"Proxy timeout: {masked}",
            "http_error": f"Proxy HTTP error {status_code}: {masked}",
            "anti_bot": f"Proxy anti-bot challenge: {masked}",
            "target": f"Proxy target failure: {masked}",
        }
        log_msg = log_messages.get(failure_type, f"Proxy failure ({failure_type}): {masked}")

        if error:
            log_msg += f" - {error}"

        logger.warning(f"{log_msg} (consecutive: {stats.consecutive_failures}/{self._max_consecutive_failures})")

        # Permanently disable after max consecutive failures
        if stats.consecutive_failures >= self._max_consecutive_failures:
            with self._lock:
                stats.permanently_disabled = True
            logger.error(
                f"Proxy permanently disabled after {stats.consecutive_failures} consecutive failures: {masked}"
            )

    def mark_proxy_success(self, proxy: str):
        """Record a successful target request - resets failure counter."""
        masked = self._mask_proxy(proxy)
        now = time.time()

        with self._lock:
            stats = self._stats[proxy]
            stats.total_requests += 1
            stats.successful_requests += 1
            stats.consecutive_failures = 0
            stats.last_used = now
            stats.last_success = now

        logger.debug(f"Proxy success: {masked} (total successes: {stats.successful_requests})")

    def record_connectivity_failure(self, proxy: str, error: str):
        """Called when proxy tunnel/connection fails (from fetcher)."""
        self.mark_proxy_failure(proxy, "connectivity", error=error)

    def record_timeout(self, proxy: str):
        """Called when request times out (from fetcher)."""
        self.mark_proxy_failure(proxy, "timeout")

    def record_http_error(self, proxy: str, status_code: int):
        """Called when target returns HTTP error."""
        if status_code in (403, 429):
            self.mark_proxy_failure(proxy, "anti_bot", status_code=status_code)
        elif 500 <= status_code < 600:
            self.mark_proxy_failure(proxy, "http_error", status_code=status_code)
        else:
            self.mark_proxy_failure(proxy, "http_error", status_code=status_code)

    def record_anti_bot(self, proxy: str):
        """Called when anti-bot challenge detected."""
        self.mark_proxy_failure(proxy, "anti_bot")

    def record_generic_failure(self, proxy: str, error: str):
        """Called for other target failures."""
        self.mark_proxy_failure(proxy, "target", error=error)

    def get_proxy(self) -> Optional[str]:
        """Get next available proxy with periodic health re-check."""
        if not self._enabled:
            return None

        # First try: get immediately available proxies
        available = self._get_available_proxies()

        # If nothing is available, the primary pool may be dead -> bring in the
        # fallback pool (once), then re-check.
        if not available and self._maybe_activate_fallback():
            available = self._get_available_proxies()

        if available:
            # Shuffle for rotation
            random.shuffle(available)

            # Check if any need periodic health re-check
            for proxy in available:
                if self._is_due_for_health_check(proxy):
                    logger.debug(f"Periodic health check for {self._mask_proxy(proxy)}")
                    if self._health_check_single(proxy):
                        self._last_health_check[proxy] = time.time()
                        return proxy
                    else:
                        logger.warning(f"Periodic health check failed: {self._mask_proxy(proxy)}")
                        self.mark_proxy_failure(proxy, "connectivity")
                        continue

                # Return proxy without health check (avoid expensive per-request checks)
                self._last_health_check[proxy] = time.time()
                return proxy

        # Second try: wait for cooldown to expire
        logger.info("No immediately available proxies, waiting for cooldown expiry...")
        return self._wait_for_next_available()

    def get_stats(self) -> Dict[str, Any]:
        """Get proxy pool statistics."""
        with self._lock:
            total = len(self._pool)
            available = len(self._get_available_proxies())
            disabled = sum(1 for s in self._stats.values() if s.permanently_disabled)
            total_requests = sum(s.total_requests for s in self._stats.values())
            total_successes = sum(s.successful_requests for s in self._stats.values())

            proxy_details = []
            for proxy, stats in self._stats.items():
                proxy_details.append({
                    "proxy": self._mask_proxy(proxy),
                    "total_requests": stats.total_requests,
                    "successes": stats.successful_requests,
                    "failures": stats.failed_requests,
                    "consecutive_failures": stats.consecutive_failures,
                    "disabled": stats.permanently_disabled,
                    "success_rate": round(stats.successful_requests / stats.total_requests * 100, 1) if stats.total_requests > 0 else 0,
                })

            return {
                "total_proxies": total,
                "available_proxies": available,
                "disabled_proxies": disabled,
                "total_requests": total_requests,
                "total_successes": total_successes,
                "overall_success_rate": round(total_successes / total_requests * 100, 1) if total_requests > 0 else 0,
                "proxies": proxy_details,
            }

    @staticmethod
    def to_playwright_format(proxy_url: Optional[str]) -> Optional[dict]:
        """'http://user:pass@host:port' -> Playwright's {server, username, password}."""
        if not proxy_url:
            return None
        parsed = urlparse(proxy_url)
        return {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password,
        }


# Global instance
proxy_manager = ProxyManager()