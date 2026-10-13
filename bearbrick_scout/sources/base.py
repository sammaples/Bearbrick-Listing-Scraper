from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Listing


@dataclass
class SourceRun:
    """Outcome of one source's search during a run, shown in the report."""

    name: str
    ok: bool = True
    message: str = ""
    found: int = 0
    listings: list[Listing] = field(default_factory=list)


class SourceBlocked(Exception):
    """The site served a bot challenge / 403 instead of results."""


MONEY_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_money(text: str | None) -> float | None:
    if not text:
        return None
    m = MONEY_RE.search(text.replace(" ", ""))
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out
