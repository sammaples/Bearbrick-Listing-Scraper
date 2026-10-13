"""Photo-check plumbing, with a stand-in for the Claude API."""

import asyncio
import io
from types import SimpleNamespace

import httpx
from PIL import Image

from bearbrick_scout import vision
from bearbrick_scout.models import Contents, Listing, ListingType, Source


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2000, 1500), "red").save(out, format="JPEG")
    return out.getvalue()


class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed, self.stop_reason, self.calls = parsed, stop_reason, []

    async def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.parsed)


def _listing(n_images=10):
    return Listing(source=Source.BUYEE_YAHOO, source_id="x1", url="u", title="BE@RBRICK 400%",
                   price_usd=50, shipping_usd=None, ships_from="JP", listing_type=ListingType.AUCTION,
                   image_urls=[f"https://img.test/{i}.jpg" for i in range(n_images)], detail_loaded=True)


def _run_check(parsed, stop_reason="end_turn"):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=_jpeg()))
    client = SimpleNamespace(messages=FakeMessages(parsed, stop_reason))

    async def go():
        async with httpx.AsyncClient(transport=transport) as http:
            return await vision.check(client, http, _listing(), "claude-opus-5-5"), client.messages.calls

    return asyncio.run(go())


GOOD = vision.PhotoCheck(box_visible="yes", box_evidence="photo 2 shows the box", count_400=1,
                         count_100_companions=1, count_100_loose=0, count_1000=0,
                         counts_confident=True, concerns=[])


def test_check_sends_shrunk_photos_capped_and_returns_result():
    result, calls = _run_check(GOOD)
    assert result["box_visible"] == "yes" and result["photos"] == vision.MAX_PHOTOS
    content = calls[0]["messages"][0]["content"]
    images = [c for c in content if c["type"] == "image"]
    assert len(images) == vision.MAX_PHOTOS
    import base64
    img = Image.open(io.BytesIO(base64.b64decode(images[0]["source"]["data"])))
    assert max(img.size) <= vision.MAX_EDGE
    assert calls[0]["output_format"] is vision.PhotoCheck


def test_refusal_gives_no_result():
    result, _ = _run_check(GOOD, stop_reason="refusal")
    assert result is None


def test_apply_box_and_counts():
    c = Contents(sets_400=1, loose_100=0, box="unknown", confident=False)
    vision.apply(c, {**GOOD.model_dump(), "count_400": 2})
    assert c.box == "yes" and c.sets_400 == 2 and c.confident


def test_apply_never_overrides_text_saying_no_box():
    c = Contents(sets_400=1, box="no", confident=True)
    vision.apply(c, GOOD.model_dump())
    assert c.box == "no"


def test_apply_photos_show_no_box():
    c = Contents(sets_400=1, box="unknown", confident=True)
    vision.apply(c, {**GOOD.model_dump(), "box_visible": "no", "box_evidence": "figure only in all photos"})
    assert c.box == "no"


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not vision.enabled()
