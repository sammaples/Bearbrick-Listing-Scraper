"""Buyee: Yahoo Auctions Japan and Mercari Japan.

Everything here ships from Japan, so the outside-the-US rules apply (item
price only). Buyee prints a USD conversion next to every yen price; we use it.
"""

from __future__ import annotations

import re
import urllib.parse

from selectolax.lexbor import LexborHTMLParser

from ..models import Listing, ListingType, Source
from .base import SourceRun, dedupe
from .browser import fetch_html, looks_blocked

BASE = "https://buyee.jp"


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text()).strip() if node else ""


def _usd(fx_text: str) -> float | None:
    m = re.search(r"US\$\s*([\d,]+(?:\.\d+)?)", fx_text or "")
    return float(m.group(1).replace(",", "")) if m else None


def yahoo_search_url(query: str, page: int = 1) -> str:
    url = f"{BASE}/item/search/query/{urllib.parse.quote(query)}"
    return url if page == 1 else f"{url}?page={page}"


def mercari_search_url(query: str, page: int = 1) -> str:
    params = {"keyword": query}
    if page > 1:
        params["page"] = str(page)
    return f"{BASE}/mercari/search?{urllib.parse.urlencode(params)}"


def parse_yahoo_search(html: str) -> list[Listing]:
    tree = LexborHTMLParser(html)
    out = []
    for card in tree.css("li.itemCard"):
        link = card.css_first("a[href*='/item/']")
        if not link:
            continue
        href = link.attributes.get("href") or ""
        m = re.search(r"/item/[\w]+/[\w]+/([\w-]+)", href) or re.search(r"/item/yahoo/[\w]+/([\w-]+)", href)
        if not m:
            continue
        img = card.css_first("img")
        original = (img.attributes.get("alt") if img else None) or None
        name = _text(card.css_first(".itemCard__itemName")) or original or ""
        price_node = card.css_first(".g-price")
        usd = _usd(_text(card.css_first(".g-priceFx")))
        if usd is None:
            continue
        info = {
            _text(li.css_first(".g-title")): _text(li.css_first(".g-text"))
            for li in card.css(".itemCard__infoItem")
            if li.css_first(".g-title")
        }
        bids = info.get("Number of Bids")
        out.append(
            Listing(
                source=Source.BUYEE_YAHOO,
                source_id=m.group(1),
                url=urllib.parse.urljoin(BASE, href.split("?")[0]),
                title=name,
                title_original=original if original and original != name else None,
                price_usd=usd,
                shipping_usd=None,
                ships_from="JP",
                listing_type=ListingType.AUCTION,
                price_original=_text(price_node) or None,
                image_urls=[img.attributes.get("src")] if img and img.attributes.get("src") else [],
                bid_count=int(bids) if bids and bids.isdigit() else None,
                ends_at=info.get("Time Remaining"),
            )
        )
    return out


def parse_mercari_search(html: str) -> list[Listing]:
    tree = LexborHTMLParser(html)
    out = []
    for li in tree.css("ul.item-lists li.list"):
        link = li.css_first("a[href*='/mercari/item/']")
        if not link:
            continue
        href = link.attributes.get("href") or ""
        m = re.search(r"/mercari/item/(\w+)", href)
        usd = _usd(_text(li.css_first(".price-fx")))
        if not m or usd is None:
            continue
        img = li.css_first("img")
        original = img.attributes.get("alt") if img else None
        name = _text(li.css_first(".name")) or original or ""
        src = (img.attributes.get("src") or "") if img else ""
        out.append(
            Listing(
                source=Source.BUYEE_MERCARI,
                source_id=m.group(1),
                url=f"{BASE}/mercari/item/{m.group(1)}",
                title=name,
                title_original=original if original and original != name else None,
                price_usd=usd,
                shipping_usd=None,
                ships_from="JP",
                listing_type=ListingType.BUY_IT_NOW,
                price_original=_text(li.css_first(".price")) or None,
                image_urls=[urllib.parse.urljoin("https:", src)] if src else [],
            )
        )
    return out


def _label_value(tree: LexborHTMLParser, label: str) -> str | None:
    """Finds "<x class=g-title>Item Condition</x> <y>value</y>"-style pairs."""
    for node in tree.css(".g-title, dt, th, .title, h4"):
        if _text(node).lower() == label.lower():
            sib = node.next
            while sib is not None and not _text(sib):
                sib = sib.next
            if sib is not None:
                return _text(sib)
    return None


def parse_yahoo_detail(html: str) -> dict:
    tree = LexborHTMLParser(html)
    images = re.findall(r"https://cdnyauction\.buyee\.jp/image/[^\"'\s?]+?\.(?:jpe?g|png)", html)
    return {
        "image_urls": dedupe(images),
        "condition": _label_value(tree, "Item Condition"),
        "description_url": f"{BASE}" + m.group(1) if (m := re.search(r'<iframe[^>]+src="(/item/[^"#]+/detail)', html)) else None,
    }


def parse_mercari_detail(html: str) -> dict:
    tree = LexborHTMLParser(html)
    images = [a.attributes.get("href") or "" for a in tree.css("a.js-smartPhoto")]
    images += re.findall(r"https://static\.mercdn\.net/item/detail/orig/photos/m\d+_\d+\.jpg", html)
    images = [i.split("?")[0] for i in images if "mercdn.net/item/detail" in i]
    return {
        "image_urls": dedupe(images),
        "condition": _label_value(tree, "Item Condition"),
        "description_url": f"{BASE}" + m.group(1) if (m := re.search(r'<iframe[^>]+src="(/mercari/item/description/[^"#]+)', html)) else None,
    }


def parse_description(html: str) -> str:
    tree = LexborHTMLParser(html)
    for n in tree.css("script, style"):
        n.decompose()
    body = tree.body
    return _text(body) if body else ""


async def search(ctx, source: Source, queries: list[str], pages: int) -> SourceRun:
    run = SourceRun(source.label)
    build = yahoo_search_url if source is Source.BUYEE_YAHOO else mercari_search_url
    parse = parse_yahoo_search if source is Source.BUYEE_YAHOO else parse_mercari_search
    seen: dict[str, Listing] = {}
    for q in queries:
        for p in range(1, pages + 1):
            status, html, page = await fetch_html(ctx, build(q, p), settle_ms=4000)
            await page.close()
            if looks_blocked(status, html):
                run.ok = False
                run.message = f"blocked (HTTP {status})"
                break
            items = parse(html)
            for it in items:
                seen.setdefault(it.source_id, it)
            if not items:
                break
    run.listings = list(seen.values())
    run.found = len(run.listings)
    if run.ok and not run.found:
        run.message = "no results parsed (page layout may have changed)"
    return run


async def enrich(ctx, listing: Listing) -> Listing:
    status, html, page = await fetch_html(ctx, listing.url, settle_ms=2500)
    await page.close()
    if looks_blocked(status, html):
        return listing
    info = parse_yahoo_detail(html) if listing.source is Source.BUYEE_YAHOO else parse_mercari_detail(html)
    if info["image_urls"]:
        listing.image_urls = dedupe(info["image_urls"])
    listing.condition = info["condition"] or listing.condition
    if info["description_url"]:
        _, desc_html, page = await fetch_html(ctx, info["description_url"], settle_ms=1000)
        await page.close()
        listing.description = parse_description(desc_html)[:8000]
    listing.detail_loaded = True
    return listing

