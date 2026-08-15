import random
import time
import threading
from typing import Optional, List
from urllib.parse import urlparse

import requests

from config.settings import settings
from scraper.utils.logger import logger


class ProxyManager:
    """
    Static proxy pool loaded from PROXY_LIST in .env.

    - Proxies are loaded from settings.proxy_list on startup.
    - Each proxy is health-checked before use (http://httpbin.org/ip).
    - Failed proxies go into cooldown and are replaced automatically.
    - No external API dependency.
    """

    def __init__(
        self,
        cooldown_seconds: int = 300,
        health_check_timeout: int = 10,
    ):
        self._cooldown_seconds = cooldown_seconds
        self._health_check_timeout = health_check_timeout

        # Load static proxies from settings
        self._static_proxies = [p.strip() for p in settings.proxy_list.split(",") if p.strip()]
        self._pool: List[str] = list(self._static_proxies)
        self._cooldown: dict[str, float] = {}
        self._failed_proxies: set[str] = set()
        self._verified_proxies: set[str] = set()
        self._lock = threading.Lock()

        if not self._pool:
            logger.warning("PROXY_LIST is empty - no proxies configured")

    @staticmethod
    def _mask_proxy(proxy: str) -> str:
        parsed = urlparse(proxy)
        if parsed.password:
            masked = proxy.replace(parsed.password, "***")
            return masked
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

    def _health_check(self, proxy: str) -> bool:
        """Test proxy connectivity via httpbin.org/ip."""
        if proxy in self._verified_proxies:
            return True
        if proxy in self._failed_proxies:
            return False

        parsed = self._parse_proxy(proxy)
        if not parsed:
            logger.warning(f"Invalid proxy format: {self._mask_proxy(proxy)}")
            self._failed_proxies.add(proxy)
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
                logger.info(f"Proxy health check passed: {self._mask_proxy(proxy)} -> {data['origin']}")
                self._verified_proxies.add(proxy)
                return True
        except Exception as e:
            logger.warning(f"Proxy health check failed: {self._mask_proxy(proxy)} - {e}")

        self._failed_proxies.add(proxy)
        return False

    def _usable(self, proxy: str) -> bool:
        until = self._cooldown.get(proxy)
        return until is None or time.time() > until

    def mark_bad(self, proxy: str):
        with self._lock:
            logger.warning(f"Proxy marked bad, cooling down {self._cooldown_seconds}s: {self._mask_proxy(proxy)}")
            self._cooldown[proxy] = time.time() + self._cooldown_seconds
            self._failed_proxies.add(proxy)
            self._verified_proxies.discard(proxy)

    def get_proxy(self) -> Optional[str]:
        with self._lock:
            usable = [p for p in self._pool if self._usable(p) and p not in self._failed_proxies]

        if not usable:
            logger.error("No usable proxies available")
            return None

        # Try each candidate with health check
        random.shuffle(usable)
        for proxy in usable:
            if self._health_check(proxy):
                return proxy

        logger.error("All candidate proxies failed health check")
        return None

    @staticmethod
    def to_playwright_format(proxy_url: str) -> dict:
        """'http://user:pass@host:port' -> Playwright's {server, username, password}."""
        parsed = urlparse(proxy_url)
        return {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password,
        }


proxy_manager = ProxyManager()
