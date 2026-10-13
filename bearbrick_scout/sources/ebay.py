"""eBay via the official Browse API (needs EBAY_CLIENT_ID / EBAY_CLIENT_SECRET).

Shipping is quoted to the owner's ZIP through the X-EBAY-C-ENDUSERCTX header.
"""

from __future__ import annotations

import base64
import os
import re
import urllib.parse

import httpx

from ..models import Listing, ListingType, Source
from .base import SourceRun, dedupe

API = "https://api.ebay.com"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayClient:
    def __init__(self, client_id: str, client_secret: str, zip_code: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.zip_code = zip_code
        self.http = http or httpx.Client(timeout=30)
        self._token: str | None = None

    def _auth(self) -> str:
        if self._token:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = self.http.post(
            f"{API}/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE},
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def _headers(self) -> dict[str, str]:
        ctx = urllib.parse.quote(f"country=US,zip={self.zip_code}")
        return {
            "Authorization": f"Bearer {self._auth()}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "X-EBAY-C-ENDUSERCTX": f"contextualLocation={ctx}",
        }

    def search(self, query: str, limit: int = 200, offset: int = 0) -> list[dict]:
        r = self.http.get(
            f"{API}/buy/browse/v1/item_summary/search",
            headers=self._headers(),
            params={"q": query, "limit": limit, "offset": offset, "sort": "newlyListed"},
        )
        r.raise_for_status()
        return r.json().get("itemSummaries", [])

    def get_item(self, item_id: str) -> dict:
        r = self.http.get(
            f"{API}/buy/browse/v1/item/{urllib.parse.quote(item_id, safe='')}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()


def _money(obj: dict | None) -> float | None:
    if not obj:
        return None
    # For foreign listings eBay returns the USD conversion in "value" and the
    # original in "convertedFromValue".
    try:
        return float(obj["value"])
    except (KeyError, TypeError, ValueError):
        return None


def _shipping(item: dict) -> float | None:
    opts = item.get("shippingOptions") or []
    costs = []
    for o in opts:
        if o.get("shippingCostType") == "CALCULATED" and not o.get("shippingCost"):
            continue
        v = _money(o.get("shippingCost"))
        if v is not None:
            costs.append(v)
    return min(costs) if costs else None


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_listing(item: dict) -> Listing:
    options = item.get("buyingOptions") or []
    is_auction = "AUCTION" in options and "FIXED_PRICE" not in options
    price = _money(item.get("currentBidPrice")) if is_auction else None
    if price is None:
        price = _money(item.get("price")) or 0.0
    images = [(item.get("image") or {}).get("imageUrl")]
    images += [i.get("imageUrl") for i in item.get("additionalImages") or []]
    return Listing(
        source=Source.EBAY,
        source_id=item["itemId"],
        url=item.get("itemWebUrl") or f"https://www.ebay.com/itm/{item['itemId']}",
        title=item.get("title", ""),
        price_usd=price,
        shipping_usd=_shipping(item),
        ships_from=(item.get("itemLocation") or {}).get("country"),
        listing_type=ListingType.AUCTION if is_auction else ListingType.BUY_IT_NOW,
        best_offer="BEST_OFFER" in options,
        condition=item.get("condition"),
        image_urls=dedupe(images),
        bid_count=item.get("bidCount"),
        ends_at=item.get("itemEndDate"),
        seller=(item.get("seller") or {}).get("username"),
    )


def enrich(client: EbayClient, listing: Listing) -> Listing:
    """Adds description, full photo set and ZIP-specific shipping."""
    item = client.get_item(listing.source_id)
    full = to_listing(item)
    listing.description = _strip_html(item.get("description")) or item.get("shortDescription")
    listing.image_urls = dedupe(listing.image_urls + full.image_urls)
    if full.shipping_usd is not None:
        listing.shipping_usd = full.shipping_usd
    listing.condition = full.condition or listing.condition
    listing.detail_loaded = True
    return listing


def make_client(zip_code: str) -> EbayClient | None:
    cid, secret = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
    if not (cid and secret):
        return None
    return EbayClient(cid, secret, zip_code)


def search(client: EbayClient, queries: list[str], pages: int) -> SourceRun:
    run = SourceRun("eBay")
    seen: dict[str, Listing] = {}
    for q in queries:
        for page in range(pages):
            items = client.search(q, offset=page * 200)
            for it in items:
                listing = to_listing(it)
                seen.setdefault(listing.source_id, listing)
            if len(items) < 200:
                break
    run.listings = list(seen.values())
    run.found = len(run.listings)
    return run
