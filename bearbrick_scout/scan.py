"""One full scan: search every site, judge every listing, remember, report."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

import anthropic
import httpx

from .config import Config
from .models import Contents, Listing, ListingType, Source, Status, Verdict
from .rules import could_match, evaluate
from .sources import buyee, ebay, mercari_us
from .sources.base import SourceRun
from .sources.browser import browser_context, fetch_html
from .store import Store
from .textparse import analyze, is_relevant
from . import vision

log = logging.getLogger("scout")

DETAIL_CONCURRENCY = 4


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
    alerts: list[Judged] = field(default_factory=list)  # due a phone alert now
    judged: int = 0
    photo_checks: int = 0


def alert_due(listing: Listing, auction_hours: float) -> bool:
    """Auctions alert only near the end, when the current bid means something."""
    if listing.listing_type is not ListingType.AUCTION:
        return True
    left = listing.hours_left()
    return left is None or left <= auction_hours


def _judge(listing: Listing, cfg: Config) -> Judged:
    contents = analyze(_analysis_text(listing), listing.description, cfg.exclude_keywords)
    if listing.photo_check:
        vision.apply(contents, listing.photo_check)
    verdict = evaluate(listing, contents, cfg.caps)
    if not listing.detail_loaded and verdict.status is not Status.REJECT:
        verdict.status = Status.REVIEW
        verdict.reasons.insert(0, "listing details not loaded yet")
    return Judged(listing, contents, verdict)


def _needs_photo_check(j: Judged) -> bool:
    """Within budget, details loaded, but box or count still open."""
    return (
        j.verdict.status is Status.REVIEW
        and j.listing.detail_loaded
        and j.listing.photo_check is None
        and bool(j.listing.image_urls)
        and ((j.contents.needs_box and j.contents.box == "unknown") or not j.contents.confident)
    )


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
    listing.photo_check = listing.photo_check or cached.photo_check
    listing.detail_loaded = True


async def _enrich(listing: Listing, ebay_client, browser_ctx, mercari_ctx) -> None:
    try:
        if listing.source is Source.EBAY:
            await asyncio.to_thread(ebay.enrich, ebay_client, listing)
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
    photo_budget = [cfg.max_photo_checks]
    async with AsyncExitStack() as stack:
        ctx = await stack.enter_async_context(browser_context())
        vclient = vhttp = None
        if vision.enabled():
            vclient = await stack.enter_async_context(anthropic.AsyncAnthropic())
            vhttp = await stack.enter_async_context(httpx.AsyncClient())
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
            # Phase 1: cheap title-only judgement, pick which listings need a detail fetch.
            todo: list[Listing] = []
            for listing in run.listings:
                if not is_relevant(_analysis_text(listing)):
                    continue
                _merge_cached(listing, store.cached_listing(listing.key))
                contents = analyze(_analysis_text(listing), listing.description, cfg.exclude_keywords)
                if (
                    not listing.detail_loaded
                    and len(todo) < cfg.max_detail_fetches
                    and could_match(listing, contents, cfg.caps)
                ):
                    todo.append(listing)

            # Phase 2: fetch detail pages a few at a time (one at a time on Mercari US,
            # which is quick to show a bot check).
            sem = asyncio.Semaphore(1 if run.name == Source.MERCARI_US.label else DETAIL_CONCURRENCY)

            async def fetch(listing: Listing) -> None:
                async with sem:
                    await _enrich(listing, ebay_client, ctx, mercari_ctx)

            await asyncio.gather(*(fetch(l) for l in todo))

            # Phase 3: judge with everything we know.
            judged_run: list[Judged] = []
            for listing in run.listings:
                if not is_relevant(_analysis_text(listing)):
                    continue
                judged_run.append(_judge(listing, cfg))

            # Phase 4: photo check where the text left the box or count open.
            want = [j for j in judged_run if _needs_photo_check(j)]
            if want and vision.enabled() and photo_budget[0] > 0:
                want = want[: photo_budget[0]]
                photo_budget[0] -= len(want)
                vsem = asyncio.Semaphore(4)

                async def look(j: Judged) -> None:
                    async with vsem:
                        j.listing.photo_check = await vision.check(vclient, vhttp, j.listing, cfg.vision_model)

                await asyncio.gather(*(look(j) for j in want))
                checked = {id(j) for j in want}
                judged_run = [_judge(j.listing, cfg) if id(j) in checked else j for j in judged_run]
                result.photo_checks += sum(1 for j in want if j.listing.photo_check)

            # Phase 5: remember, and collect what changed / what to alert.
            for judged in judged_run:
                listing, verdict = judged.listing, judged.verdict
                event = store.save(run_id, listing, judged.contents, verdict)
                result.judged += 1
                if (
                    verdict.status is not Status.REJECT
                    and not store.was_notified(listing.key)
                    and alert_due(listing, cfg.auction_alert_hours)
                ):
                    result.alerts.append(judged)
                if event == "new":
                    result.new.append(judged)
                elif event == "dropped":
                    result.dropped.append(judged)

    store.finish_run(run_id, [{"name": r.name, "ok": r.ok, "found": r.found, "message": r.message} for r in runs])
    return result
