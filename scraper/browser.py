import random
from typing import Optional, Tuple

from playwright.async_api import async_playwright, Browser, BrowserContext
from playwright_stealth import stealth_async

from scraper.proxy_manager import proxy_manager
from scraper.utils.user_agents import random_user_agent
from scraper.utils.logger import logger

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]


class BrowserManager:
    """
    Owns one Playwright/Chromium process per worker and hands out isolated
    contexts — each with its own proxy, user-agent, viewport, and stealth
    patches applied — so concurrent scrape tasks never share an IP,
    fingerprint, or cookie jar.
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        logger.info("Browser launched")

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed")

    async def new_stealth_context(self) -> Tuple[BrowserContext, Optional[str]]:
        proxy_url = proxy_manager.get_proxy()
        proxy_cfg = proxy_manager.to_playwright_format(proxy_url) if proxy_url else None

        context = await self._browser.new_context(
            proxy=proxy_cfg,
            user_agent=random_user_agent(),
            viewport=random.choice(VIEWPORTS),
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        page = await context.new_page()
        # Patches navigator.webdriver, plugin/language fingerprints, etc.
        await stealth_async(page)
        return context, proxy_url


browser_manager = BrowserManager()
