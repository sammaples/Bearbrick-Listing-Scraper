"""One full scan: search every site, judge every listing, remember, report."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from .config import Config
from .models import Contents, Listing, Source, Status, Verdict
from .rules import could_match, evaluate
from .sources import buyee, ebay, mercari_us
from .sources.base import SourceRun
from .sources.browser import browser_context, fetch_html
from .store import Store
from .textparse import analyze, is_relevant

log = logging.getLogger("scout")


@dataclass
class Judged:
    listing: Listing
    contents: Contents
    verdict: Verdict


@dataclass
class ScanResult:
    run_id: int
    store: Store
    sources: list[SourceRun]
    new: list[Judged] = field(default_factory=list)
    dropped: list[Judged] = field(default_factory=list)
    judged: int = 0


def _analysis_text(listing: Listing) -> str:
    return " ".join(t for t in (listing.title, listing.title_original) if t)


def _merge_cached(listing: Listing, cached: Listing | None) -> None:
    """Reuses detail-page data from earlier runs; price/bids come fresh."""
    if not cached or not cached.detail_loaded:
        return
    listing.description = listing.description or cached.description
    if len(cached.image_urls) > len(listing.image_urls):
        listing.image_urls = cached.image_urls
    listing.condition = listing.condition or cached.condition
    if listing.shipping_usd is None:
        listing.shipping_usd = cached.shipping_usd
    listing.detail_loaded = True


async def _enrich(listing: Listing, ebay_client, browser_ctx, mercari_ctx) -> None:
    try:
        if listing.source is Source.EBAY:
            ebay.enrich(ebay_client, listing)
        elif listing.source in (Source.BUYEE_YAHOO, Source.BUYEE_MERCARI):
            await buyee.enrich(browser_ctx, listing)
        elif listing.source is Source.MERCARI_US and mercari_ctx is not None:
            await mercari_us.enrich(mercari_ctx, listing)
    except Exception as e:  # one bad page must not sink the run
        log.warning("detail fetch failed for %s: %s", listing.key, e)


async def run_scan(cfg: Config) -> ScanResult:
    store = Store(cfg.db_path)
    run_id = store.start_run()
    runs: list[SourceRun] = []
    src = cfg.sources

    ebay_client = ebay.make_client(cfg.zip_code)
    if src.get("ebay") and src["ebay"].enabled:
        if ebay_client is None:
            runs.append(SourceRun("eBay", ok=False, message="skipped: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set"))
        else:
            try:
                runs.append(ebay.search(ebay_client, src["ebay"].queries, src["ebay"].pages))
            except Exception as e:
                runs.append(SourceRun("eBay", ok=False, message=f"error: {e}"))

    if src.get("mercari_us") and src["mercari_us"].enabled:
        try:
            runs.append(await mercari_us.search(src["mercari_us"].queries))
        except Exception as e:
            runs.append(SourceRun(Source.MERCARI_US.label, ok=False, message=f"error: {e}"))

    result = ScanResult(run_id, store, runs)
    async with AsyncExitStack() as stack:
        ctx = await stack.enter_async_context(browser_context())
        for name, source in (("buyee_yahoo", Source.BUYEE_YAHOO), ("buyee_mercari", Source.BUYEE_MERCARI)):
            if src.get(name) and src[name].enabled:
                try:
                    runs.append(await buyee.search(ctx, source, src[name].queries, src[name].pages))
                except Exception as e:
                    runs.append(SourceRun(source.label, ok=False, message=f"error: {e}"))

        mercari_ctx = None
        if any(r.name == Source.MERCARI_US.label and r.listings for r in runs):
            # Item pages load more reliably from a session that warmed up on the homepage.
            mercari_ctx = await stack.enter_async_context(browser_context())
            _, _, home = await fetch_html(mercari_ctx, mercari_us.BASE + "/", settle_ms=10000)
            await home.close()

        for run in runs:
            fetched = 0
            for listing in run.listings:
                if not is_relevant(_analysis_text(listing)):
                    continue
                _merge_cached(listing, store.cached_listing(listing.key))
                contents = analyze(_analysis_text(listing), listing.description, cfg.exclude_keywords)
                if (
                    not listing.detail_loaded
                    and fetched < cfg.max_detail_fetches
                    and could_match(listing, contents, cfg.caps)
                ):
                    await _enrich(listing, ebay_client, ctx, mercari_ctx)
                    fetched += 1
                    contents = analyze(_analysis_text(listing), listing.description, cfg.exclude_keywords)
                verdict = evaluate(listing, contents, cfg.caps)
                if not listing.detail_loaded and verdict.status is not Status.REJECT:
                    verdict.status = Status.REVIEW
                    verdict.reasons.insert(0, "listing details not loaded yet")
                event = store.save(run_id, listing, contents, verdict)
                result.judged += 1
                judged = Judged(listing, contents, verdict)
                if event == "new":
                    result.new.append(judged)
                elif event == "dropped":
                    result.dropped.append(judged)

    store.finish_run(run_id, [{"name": r.name, "ok": r.ok, "found": r.found, "message": r.message} for r in runs])
    return result
