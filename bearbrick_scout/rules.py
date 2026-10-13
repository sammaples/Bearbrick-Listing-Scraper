"""Budget rules: decides match / needs-a-look / reject for one listing.

Owner's rules (values come from config, see config.example.yaml):

* 400% figure, or a 100%+400% set sold together (the companion 100% is free):
    - ships from US:                     price + shipping <= set_400.us
    - eBay US Buy It Now w/ Best Offer:  price + shipping <= set_400.us_ebay_best_offer
    - Mercari US (any listing):          price + shipping <= set_400.us_mercari
    - ships from outside the US:         item price only  <= set_400.intl
* Loose 100% figures: per-piece cap, US (incl. shipping) / intl (price only).
* 1000%: US only, price + shipping <= figure_1000.us.
* Bundles: caps add up (e.g. set + 3 loose 100% in the US = 100 + 3*5).
* Every 400% and 1000% must have its box (confirmed in text or photos).
* Auctions use the current bid.
"""

from __future__ import annotations

from .config import Caps
from .models import Contents, Listing, ListingType, Source, Status, Verdict


def _set_400_cap(listing: Listing, caps: Caps) -> tuple[float, str]:
    if not listing.in_us:
        return caps.set_400_intl, "400% (outside US)"
    if listing.source is Source.MERCARI_US:
        return caps.set_400_us_mercari, "400% (Mercari US)"
    if (
        listing.source is Source.EBAY
        and listing.listing_type is ListingType.BUY_IT_NOW
        and listing.best_offer
    ):
        return caps.set_400_us_ebay_best_offer, "400% (eBay BIN + Best Offer)"
    return caps.set_400_us, "400% (US)"


def compute_cap(listing: Listing, contents: Contents, caps: Caps) -> tuple[float | None, str, list[str]]:
    """Returns (cap, breakdown text, blocking reasons)."""
    total = 0.0
    parts: list[str] = []
    blockers: list[str] = []

    if contents.figures_1000:
        cap_1000 = caps.figure_1000_us if listing.in_us else caps.figure_1000_intl
        if cap_1000 is None:
            blockers.append("1000% only wanted from inside the US")
        else:
            total += contents.figures_1000 * cap_1000
            parts.append(f"{contents.figures_1000}×1000% @ ${cap_1000:g}")

    if contents.sets_400:
        cap_400, label = _set_400_cap(listing, caps)
        total += contents.sets_400 * cap_400
        parts.append(f"{contents.sets_400}×{label} @ ${cap_400:g}")

    if contents.loose_100:
        cap_100 = caps.loose_100_us if listing.in_us else caps.loose_100_intl
        total += contents.loose_100 * cap_100
        parts.append(f"{contents.loose_100}×100% @ ${cap_100:g}")

    if blockers or not parts:
        return None, " + ".join(parts), blockers
    return total, " + ".join(parts) + f" = ${total:g}", blockers


def cost_to_compare(listing: Listing) -> tuple[float | None, str]:
    """US listings count price + shipping; outside the US, item price only."""
    if not listing.in_us:
        return listing.price_usd, "item price (outside US)"
    if listing.shipping_usd is None:
        return None, "shipping unknown"
    return listing.price_usd + listing.shipping_usd, "price + shipping"


def evaluate(listing: Listing, contents: Contents, caps: Caps) -> Verdict:
    if contents.excluded_reason:
        return Verdict(Status.REJECT, None, None, "", [contents.excluded_reason])
    if contents.is_empty:
        return Verdict(Status.REJECT, None, None, "", contents.notes or ["not a tracked size"])

    cap, rule, blockers = compute_cap(listing, contents, caps)
    if blockers:
        return Verdict(Status.REJECT, None, None, rule, blockers)
    assert cap is not None

    if contents.needs_box and contents.box == "no":
        return Verdict(Status.REJECT, None, cap, rule, ["listing says no box"])

    cost, cost_label = cost_to_compare(listing)
    reasons: list[str] = []

    if cost is None:
        # US listing with unresolved shipping. If the item alone already
        # busts the cap, reject; otherwise a human should check shipping.
        if listing.price_usd > cap:
            return Verdict(Status.REJECT, listing.price_usd, cap, rule, ["over budget before shipping"])
        reasons.append("shipping cost unknown, check before buying")
    elif cost > cap:
        return Verdict(Status.REJECT, cost, cap, rule, [f"over budget ({cost_label} ${cost:.2f} > ${cap:g})"])

    if contents.needs_box and contents.box == "unknown":
        reasons.append("box not confirmed in description or photos")
    if not contents.confident:
        reasons.append("piece count unclear")

    status = Status.REVIEW if reasons else Status.MATCH
    if listing.listing_type is ListingType.AUCTION:
        reasons.append("auction: price is the current bid")
    if listing.best_offer and listing.listing_type is ListingType.BUY_IT_NOW:
        reasons.append("Best Offer available")
    return Verdict(status, cost if cost is not None else listing.price_usd, cap, rule, reasons)


def could_match(listing: Listing, contents: Contents, caps: Caps) -> bool:
    """Cheap pre-check on search-result data, before fetching the detail page.

    Optimistic: assumes free shipping and a box. Only listings that pass are
    worth a detail fetch (description, photos, shipping).
    """
    if contents.excluded_reason or contents.is_empty:
        return False
    cap, _, blockers = compute_cap(listing, contents, caps)
    if blockers or cap is None:
        return False
    if contents.needs_box and contents.box == "no":
        return False
    if not contents.confident:
        # Unknown count could mean a big bundle with a bigger cap.
        return True
    return listing.price_usd <= cap
