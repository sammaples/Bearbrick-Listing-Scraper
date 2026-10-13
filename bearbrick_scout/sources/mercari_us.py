"""Mercari US (www.mercari.com), scraped with a real browser.

Mercari sits behind Cloudflare and intermittently challenges datacenter IPs,
so each search warms up on the homepage first and retries in a fresh browser
context. A run that stays blocked is reported, not silently skipped.

The search-card selectors were checked against the live site (Oct 2026). Item
pages could not be loaded from the dev sandbox, so item parsing leans on
schema.org JSON-LD and plain-text patterns; anything not found (e.g. shipping)
stays unknown and routes the listing to "Needs a look".
"""

from __future__ import annotations

import json
import re
import urllib.parse

from selectolax.lexbor import LexborHTMLParser

from ..models import Listing, ListingType, Source
from .base import SourceBlocked, SourceRun, dedupe, parse_money
from .browser import browser_context, fetch_html, looks_blocked

BASE = "https://www.mercari.com"


def search_url(query: str) -> str:
    # sortBy=2 = newest first, so fresh listings are always on the first screen.
    return f"{BASE}/search/?{urllib.parse.urlencode({'keyword': query, 'sortBy': 2})}"


def parse_search(html: str) -> list[Listing]:
    tree = LexborHTMLParser(html)
    out = []
    for a in tree.css("a[data-testid=ProductThumbWrapper]"):
        box = a.css_first("[data-testid=ItemContainer]")
        if box is None or box.attributes.get("data-is-on-sale") == "false":
            continue
        item_id = box.attributes.get("data-productid")
        name = a.css_first("[data-testid=ItemName]")
        price = a.css_first("[data-testid=ProductThumbItemPrice]")
        img = a.css_first("img")
        price_usd = parse_money(price.text() if price else None)
        if not item_id or price_usd is None:
            continue
        title = (name.text().strip() if name else "") or (img.attributes.get("title") if img else "") or ""
        src = ""
        if img is not None:
            srcset = img.attributes.get("srcset") or ""
            src = srcset.split(" ")[0] if srcset else img.attributes.get("src") or ""
        out.append(
            Listing(
                source=Source.MERCARI_US,
                source_id=item_id,
                url=f"{BASE}/us/item/{item_id}/",
                title=title,
                price_usd=price_usd,
                shipping_usd=None,
                ships_from="US",
                listing_type=ListingType.BUY_IT_NOW,
                image_urls=[src.split("?")[0]] if src else [],
            )
        )
    return out


def _json_ld_product(html: str) -> dict:
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                return obj
    return {}


SHIP_FREE_RE = re.compile(r"free\s+shipping", re.I)
SHIP_COST_RE = re.compile(r"(?:shipping[^$]{0,40}\$\s*(\d+(?:\.\d{2})?))|(?:\$\s*(\d+(?:\.\d{2})?)\s*shipping)", re.I)
CONDITION_RE = re.compile(r"\bcondition\b\W{0,5}(new|like new|good|fair|poor)\b", re.I)


def parse_item(html: str, item_id: str) -> dict:
    tree = LexborHTMLParser(html)
    for n in tree.css("script, style"):
        n.decompose()
    text = re.sub(r"\s+", " ", tree.body.text() if tree.body else "")
    product = _json_ld_product(html)

    shipping = None
    if SHIP_FREE_RE.search(text):
        shipping = 0.0
    elif m := SHIP_COST_RE.search(text):
        shipping = float(m.group(1) or m.group(2))

    images = re.findall(rf"https://u-mercari-images\.mercdn\.net/photos/{item_id}_\d+\.jpg", html)
    ld_images = product.get("image") or []
    images += [ld_images] if isinstance(ld_images, str) else list(ld_images)

    cond = None
    offers = product.get("offers") or {}
    if isinstance(offers, dict) and offers.get("itemCondition"):
        cond = str(offers["itemCondition"]).rsplit("/", 1)[-1].replace("Condition", "")
    if not cond and (m := CONDITION_RE.search(text)):
        cond = m.group(1).title()

    return {
        "shipping_usd": shipping,
        "image_urls": dedupe([i.split("?")[0] for i in images]),
        "description": product.get("description") or None,
        "condition": cond,
    }


async def _warm_up(ctx) -> None:
    _, _, home = await fetch_html(ctx, BASE + "/", settle_ms=10000)
    await home.close()


async def _search_page(ctx, url: str) -> str:
    status, html, page = await fetch_html(ctx, url, settle_ms=5000)
    try:
        if looks_blocked(status, html):
            raise SourceBlocked(f"HTTP {status}")
        # Results lazy-load on scroll (about 28 per search).
        for _ in range(4):
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(1200)
        html = await page.content()
    finally:
        await page.close()
    if "data-testid=\"ProductThumbWrapper\"" not in html and "No results" not in html:
        # Page shell loaded but the results request was challenged.
        raise SourceBlocked("results did not load")
    return html


async def search(queries: list[str], attempts: int = 3) -> SourceRun:
    """One warmed-up browser for all queries; a fresh one after a block."""
    run = SourceRun(Source.MERCARI_US.label)
    seen: dict[str, Listing] = {}
    pending = list(queries)
    for _ in range(attempts):
        if not pending:
            break
        async with browser_context() as ctx:
            await _warm_up(ctx)
            retry = []
            for q in pending:
                try:
                    html = await _search_page(ctx, search_url(q))
                except SourceBlocked:
                    retry.append(q)
                    continue
                for it in parse_search(html):
                    seen.setdefault(it.source_id, it)
            pending = retry
    if pending:
        run.ok = False
        run.message = f"blocked by Cloudflare on {len(pending)} of {len(queries)} searches"
    run.listings = list(seen.values())
    run.found = len(run.listings)
    return run


async def enrich(ctx, listing: Listing) -> Listing:
    status, html, page = await fetch_html(ctx, listing.url, settle_ms=4000)
    await page.close()
    if looks_blocked(status, html):
        return listing
    info = parse_item(html, listing.source_id)
    if info["shipping_usd"] is not None:
        listing.shipping_usd = info["shipping_usd"]
    if info["image_urls"]:
        listing.image_urls = dedupe(info["image_urls"] + listing.image_urls)
    listing.description = info["description"] or listing.description
    listing.condition = info["condition"] or listing.condition
    listing.detail_loaded = True
    return listing
