import asyncio
import random
import re
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeout, Error as PlaywrightError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from scraper.browser import browser_manager
from scraper.proxy_manager import proxy_manager
from scraper.utils.logger import logger

# Generic phrases that show up on interstitial / block pages across most
# anti-bot vendors. Extend this per-target if a site uses its own wording.
BLOCK_MARKERS = ["access denied", "are you a robot", "verify you are human",
                  "unusual traffic", "checking your browser"]


class FetchBlocked(Exception):
    pass


class ProxyConnectionError(Exception):
    """Raised when proxy tunnel/connection fails - triggers immediate proxy replacement."""
    pass


def _is_proxy_connection_error(error: Exception) -> bool:
    """Check if error is a proxy tunnel/connection failure."""
    error_str = str(error).lower()
    proxy_errors = [
        "err_tunnel_connection_failed",
        "net::err_tunnel_connection_failed",
        "err_connection_refused",
        "net::err_connection_refused",
        "err_connection_reset",
        "net::err_connection_reset",
        "err_proxy_connection_failed",
        "net::err_proxy_connection_failed",
        "proxy authentication required",
        "407 proxy authentication required",
        "tunnel connection failed",
        "connection refused",
        "connection reset",
    ]
    return any(err in error_str for err in proxy_errors)


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((PlaywrightTimeout, FetchBlocked, ProxyConnectionError)),
)
async def fetch_page(url: str, wait_for_selector: Optional[str] = None) -> str:
    """
    Loads `url` in a fresh stealth context (new proxy + fingerprint each
    call) and returns the rendered HTML. Optimized for speed.
    On proxy connection failure, immediately marks proxy bad and retries with new proxy.
    """
    context, proxy_used = await browser_manager.new_stealth_context()
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        if wait_for_selector:
            await page.wait_for_selector(wait_for_selector, timeout=10000)

        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))

        html = await page.content()
        lower_html = html.lower()

        if any(marker in lower_html for marker in BLOCK_MARKERS):
            if proxy_used:
                proxy_manager.mark_bad(proxy_used)
            raise FetchBlocked(f"Block/interstitial page detected: {url}")

        return html

    except PlaywrightTimeout:
        if proxy_used:
            proxy_manager.mark_bad(proxy_used)
        raise
    except PlaywrightError as e:
        # Check for proxy connection errors
        if _is_proxy_connection_error(e):
            if proxy_used:
                logger.warning(f"Proxy connection error, marking bad immediately: {proxy_manager._mask_proxy(proxy_used)}")
                proxy_manager.mark_bad(proxy_used)
            raise ProxyConnectionError(f"Proxy connection failed: {e}") from e
        raise
    finally:
        await context.close()
