"""Pushover alerts for new matches (needs PUSHOVER_APP_TOKEN + PUSHOVER_USER_KEY).

One alert per listing, with its first photo, up to MAX_INDIVIDUAL; beyond
that a single summary alert so a big first run doesn't spam the phone.
"""

from __future__ import annotations

import logging
import os

import httpx

from .models import Status
from .scan import Judged

log = logging.getLogger("scout")

API = "https://api.pushover.net/1/messages.json"
MAX_INDIVIDUAL = 5
MAX_IMAGE_BYTES = 2_500_000


def _message(j: Judged) -> tuple[str, str]:
    l, v = j.listing, j.verdict
    head = "Match" if v.status is Status.MATCH else "Needs a look"
    total = f"${v.total_usd:.2f}" if v.total_usd is not None else f"${l.price_usd:.2f}"
    title = f"{head}: {total} · {l.source.label}"
    lines = [l.title]
    if l.title_original:
        lines.append(l.title_original)
    lines.append(f"{total} vs cap ${v.cap_usd:g}" if v.cap_usd is not None else total)
    lines.append(f"Box: {j.contents.box} · {j.contents.describe()}")
    if v.reasons:
        lines.append("; ".join(v.reasons))
    return title[:250], "\n".join(lines)[:1024]


def _image(http: httpx.Client, url: str | None) -> tuple[str, bytes, str] | None:
    if not url:
        return None
    try:
        r = http.get(url, timeout=15, follow_redirects=True)
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if r.status_code == 200 and ctype.startswith("image/") and len(r.content) <= MAX_IMAGE_BYTES:
            return ("photo", r.content, ctype)
    except httpx.HTTPError:
        pass
    return None


def send_alerts(new: list[Judged]) -> list[str]:
    """Sends alerts; returns the keys of listings that were alerted."""
    token, user = os.environ.get("PUSHOVER_APP_TOKEN"), os.environ.get("PUSHOVER_USER_KEY")
    if not (token and user) or not new:
        return []
    # Clear matches first, then needs-a-look; cheapest relative to cap first.
    new = sorted(new, key=lambda j: (j.verdict.status is not Status.MATCH, (j.verdict.total_usd or 0) / (j.verdict.cap_usd or 1)))
    sent: list[str] = []
    with httpx.Client(timeout=20) as http:
        for j in new[:MAX_INDIVIDUAL]:
            title, message = _message(j)
            data = {"token": token, "user": user, "title": title, "message": message,
                    "url": j.listing.url, "url_title": "Open listing"}
            img = _image(http, (j.listing.image_urls or [None])[0])
            r = http.post(API, data=data, files={"attachment": img} if img else None)
            if r.status_code == 200:
                sent.append(j.listing.key)
            else:
                log.warning("Pushover rejected an alert: HTTP %s", r.status_code)
        rest = new[MAX_INDIVIDUAL:]
        if rest:
            lines = [f"${(j.verdict.total_usd or j.listing.price_usd):.0f} · {j.listing.title[:60]}" for j in rest[:15]]
            r = http.post(API, data={"token": token, "user": user,
                                     "title": f"+{len(rest)} more new listings",
                                     "message": "\n".join(lines)[:1024]})
            if r.status_code == 200:
                sent += [j.listing.key for j in rest]
    return sent
