"""
Production-ready Browser Manager with:
- Resource pooling and limits
- Graceful shutdown handling
- Context lifecycle management
- Health monitoring
"""
import asyncio
import random
import signal
import weakref
from typing import Optional, Tuple, Set
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import stealth_async

from config.settings import settings
from scraper.proxy_manager import proxy_manager
from scraper.utils.logger import get_logger


logger = get_logger("browser_manager")


VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


class BrowserManager:
    """
    Manages Playwright browser lifecycle with:
    - Single browser instance per process
    - Context pooling with automatic cleanup
    - Graceful shutdown on signals
    - Resource limits
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._active_contexts: Set[weakref.ref] = set()
        self._shutdown_event = asyncio.Event()
        self._startup_lock = asyncio.Lock()
        self._max_contexts = settings.max_concurrent_browsers

    async def start(self):
        """Start the browser with proper configuration."""
        async with self._startup_lock:
            if self._browser is not None:
                return

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                ],
            )
            logger.info(f"Browser launched (max contexts: {self._max_contexts})")

            # Set up signal handlers for graceful shutdown
            self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

    def _signal_shutdown(self):
        """Signal handler to initiate graceful shutdown."""
        logger.info("Shutdown signal received, initiating graceful shutdown...")
        self._shutdown_event.set()

    async def stop(self):
        """Stop the browser gracefully, waiting for active contexts."""
        logger.info("Stopping browser manager...")

        # Wait for active contexts to complete (with timeout)
        if self._active_contexts:
            logger.info(f"Waiting for {len(self._active_contexts)} active contexts to complete...")
            try:
                await asyncio.wait_for(self._wait_for_contexts(), timeout=settings.shutdown_timeout)
            except asyncio.TimeoutError:
                logger.warning("Shutdown timeout reached, forcing context closure")
                await self._force_close_contexts()

        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            self._browser = None

        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            self._playwright = None

        logger.info("Browser stopped")

    async def _wait_for_contexts(self):
        """Wait for all active contexts to be released."""
        while self._active_contexts:
            # Clean up dead references
            self._active_contexts = {ref for ref in self._active_contexts if ref() is not None}
            if self._active_contexts:
                await asyncio.sleep(0.5)

    async def _force_close_contexts(self):
        """Force close all active contexts."""
        for ref in list(self._active_contexts):
            context = ref()
            if context:
                try:
                    await context.close()
                except Exception as e:
                    logger.debug(f"Error force-closing context: {e}")
        self._active_contexts.clear()

    @asynccontextmanager
    async def stealth_context(self, proxy_url: Optional[str] = None):
        """
        Create a new stealth context with automatic cleanup.
        Usage:
            async with browser_manager.stealth_context() as (context, proxy):
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(url)
        """
        if self._shutdown_event.is_set():
            raise RuntimeError("Browser manager is shutting down")

        if self._browser is None:
            await self.start()

        # Check context limit
        active_count = len([ref for ref in self._active_contexts if ref() is not None])
        if active_count >= self._max_contexts:
            logger.warning(f"Max contexts ({self._max_contexts}) reached, waiting...")
            await asyncio.sleep(1)

        if proxy_url is None:
            proxy_url = proxy_manager.get_proxy()

        proxy_cfg = proxy_manager.to_playwright_format(proxy_url)

        context = await self._browser.new_context(
            proxy=proxy_cfg,
            user_agent=random_user_agent(),
            viewport=random.choice(VIEWPORTS),
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )

        # Track context for graceful shutdown
        context_ref = weakref.ref(context)
        self._active_contexts.add(context_ref)

        page = await context.new_page()
        await stealth_async(page)

        try:
            yield context, proxy_url
        finally:
            # Cleanup
            try:
                await context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")
            self._active_contexts.discard(context_ref)

    async def new_stealth_context(self, proxy_url: Optional[str] = None) -> Tuple[BrowserContext, Optional[str]]:
        """
        Create a new stealth context (legacy API).
        Caller is responsible for closing the context.
        """
        if self._browser is None:
            await self.start()

        if proxy_url is None:
            proxy_url = proxy_manager.get_proxy()

        proxy_cfg = proxy_manager.to_playwright_format(proxy_url)

        viewport = random.choice(VIEWPORTS)
        ua = random_user_agent()
        
        context = await self._browser.new_context(
            proxy=proxy_cfg,
            user_agent=ua,
            viewport=viewport,
            locale="en-US",
            timezone_id="Asia/Kolkata",
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            color_scheme="light",
            reduced_motion="reduce",
            forced_colors="none",
        )
        
        # Add extra HTTP headers
        await context.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Cache-Control": "max-age=0",
            "Sec-CH-UA": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        
        page = await context.new_page()
        await stealth_async(page)

        # Track for graceful shutdown
        context_ref = weakref.ref(context)
        self._active_contexts.add(context_ref)

        return context, proxy_url

    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        return self._shutdown_event.is_set()

    def get_active_context_count(self) -> int:
        """Get number of currently active contexts."""
        return len([ref for ref in self._active_contexts if ref() is not None])


# Global instance
browser_manager = BrowserManager()