"""Reads a listing's title/description (English or Japanese) and works out
what it contains: sizes, piece counts, and whether a box is included.

Text analysis is deliberately conservative. Anything it can't pin down is
marked not-confident / box "unknown", which routes the listing to
"Needs a look" (and later to the photo check) instead of guessing.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Contents

BRAND_RE = re.compile(r"be@rbrick|bearbrick|bear\s*brick|ベアブリック", re.I)

# (?<![\d.]) stops "2000%" or "1.400%" from matching.
SIZE_RE = re.compile(r"(?<![\d.])(1000|400|100)\s*%")

# "100% & 400%", "400%+100%", "100％＆400％", "100% and 400%", "100%・400%"
SET_RE = re.compile(
    r"(?<![\d.])100\s*%\s*(?:&|\+|and|・|/|x|×|,)?\s*400\s*%"
    r"|(?<![\d.])400\s*%\s*(?:&|\+|and|・|/|x|×|,)?\s*100\s*%"
)

PIECE_UNITS = r"(?:pcs|pc|pieces|piece|figures|figs|体|個|点|bears)"
SET_UNITS = r"(?:sets|set|セット|組)"

# "400% x2", "400% 2体", "100% 12pcs"
QTY_AFTER_SIZE_RE = re.compile(
    rf"(?<![\d.])(1000|400|100)\s*%\s*(?:[x×*]\s*(\d{{1,3}})\b|(\d{{1,3}})\s*{PIECE_UNITS})"
)
# "2x 400%", "12 pcs 100%", "3 x bearbrick 400%"
QTY_BEFORE_SIZE_RE = re.compile(
    rf"(?<![\d.])(\d{{1,3}})\s*(?:[x×*]|{PIECE_UNITS})\s*(?:of\s+)?(?:be@rbrick|bearbrick|ベアブリック)?\s*(1000|400|100)\s*%"
)
SETS_QTY_RE = re.compile(rf"(?<![\d.])(\d{{1,3}})\s*{SET_UNITS}\b|{SET_UNITS}\s*of\s*(\d{{1,3}})\b")
GENERIC_QTY_RE = re.compile(
    rf"(?:lot|bundle|set)\s+of\s+(\d{{1,3}})\b|(?<![\d.%])(\d{{1,3}})\s*{PIECE_UNITS}"
)
# "+3点", "➕3 items": extra things of unknown kind, not more of the same figure.
EXTRAS_RE = re.compile(r"[+➕&]\s*(?:\d{1,3}\s*(?:点|items?|個|pcs|pieces)|others?|おまけ|付属品)")
BULK_RE = re.compile(r"\blot\b|bundle|assorted|まとめ|大量|セット売り|複数")
SERIES_RE = re.compile(r"series\s*\d+|シリーズ\s*\d+")

BOX_NO_RE = re.compile(
    r"no\s*box|without\s*(?:the\s*)?box|w/o\s*box|box\s*(?:is\s*)?not\s*included|missing\s*box"
    r"|figure\s*only|bear\s*only|loose\b"
    r"|箱\s*(?:なし|無し|無|欠品|ありません)|外箱\s*(?:なし|無し|無)|本体のみ"
)
BOX_YES_RE = re.compile(
    r"with\s*(?:the\s*)?(?:original\s*)?box|w/\s*box|box\s*included|includes?\s*(?:the\s*)?box"
    r"|original\s*box|in\s*(?:the\s*)?box|boxed|\bnib\b|new\s*in\s*box|sealed|unopened"
    r"|box\s*(?:has\s*)?(?:damage|wear|dent)"
    r"|箱\s*(?:あり|有り|有|付き|付)|外箱|元箱|化粧箱|未開封"
)


def normalize(text: str | None) -> str:
    # NFKC folds full-width "％＆＠１２" into "%&@12".
    return unicodedata.normalize("NFKC", text or "").lower()


def is_relevant(title: str) -> bool:
    return bool(BRAND_RE.search(normalize(title)))


def find_excluded(text: str, exclude_keywords: list[str]) -> str | None:
    t = normalize(text)
    for kw in exclude_keywords:
        if normalize(kw) in t:
            return kw
    return None


def box_status(text: str) -> tuple[str, str | None]:
    """Returns ("yes"|"no"|"unknown", matched phrase)."""
    t = normalize(text)
    m = BOX_NO_RE.search(t)
    if m:
        return "no", m.group(0)
    m = BOX_YES_RE.search(t)
    if m:
        return "yes", m.group(0)
    return "unknown", None


def _sizes(t: str) -> set[int]:
    return {int(s) for s in SIZE_RE.findall(t)}


def _size_quantities(t: str) -> dict[int, int]:
    qty: dict[int, int] = {}
    for size, a, b in QTY_AFTER_SIZE_RE.findall(t):
        n = int(a or b)
        if 1 <= n <= 200:
            qty[int(size)] = max(qty.get(int(size), 0), n)
    for n, size in QTY_BEFORE_SIZE_RE.findall(t):
        n = int(n)
        if 1 <= n <= 200:
            qty[int(size)] = max(qty.get(int(size), 0), n)
    return qty


def _first_int(matches: list[tuple[str, ...]]) -> int | None:
    for groups in matches:
        for g in groups:
            if g and 1 <= int(g) <= 200:
                return int(g)
    return None


def analyze(title: str, description: str | None, exclude_keywords: list[str]) -> Contents:
    t_title = normalize(title)
    t_all = normalize(f"{title}\n{description or ''}")
    c = Contents()

    excluded = find_excluded(title, exclude_keywords)
    if excluded:
        c.excluded_reason = f'title contains "{excluded}"'
        return c

    c.box, phrase = box_status(t_all)
    if phrase:
        c.notes.append(f'box: "{phrase}"')

    # Sizes come from the title only: descriptions often mention other sizes
    # ("also selling a 1000%"), which would corrupt the counts.
    sizes = _sizes(t_title)
    qty = _size_quantities(t_title)
    extras = bool(EXTRAS_RE.search(t_title))
    generic_qty = None if extras else _first_int(GENERIC_QTY_RE.findall(t_title))
    sets_qty = _first_int(SETS_QTY_RE.findall(t_title))
    bulk = bool(BULK_RE.search(t_title))
    is_set = bool(SET_RE.search(t_title))

    if not sizes and SERIES_RE.search(t_title):
        # Blind-box series figures ("Series 45") are 100% unless stated.
        sizes = {100}
        c.notes.append("size assumed 100% (series figure)")

    if not sizes:
        c.notes.append("no size (100%/400%/1000%) in title")
        return c

    if is_set:
        # A 100% packaged with its 400% adds nothing; count the sets only.
        c.sets_400 = qty.get(400) or sets_qty or 1
        if qty.get(100) == 2 * c.sets_400:
            # "400% 100% 2体セット": the count is the set's own pieces.
            del qty[100]
        extra_100 = qty.get(100, 0) - c.sets_400
        c.loose_100 = max(extra_100, 0)
        c.figures_1000 = qty.get(1000, 1) if 1000 in sizes else 0
        c.confident = (not bulk or bool(qty or sets_qty)) and 100 not in qty
        c.notes.append("100%+400% set: companion 100% not counted")
        if 100 in qty:
            # "set + 100% x4": does the 4 include the set's own 100%? Assume
            # yes (lower cap) and ask for a look.
            c.notes.append("extra 100% count assumed to include the set's companion")
    elif sizes == {400}:
        c.sets_400 = qty.get(400) or generic_qty or 1
        c.confident = not bulk or bool(qty or generic_qty)
    elif sizes == {100}:
        n = qty.get(100) or generic_qty
        c.loose_100 = n or 1
        c.confident = bool(n) or not bulk
    elif sizes == {1000}:
        c.figures_1000 = qty.get(1000) or generic_qty or 1
        c.confident = not bulk or bool(qty or generic_qty)
    else:
        # Mixed sizes without a recognised set phrase (e.g. "1000% & 100%",
        # "400% + 3 x 100%"). Take explicit numbers, default 1, flag it.
        c.figures_1000 = qty.get(1000, 1) if 1000 in sizes else 0
        c.sets_400 = qty.get(400, 1) if 400 in sizes else 0
        c.loose_100 = qty.get(100, 1) if 100 in sizes else 0
        if 400 in sizes and 100 in sizes and 100 not in qty:
            # Unclear whether the 100% is the 400%'s companion: treat it as the
            # companion (lower cap), per the owner's rule.
            c.loose_100 = 0
        c.confident = False
        c.notes.append("mixed sizes: counts need checking")

    if extras:
        c.confident = False
        c.notes.append("listing includes extra items of unknown kind")
    if not c.confident:
        c.notes.append("piece count unclear")
    return c
