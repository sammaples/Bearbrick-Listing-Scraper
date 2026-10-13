"""Shared headless Chromium for sites without an API (Buyee, Mercari US)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from playwright.async_api import BrowserContext, Page, async_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
CHALLENGE_TITLES = ("just a moment", "attention required", "access denied")


@asynccontextmanager
async def browser_context():
    async with async_playwright() as p:
        launch: dict = {"args": ["--disable-blink-features=AutomationControlled"]}
        if exe := os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = exe
        if proxy := os.environ.get("SCRAPER_PROXY"):
            # Optional residential proxy if a site blocks datacenter IPs.
            launch["proxy"] = {"server": proxy}
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
        try:
            yield ctx
        finally:
            await browser.close()


async def fetch_html(ctx: BrowserContext, url: str, settle_ms: int = 3000) -> tuple[int, str, Page]:
    """Loads a page, waits for scripts/challenges to settle, returns (status, html, page)."""
    page = await ctx.new_page()
    resp = await page.goto(url, timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(settle_ms)
    return (resp.status if resp else 0), await page.content(), page


def looks_blocked(status: int, html: str) -> bool:
    if status in (403, 429, 503):
        return True
    head = html[:3000].lower()
    return any(f"<title>{t}" in head for t in CHALLENGE_TITLES)
