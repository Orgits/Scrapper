"""
Production-ready Fetcher with:
- Configurable timeouts
- Exponential backoff retries
- Detailed error classification
- Proxy integration with failure tracking
- Graceful shutdown awareness
"""
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from playwright.async_api import TimeoutError as PlaywrightTimeout, Error as PlaywrightError

from config.settings import settings
from scraper.browser import browser_manager
from scraper.proxy_manager import proxy_manager
from scraper.exceptions import FetchFailedError
from scraper.utils.logger import get_logger


logger = get_logger("fetcher")

# Generic phrases that show up on interstitial / block pages across most
# anti-bot vendors. Extend this per-target if a site uses its own wording.
# Note: Avoid false positives from legitimate CDN domains (e.g., cloudflare.com in script src)
BLOCK_MARKERS = [
    "access denied",
    "are you a robot",
    "verify you are human",
    "unusual traffic",
    "checking your browser",
    "captcha",
    "cloudflare challenge",
    "cloudflare captcha",
    "ray id",
    "cf-mitigated",
    "cf-ray",
]


class FetchBlocked(Exception):
    """Target returned anti-bot challenge/interstitial page."""
    pass


class ProxyConnectionError(Exception):
    """Proxy tunnel/connection failed - immediate proxy replacement needed."""
    pass


class TargetTimeoutError(Exception):
    """Target page load/selector timeout - may work with different proxy."""
    pass


class TargetHTTPError(Exception):
    """Target returned HTTP error (403, 429, 5xx)."""
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class SelectorTimeoutError(Exception):
    """Wait for selector timed out - page may not have loaded fully."""
    pass


@dataclass
class FetchDiagnostics:
    """Diagnostics captured for failed fetch attempts."""
    url: str
    proxy: Optional[str] = None
    error_type: str = ""
    error_message: str = ""
    http_status: Optional[int] = None
    page_title: str = ""
    final_url: str = ""
    body_snippet: str = ""
    duration_seconds: float = 0.0
    attempt: int = 0


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


def _classify_playwright_timeout(error: PlaywrightTimeout) -> type[Exception]:
    """Classify Playwright timeout into specific error type."""
    error_str = str(error).lower()
    if "wait_for_selector" in error_str or "selector" in error_str:
        return SelectorTimeoutError
    if "navigation" in error_str or "goto" in error_str:
        return TargetTimeoutError
    return TargetTimeoutError


async def _fetch_with_context(
    url: str,
    context,
    proxy_used: Optional[str],
    wait_for_selector: Optional[str] = None,
    attempt: int = 0,
) -> Tuple[str, FetchDiagnostics]:
    """
    Single fetch attempt with a given browser context.
    Returns (html, diagnostics).
    """
    start_time = time.time()
    diagnostics = FetchDiagnostics(url=url, proxy=proxy_used, attempt=attempt)

    try:
        page = context.pages[0] if context.pages else await context.new_page()

        # Navigate with timeout from settings
        nav_timeout = settings.navigation_timeout * 1000  # convert to ms
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
            if response:
                diagnostics.http_status = response.status
                # Check for HTTP errors
                if response.status >= 400:
                    raise TargetHTTPError(response.status, f"HTTP {response.status} for {url}")
        except PlaywrightTimeout as e:
            raise TargetTimeoutError(f"Navigation timeout ({settings.navigation_timeout}s): {e}") from e

        if wait_for_selector:
            selector_timeout = settings.selector_wait_timeout * 1000
            try:
                await page.wait_for_selector(wait_for_selector, timeout=selector_timeout)
            except PlaywrightTimeout as e:
                error_type = _classify_playwright_timeout(e)
                raise error_type(f"Selector '{wait_for_selector}' timeout ({settings.selector_wait_timeout}s): {e}") from e

        # Random delay between requests
        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))

        html = await page.content()
        diagnostics.final_url = page.url
        diagnostics.page_title = await page.title()
        diagnostics.body_snippet = html[:500]
        diagnostics.duration_seconds = time.time() - start_time

        # Check for anti-bot challenges
        lower_html = html.lower()
        if any(marker in lower_html for marker in BLOCK_MARKERS):
            diagnostics.error_type = "anti_bot"
            diagnostics.error_message = "Block/interstitial page detected"
            raise FetchBlocked(f"Block/interstitial page detected: {url}")

        return html, diagnostics

    except Exception as e:
        diagnostics.duration_seconds = time.time() - start_time
        diagnostics.error_message = str(e)
        diagnostics.error_type = type(e).__name__
        raise


async def fetch_page_with_retry(
    url: str,
    wait_for_selector: Optional[str] = None,
    target_name: str = "unknown",
) -> Tuple[str, Optional[str]]:
    """
    Fetch a page with automatic proxy retry and exponential backoff.

    Returns (html, proxy_used) where proxy_used is the proxy that succeeded,
    or None if direct connection was used.

    Raises FetchFailedError if all attempts exhausted.
    """
    max_attempts = settings.max_proxy_attempts_per_url
    last_error: Optional[Exception] = None
    failed_proxies: list[str] = []

    for attempt in range(max_attempts):
        # Check for shutdown
        if browser_manager.is_shutting_down():
            raise RuntimeError("Browser manager is shutting down")

        # Get next available proxy
        proxy_url = proxy_manager.get_proxy()
        if proxy_url in failed_proxies:
            # Skip if we already tried this proxy for this URL
            continue

        logger.info(
            f"[{target_name}] Fetch attempt {attempt + 1}/{max_attempts} for {url} "
            f"via {proxy_manager._mask_proxy(proxy_url) if proxy_url else 'direct'}"
        )

        context = None
        try:
            context, _ = await browser_manager.new_stealth_context(proxy_url)
            html, diagnostics = await _fetch_with_context(
                url, context, proxy_url, wait_for_selector, attempt + 1
            )

            # Success!
            if proxy_url:
                proxy_manager.mark_proxy_success(proxy_url)
            logger.info(
                f"[{target_name}] Fetch succeeded via "
                f"{proxy_manager._mask_proxy(proxy_url) if proxy_url else 'direct'} "
                f"({diagnostics.duration_seconds:.1f}s)"
            )
            return html, proxy_url

        except (ProxyConnectionError, TargetTimeoutError, SelectorTimeoutError, FetchBlocked) as e:
            last_error = e
            error_type = type(e).__name__

            if proxy_url:
                # Record appropriate failure type
                if error_type == "ProxyConnectionError":
                    proxy_manager.record_connectivity_failure(proxy_url, str(e))
                elif error_type in ("TargetTimeoutError", "SelectorTimeoutError"):
                    proxy_manager.record_timeout(proxy_url)
                elif error_type == "FetchBlocked":
                    proxy_manager.record_anti_bot(proxy_url)
                else:
                    proxy_manager.record_generic_failure(proxy_url, str(e))

                failed_proxies.append(proxy_url)

            logger.warning(f"[{target_name}] Attempt {attempt + 1} failed: {error_type} - {e}")

            # Exponential backoff before retry
            if attempt < max_attempts - 1:
                delay = min(
                    settings.retry_base_delay * (2 ** attempt) + random.uniform(0, 1),
                    settings.retry_max_delay,
                )
                logger.debug(f"[{target_name}] Waiting {delay:.1f}s before retry...")
                await asyncio.sleep(delay)

        except TargetHTTPError as e:
            last_error = e
            if proxy_url:
                proxy_manager.record_http_error(proxy_url, e.status_code)
                failed_proxies.append(proxy_url)
            logger.warning(f"[{target_name}] HTTP error {e.status_code}: {e}")

            # Exponential backoff
            if attempt < max_attempts - 1:
                delay = min(
                    settings.retry_base_delay * (2 ** attempt) + random.uniform(0, 1),
                    settings.retry_max_delay,
                )
                await asyncio.sleep(delay)

        except Exception as e:
            last_error = e
            if proxy_url:
                proxy_manager.record_generic_failure(proxy_url, str(e))
                failed_proxies.append(proxy_url)
            logger.warning(f"[{target_name}] Unexpected error: {type(e).__name__} - {e}")

            if attempt < max_attempts - 1:
                delay = min(
                    settings.retry_base_delay * (2 ** attempt) + random.uniform(0, 1),
                    settings.retry_max_delay,
                )
                await asyncio.sleep(delay)

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    # All proxy attempts exhausted - try direct connection if enabled
    if settings.proxy_fallback_direct and proxy_manager._enabled:
        logger.info(f"[{target_name}] All proxy attempts failed, trying direct connection...")
        direct_context = None
        try:
            direct_context, _ = await browser_manager.new_stealth_context(None)
            html, diagnostics = await _fetch_with_context(url, direct_context, None, wait_for_selector, max_attempts + 1)
            logger.info(f"[{target_name}] Direct connection succeeded ({diagnostics.duration_seconds:.1f}s)")
            return html, None
        except Exception as e:
            logger.warning(f"[{target_name}] Direct connection also failed: {type(e).__name__} - {e}")
            last_error = e
        finally:
            if direct_context:
                try:
                    await direct_context.close()
                except Exception:
                    pass

    # Everything failed
    raise FetchFailedError(
        url=url,
        attempts=max_attempts + (1 if settings.proxy_fallback_direct else 0),
        last_error=last_error,
        failed_proxies=failed_proxies,
    ) from last_error


# Legacy compatibility - fetch_page with simple retry
async def fetch_page(url: str, wait_for_selector: Optional[str] = None) -> str:
    """
    Legacy fetch function with basic retry.
    For new code, use fetch_page_with_retry().
    """
    context, proxy_used = await browser_manager.new_stealth_context()
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=settings.navigation_timeout * 1000)

        if wait_for_selector:
            await page.wait_for_selector(wait_for_selector, timeout=settings.selector_wait_timeout * 1000)

        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))

        html = await page.content()
        lower_html = html.lower()

        if any(marker in lower_html for marker in BLOCK_MARKERS):
            if proxy_used:
                proxy_manager.mark_proxy_failure(proxy_used, "anti_bot")
            raise FetchBlocked(f"Block/interstitial page detected: {url}")

        return html

    except PlaywrightTimeout:
        if proxy_used:
            proxy_manager.mark_proxy_failure(proxy_used, "timeout")
        raise
    except PlaywrightError as e:
        if _is_proxy_connection_error(e):
            if proxy_used:
                logger.warning(f"Proxy connection error, marking bad: {proxy_manager._mask_proxy(proxy_used)}")
                proxy_manager.mark_proxy_failure(proxy_used, "connectivity", error=str(e))
            raise ProxyConnectionError(f"Proxy connection failed: {e}") from e
        raise
    finally:
        await context.close()