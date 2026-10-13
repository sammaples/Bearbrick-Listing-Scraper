"""Runs the photo check with several models on the same stored listings.

Usage: python -m bearbrick_scout.compare_models [--limit 20] [--models a,b,c]
Prints each model's answer per listing and the total cost, so the model
choice in config (photo_check.model) can be made on real data.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import anthropic
import httpx

from . import vision
from .config import load_config
from .models import Listing
from .store import Store

# USD per million tokens (input, output). Check current pricing before relying on these.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


class Metered:
    """Wraps the client to total up token usage per model."""

    def __init__(self, client: anthropic.AsyncAnthropic):
        self.client, self.tokens = client, {}
        self.messages = self

    async def parse(self, **kw):
        resp = await self.client.messages.parse(**kw)
        i, o = self.tokens.get(kw["model"], (0, 0))
        self.tokens[kw["model"]] = (i + resp.usage.input_tokens, o + resp.usage.output_tokens)
        return resp


async def main(limit: int, models: list[str]) -> None:
    cfg = load_config()
    rows = Store(cfg.db_path).db.execute(
        "SELECT listing_json FROM listings WHERE status IN ('match','review') ORDER BY last_seen_run DESC"
    ).fetchall()
    listings = [Listing.from_dict(json.loads(r[0])) for r in rows]
    listings = [l for l in listings if l.image_urls and l.detail_loaded][:limit]
    async with anthropic.AsyncAnthropic() as raw, httpx.AsyncClient() as http:
        client = Metered(raw)
        for l in listings:
            print(f"\n{l.title[:80]}\n  {l.url}")
            results = await asyncio.gather(*(vision.check(client, http, l, m) for m in models))
            for m, r in zip(models, results):
                if r is None:
                    print(f"  {m:<20} (no answer)")
                    continue
                print(f"  {m:<20} box={r['box_visible']:<10} 400={r['count_400']} 100={r['count_100_loose']}"
                      f" 1000={r['count_1000']} sure={r['counts_confident']}  {r['box_evidence'][:60]}")
    print("\nCost for this comparison:")
    for m in models:
        i, o = client.tokens.get(m, (0, 0))
        pin, pout = PRICES.get(m, (0, 0))
        cost = i / 1e6 * pin + o / 1e6 * pout
        print(f"  {m:<20} ${cost:.3f}  (${cost / max(len(listings), 1):.4f} per listing)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--models", default=",".join(PRICES))
    a = ap.parse_args()
    asyncio.run(main(a.limit, a.models.split(",")))
