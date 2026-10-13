"""Photo check with Claude: is the box there, and how many pieces?

Runs only when ANTHROPIC_API_KEY is set, and only for listings that already
fit the budget but whose box or piece count the text couldn't settle. Each
listing is checked once; the result is stored on the listing and reused.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from typing import Literal

import anthropic
import httpx
from PIL import Image
from pydantic import BaseModel, Field

from .models import Contents, Listing

log = logging.getLogger("scout")

MAX_PHOTOS = 8
MAX_EDGE = 1024  # px; plenty to spot a box, about half the tokens of full size

SYSTEM = """You check resale listings of Medicom Toy BE@RBRICK figures for a buyer.

Sizes: 100% figures are about 7 cm, 400% about 28 cm, 1000% about 70 cm. A
"100% & 400% set" is one 400% and one matching 100% sold together in a single
box; that 100% is the set's companion, not a loose extra.

Box rule: every 400% and every 1000% must come with its original retail box.
Answer box_visible:
- "yes" only if a retail box for each 400%/1000% is visible in the photos, or
  the description clearly states the box is included (e.g. 箱あり, 箱付き,
  未開封/unopened, "with box").
- "no" if the photos/description show or say there is no box (e.g. figure
  only, 箱なし, 本体のみ, loose figure with no box anywhere).
- "unclear" if you cannot tell (box partly visible, could be a different
  item's box, too few photos).
If the listing has no 400% or 1000% figure, answer "not_needed".

Count what is actually being sold, using the photos and text together. Do not
count figures that appear only as background or decoration."""


class PhotoCheck(BaseModel):
    box_visible: Literal["yes", "no", "unclear", "not_needed"]
    box_evidence: str = Field(description="Which photo number(s) or text show it, in a few words")
    count_400: int = Field(description="400% figures, counting each 100%+400% set as one")
    count_100_companions: int = Field(description="100% figures that are the companion in a 100%+400% set")
    count_100_loose: int = Field(description="Other 100% figures being sold")
    count_1000: int
    counts_confident: bool
    concerns: list[str] = Field(description="Damage, possible fake, missing parts, or other red flags")


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _shrink(data: bytes) -> tuple[bytes, str] | None:
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return None


async def _photos(http: httpx.AsyncClient, urls: list[str]) -> list[dict]:
    async def one(url: str) -> dict | None:
        try:
            r = await http.get(url, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                return None
        except httpx.HTTPError:
            return None
        shrunk = _shrink(r.content)
        if not shrunk:
            return None
        data, media = shrunk
        return {"type": "image", "source": {"type": "base64", "media_type": media,
                                            "data": base64.standard_b64encode(data).decode()}}

    got = await asyncio.gather(*(one(u) for u in urls[:MAX_PHOTOS]))
    return [g for g in got if g]


async def check(client: anthropic.AsyncAnthropic, http: httpx.AsyncClient, listing: Listing, model: str) -> dict | None:
    images = await _photos(http, listing.image_urls)
    if not images:
        return None
    content: list[dict] = []
    for i, img in enumerate(images, 1):
        content += [{"type": "text", "text": f"Photo {i}:"}, img]
    text = f"Title: {listing.title}\n"
    if listing.title_original:
        text += f"Original title: {listing.title_original}\n"
    if listing.description:
        text += f"Description:\n{listing.description[:4000]}\n"
    content.append({"type": "text", "text": text})
    try:
        resp = await client.messages.parse(
            model=model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_format=PhotoCheck,
        )
    except anthropic.APIError as e:
        log.warning("photo check failed for %s: %s", listing.key, type(e).__name__)
        return None
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None
    return {**resp.parsed_output.model_dump(), "model": model, "photos": len(images)}


def apply(contents: Contents, result: dict) -> None:
    """Folds a photo-check result into what the text analysis found."""
    box = result["box_visible"]
    if box in ("yes", "no") and contents.box == "unknown":
        contents.box = box
    contents.notes.append(f'photos: box {box} ({result["box_evidence"]})')
    if not contents.confident and result["counts_confident"]:
        contents.sets_400 = result["count_400"]
        contents.loose_100 = result["count_100_loose"]
        contents.figures_1000 = result["count_1000"]
        contents.confident = True
        contents.notes.append("piece count from photos")
    for c in result.get("concerns") or []:
        contents.notes.append(f"photo concern: {c}")
