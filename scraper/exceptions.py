"""Custom exceptions for the scraper."""


class FetchFailedError(Exception):
    """Raised when all proxy attempts and direct fallback fail for a URL."""
    
    def __init__(
        self,
        url: str,
        attempts: int,
        last_error: Exception,
        failed_proxies: list[str]
    ):
        self.url = url
        self.attempts = attempts
        self.last_error = last_error
        self.failed_proxies = failed_proxies
        
        msg = (
            f"Failed to fetch {url} after {attempts} attempts "
            f"(proxies tried: {len(failed_proxies)}). "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )
        super().__init__(msg)


class ScrapeResult:
    """Tracks overall scrape statistics."""
    
    def __init__(self):
        self.pages_scraped = 0
        self.companies_scraped = 0
        self.failed_urls: list[dict] = []
        self.proxy_failures = 0
        self.successful_proxies = 0
        self.direct_fallbacks = 0
        self.start_time = None
        self.end_time = None
    
    def add_failed_url(self, url: str, error: str, attempts: int, proxies_tried: int):
        self.failed_urls.append({
            "url": url,
            "error": error,
            "attempts": attempts,
            "proxies_tried": proxies_tried
        })
    
    def summary(self) -> str:
        duration = 0
        if self.start_time and self.end_time:
            duration = self.end_time - self.start_time
        
        lines = [
            "\n" + "=" * 60,
            "SCRAPE SUMMARY",
            "=" * 60,
            f"Duration: {duration:.1f}s",
            f"Pages scraped: {self.pages_scraped}",
            f"Companies scraped: {self.companies_scraped}",
            f"Failed URLs: {len(self.failed_urls)}",
            f"Proxy failures recorded: {self.proxy_failures}",
            f"Successful proxy uses: {self.successful_proxies}",
            f"Direct fallbacks: {self.direct_fallbacks}",
            "",
            "Failed URLs detail:",
        ]
        for failed in self.failed_urls:
            lines.append(
                f"  - {failed['url']}: {failed['error']} "
                f"(attempts: {failed['attempts']}, proxies: {failed['proxies_tried']})"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


scrape_result = ScrapeResult()