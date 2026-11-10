"""Browser-backed fetcher for JavaScript-only sites. Optional: needs the `browser` extra
(`pip install -e ".[browser]"` then `playwright install chromium`). It is never the
default path; `FallbackFetcher` invokes it only when static HTTP came back empty."""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from gtm_engine.config.schema import EngineSettings
from gtm_engine.scraping.fetcher import Fetcher, FetchResult, HttpFetcher

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def visible_chars(html: str) -> int:
    return len(" ".join(_TAG_RE.sub(" ", html or "").split()))


def looks_like_js_shell(html: str, min_text_chars: int = 200) -> bool:
    """True when the HTML carries almost no visible text: a bare <div id=root> app shell."""
    return visible_chars(html) < min_text_chars


class PlaywrightFetcher:
    """Renders the page in headless Chromium and returns the post-JS HTML."""

    name = "playwright"

    def __init__(self, settings: EngineSettings):
        self.settings = settings
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._browser is None:
            from playwright.async_api import async_playwright  # imported lazily: optional extra
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        return self._browser

    async def get(self, url: str, **_) -> FetchResult:
        async with self._lock:  # one page at a time: this is a fallback, not a crawler
            try:
                browser = await self._ensure()
                page = await browser.new_page(user_agent=self.settings.user_agent)
                try:
                    resp = await page.goto(url, wait_until="networkidle",
                                           timeout=int(self.settings.request_timeout_s * 1000))
                    html = await page.content()
                    status = resp.status if resp else 0
                    return FetchResult(url, page.url, status, html, "text/html; rendered=playwright")
                finally:
                    await page.close()
            except Exception as exc:  # noqa: BLE001 - playwright raises many concrete types
                return FetchResult(url, url, 0, "", "", error=f"browser_error:{type(exc).__name__}")

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            await self._pw.stop()
            self._browser = self._pw = None


class FallbackFetcher:
    """Static HTTP first; a browser only when the static result is unusable and the host
    is a company website (never for APIs, robots-blocked URLs, or non-HTML responses)."""

    def __init__(self, primary: HttpFetcher, browser: Fetcher | None, settings: EngineSettings):
        self.primary = primary
        self.browser = browser
        self.settings = settings
        self.fallbacks = 0
        self._tried_hosts: set[str] = set()

    async def get(self, url: str, **kwargs) -> FetchResult:
        result = await self.primary.get(url, **kwargs)
        if self.browser is None or kwargs.get("api"):
            return result
        if result.error == "robots_disallowed":
            return result
        needs_browser = (not result.ok and result.status_code in (0, 403)) or (
            result.ok and result.is_html and looks_like_js_shell(result.text)
        )
        host = urlparse(url).netloc.lower()
        if not needs_browser or host in self._tried_hosts and not result.ok:
            return result
        self._tried_hosts.add(host)
        rendered = await self.browser.get(url)
        if rendered.ok and (not looks_like_js_shell(rendered.text) or visible_chars(rendered.text) >= visible_chars(result.text) + 100):
            self.fallbacks += 1
            log.info("browser fallback rendered %s (%d chars)", url, len(rendered.text))
            return rendered
        return result

    async def close(self) -> None:
        await self.primary.close()
        if self.browser is not None:
            await self.browser.close()

    async def __aenter__(self) -> "FallbackFetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


def build_fetcher(settings: EngineSettings) -> HttpFetcher | FallbackFetcher:
    http = HttpFetcher(settings)
    if not settings.enable_browser_fallback:
        return http
    try:
        import playwright  # noqa: F401
    except ImportError:
        log.warning("enable_browser_fallback is on but playwright is not installed; static only")
        return http
    return FallbackFetcher(http, PlaywrightFetcher(settings), settings)
