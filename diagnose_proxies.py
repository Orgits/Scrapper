#!/usr/bin/env python3
"""
Proxy diagnostic script for Tofler.in compatibility testing.
Tests proxies with requests and Playwright against tofler.in.
"""

import asyncio
import time
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright

# Test proxies from the file
TEST_PROXIES = [
    "64g7b2izvh21:9lqtyvy1klbew8n@65.111.6.55:3129",
    "64g7b2izvh21:9lqtyvy1klbew8n@209.50.170.1:3129",
    "64g7b2izvh21:9lqtyvy1klbew8n@104.207.39.235:3129",
    "64g7b2izvh21:9lqtyvy1klbew8n@45.3.52.168:3129",
    "64g7b2izvh21:9lqtyvy1klbew8n@195.63.31.219:3129",
]

TARGETS = {
    "httpbin_http": "http://httpbin.org/ip",
    "httpbin_https": "https://httpbin.org/ip",
    "tofler_home": "https://www.tofler.in/",
    "tofler_company": "https://www.tofler.in/fintech-compu-systems-limited/company/U72200DL1985PLC021153",
}


def mask_proxy(proxy: str) -> str:
    """Mask password in proxy string."""
    parsed = urlparse(f"http://{proxy}")
    if parsed.password:
        return f"http://{parsed.username}:***@{parsed.hostname}:{parsed.port}"
    return f"http://{parsed.username}@{parsed.hostname}:{parsed.port}"


@dataclass
class ProxyTestResult:
    proxy: str
    target: str
    method: str  # "requests" or "playwright"
    tcp_connect: bool
    http_status: Optional[int]
    connect_time_ms: Optional[float]
    response_time_ms: Optional[float]
    final_url: str
    content_length: int
    has_overview_module: bool
    error_type: str
    error_message: str


def parse_proxy_url(proxy: str) -> dict:
    """Parse proxy into components."""
    parsed = urlparse(f"http://{proxy}")
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "username": parsed.username,
        "password": parsed.password,
    }


def test_with_requests(proxy: str, target_url: str, timeout: int = 15) -> ProxyTestResult:
    """Test proxy with Python requests."""
    parsed = parse_proxy_url(proxy)
    proxy_auth = f"{parsed['username']}:{parsed['password']}@"
    proxy_url = f"{parsed['scheme']}://{proxy_auth}{parsed['host']}:{parsed['port']}"
    proxies = {"http": proxy_url, "https": proxy_url}

    result = ProxyTestResult(
        proxy=mask_proxy(proxy),
        target=target_url,
        method="requests",
        tcp_connect=False,
        http_status=None,
        connect_time_ms=None,
        response_time_ms=None,
        final_url="",
        content_length=0,
        has_overview_module=False,
        error_type="",
        error_message="",
    )

    try:
        start = time.time()
        resp = requests.get(target_url, proxies=proxies, timeout=timeout, allow_redirects=True)
        result.connect_time_ms = (time.time() - start) * 1000
        result.tcp_connect = True
        result.http_status = resp.status_code
        result.response_time_ms = (time.time() - start) * 1000
        result.final_url = resp.url
        result.content_length = len(resp.content)
        result.has_overview_module = "overview-module" in resp.text

        if resp.status_code >= 400:
            result.error_type = f"HTTP_{resp.status_code}"
            result.error_message = f"HTTP {resp.status_code}"

    except requests.exceptions.ConnectTimeout:
        result.error_type = "ConnectTimeout"
        result.error_message = "TCP connection timeout"
    except requests.exceptions.ReadTimeout:
        result.error_type = "ReadTimeout"
        result.error_message = "Read timeout"
    except requests.exceptions.ProxyError as e:
        result.error_type = "ProxyError"
        result.error_message = str(e)
    except requests.exceptions.SSLError as e:
        result.error_type = "SSLError"
        result.error_message = str(e)
    except Exception as e:
        result.error_type = type(e).__name__
        result.error_message = str(e)

    return result


async def test_with_playwright(proxy: str, target_url: str, timeout: int = 20000) -> ProxyTestResult:
    """Test proxy with Playwright."""
    parsed = parse_proxy_url(proxy)
    proxy_cfg = {
        "server": f"{parsed['scheme']}://{parsed['host']}:{parsed['port']}",
        "username": parsed['username'],
        "password": parsed['password'],
    }

    result = ProxyTestResult(
        proxy=mask_proxy(proxy),
        target=target_url,
        method="playwright",
        tcp_connect=False,
        http_status=None,
        connect_time_ms=None,
        response_time_ms=None,
        final_url="",
        content_length=0,
        has_overview_module=False,
        error_type="",
        error_message="",
    )

    playwright = None
    browser = None
    context = None

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(proxy=proxy_cfg)
        page = await context.new_page()

        start = time.time()
        try:
            response = await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout)
            result.connect_time_ms = (time.time() - start) * 1000
            result.tcp_connect = True

            if response:
                result.http_status = response.status

            result.response_time_ms = (time.time() - start) * 1000
            result.final_url = page.url
            html = await page.content()
            result.content_length = len(html)
            result.has_overview_module = "overview-module" in html

            if response and response.status >= 400:
                result.error_type = f"HTTP_{response.status}"
                result.error_message = f"HTTP {response.status}"

        except Exception as e:
            result.response_time_ms = (time.time() - start) * 1000
            result.error_type = type(e).__name__
            result.error_message = str(e)

    except Exception as e:
        result.error_type = type(e).__name__
        result.error_message = str(e)
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass

    return result


def print_result(r: ProxyTestResult):
    status = f"HTTP {r.http_status}" if r.http_status else "N/A"
    overview = "✓" if r.has_overview_module else "✗"
    connect = f"{r.connect_time_ms:.0f}" if r.connect_time_ms else "N/A"
    resp = f"{r.response_time_ms:.0f}" if r.response_time_ms else "N/A"
    print(f"  {r.method:10} | {status:8} | connect={connect}ms | resp={resp}ms | len={r.content_length:6} | overview-module={overview} | {r.error_type or 'OK'}")


async def main():
    print("=" * 120)
    print("PROXY DIAGNOSTIC: Testing 5 proxies against 4 targets (Tofler.in)")
    print("=" * 120)

    for proxy in TEST_PROXIES:
        print(f"\n{'='*120}")
        print(f"PROXY: {mask_proxy(proxy)}")
        print(f"{'='*120}")
        print(f"{'Method':10} | {'Status':8} | Connect(ms) | Resp(ms) | Length | Overview | Error")

        for target_name, target_url in TARGETS.items():
            print(f"\n  Target: {target_name} ({target_url})")

            # Test with requests
            r1 = test_with_requests(proxy, target_url)
            print_result(r1)

            # Test with Playwright
            r2 = await test_with_playwright(proxy, target_url)
            print_result(r2)

    print(f"\n{'='*120}")
    print("SUMMARY TABLE")
    print(f"{'='*120}")

    # Build summary
    print(f"{'Proxy':30} | {'httpbin HTTP':12} | {'httpbin HTTPS':12} | {'Tofler Home':12} | {'Tofler Company':12} | Diagnosis")
    print("-" * 120)

    for proxy in TEST_PROXIES:
        masked = mask_proxy(proxy)[:28]
        results = {}

        for target_name, target_url in TARGETS.items():
            r1 = test_with_requests(proxy, target_url)
            r2 = await test_with_playwright(proxy, target_url)

            key = f"{target_name}_requests"
            results[key] = f"HTTP {r1.http_status}" if r1.http_status else r1.error_type

            key = f"{target_name}_playwright"
            results[key] = f"HTTP {r2.http_status}" if r2.http_status else r2.error_type

        # Diagnosis
        httpbin_ok = results.get("httpbin_https_requests") and "200" in str(results.get("httpbin_https_requests"))
        tofler_req = results.get("tofler_company_requests", "")
        tofler_pw = results.get("tofler_company_playwright", "")

        if "200" in str(tofler_req) and "200" in str(tofler_pw):
            diag = "✓ Both work"
        elif "200" in str(tofler_req) and "200" not in str(tofler_pw):
            diag = "✗ Playwright only"
        elif "200" not in str(tofler_req) and "200" in str(tofler_pw):
            diag = "✗ Requests only"
        elif "403" in str(tofler_req) or "403" in str(tofler_pw):
            diag = "✗ 403 Forbidden"
        elif "Timeout" in str(tofler_req) or "Timeout" in str(tofler_pw):
            diag = "✗ Timeout"
        elif "ProxyError" in str(tofler_req) or "ProxyError" in str(tofler_pw):
            diag = "✗ Proxy transport"
        else:
            diag = f"? Req:{tofler_req} PW:{tofler_pw}"

        print(f"{masked:30} | {results.get('httpbin_http_requests','?'):12} | {results.get('httpbin_https_requests','?'):12} | {results.get('tofler_home_requests','?'):12} | {results.get('tofler_company_requests','?'):12} | {diag}")


if __name__ == "__main__":
    asyncio.run(main())