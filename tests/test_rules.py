"""Rule tests use made-up caps; real budgets live in the git-ignored config."""

from bearbrick_scout.config import Caps
from bearbrick_scout.models import Contents, Listing, ListingType, Source, Status
from bearbrick_scout.rules import compute_cap, could_match, evaluate

CAPS = Caps(
    set_400_us=100, set_400_us_ebay_best_offer=120, set_400_us_mercari=120, set_400_intl=80,
    loose_100_us=5, loose_100_intl=3, figure_1000_us=350, figure_1000_intl=None,
)


def L(source=Source.EBAY, price=90.0, ship=5.0, where="US", kind=ListingType.BUY_IT_NOW, bo=False):
    return Listing(source=source, source_id="1", url="u", title="t", price_usd=price, shipping_usd=ship,
                   ships_from=where, listing_type=kind, best_offer=bo, detail_loaded=True)


def C(sets=0, loose=0, k=0, box="yes", confident=True):
    return Contents(sets_400=sets, loose_100=loose, figures_1000=k, box=box, confident=confident)


def test_owner_example_set_plus_three_100s_in_us():
    cap, _, _ = compute_cap(L(), C(sets=1, loose=3), CAPS)
    assert cap == 115


def test_same_bundle_outside_us():
    cap, _, _ = compute_cap(L(where="JP"), C(sets=1, loose=3), CAPS)
    assert cap == 89


def test_us_400_counts_shipping():
    assert evaluate(L(price=96, ship=4), C(sets=1), CAPS).status is Status.MATCH
    assert evaluate(L(price=96, ship=5), C(sets=1), CAPS).status is Status.REJECT


def test_intl_ignores_shipping():
    v = evaluate(L(source=Source.BUYEE_MERCARI, price=80, ship=None, where="JP"), C(sets=1), CAPS)
    assert v.status is Status.MATCH and v.total_usd == 80


def test_ebay_best_offer_bin_gets_higher_cap():
    assert evaluate(L(price=110, ship=10, bo=True), C(sets=1), CAPS).status is Status.MATCH
    assert evaluate(L(price=110, ship=10, bo=False), C(sets=1), CAPS).status is Status.REJECT


def test_best_offer_flag_on_auction_does_not_raise_cap():
    v = evaluate(L(price=110, ship=0, bo=True, kind=ListingType.AUCTION), C(sets=1), CAPS)
    assert v.status is Status.REJECT


def test_best_offer_cap_only_inside_us():
    v = evaluate(L(price=85, ship=0, bo=True, where="GB"), C(sets=1), CAPS)
    assert v.status is Status.REJECT


def test_mercari_us_gets_higher_cap_but_mercari_jp_does_not():
    assert evaluate(L(source=Source.MERCARI_US, price=115, ship=5), C(sets=1), CAPS).status is Status.MATCH
    jp = L(source=Source.BUYEE_MERCARI, price=115, ship=None, where="JP")
    assert evaluate(jp, C(sets=1), CAPS).status is Status.REJECT


def test_auction_current_bid_plus_shipping():
    assert evaluate(L(price=90, ship=9, kind=ListingType.AUCTION), C(sets=1), CAPS).status is Status.MATCH
    assert evaluate(L(price=90, ship=11, kind=ListingType.AUCTION), C(sets=1), CAPS).status is Status.REJECT


def test_1000_us_only():
    assert evaluate(L(price=340, ship=10), C(k=1), CAPS).status is Status.MATCH
    assert evaluate(L(price=340, ship=11), C(k=1), CAPS).status is Status.REJECT
    assert evaluate(L(price=100, where="JP"), C(k=1), CAPS).status is Status.REJECT


def test_loose_100_per_piece():
    assert evaluate(L(price=50, ship=10), C(loose=12), CAPS).status is Status.MATCH  # 60 <= 12*5
    assert evaluate(L(price=37, where="JP"), C(loose=12), CAPS).status is Status.REJECT  # > 12*3


def test_box_required_for_400_and_1000_not_100():
    assert evaluate(L(), C(sets=1, box="no"), CAPS).status is Status.REJECT
    v = evaluate(L(), C(sets=1, box="unknown"), CAPS)
    assert v.status is Status.REVIEW and any("box" in r for r in v.reasons)
    assert evaluate(L(price=20, ship=0), C(loose=5, box="unknown"), CAPS).status is Status.MATCH


def test_unknown_shipping_goes_to_review_in_us():
    v = evaluate(L(price=80, ship=None), C(sets=1), CAPS)
    assert v.status is Status.REVIEW
    assert evaluate(L(price=101, ship=None), C(sets=1), CAPS).status is Status.REJECT


def test_unclear_count_goes_to_review():
    assert evaluate(L(price=4, ship=0), C(loose=1, confident=False), CAPS).status is Status.REVIEW


def test_could_match_is_optimistic_but_prunes_obvious_rejects():
    assert could_match(L(price=99, ship=None), C(sets=1, box="unknown"), CAPS)
    assert not could_match(L(price=150, ship=None), C(sets=1), CAPS)
    assert could_match(L(price=150, ship=None), C(loose=1, confident=False), CAPS)


def test_auction_alerts_only_near_the_end():
    from datetime import datetime, timedelta, timezone

    from bearbrick_scout.scan import alert_due

    soon = L(kind=ListingType.AUCTION)
    soon.ends_at = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    later = L(kind=ListingType.AUCTION)
    later.ends_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert alert_due(soon, 12) and not alert_due(later, 12)
    assert alert_due(L(), 12)  # Buy It Now alerts right away
