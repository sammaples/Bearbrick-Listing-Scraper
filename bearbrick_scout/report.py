"""Renders the scan as a self-contained HTML page plus a plain-text summary.

Stage 4 replaces this page with the private app; the data shapes stay.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .scan import Judged, ScanResult

CSS = """
:root { --bg:#f6f5f2; --card:#fff; --ink:#1d1d1f; --muted:#6b6b70; --line:#e4e2dc;
  --match:#1f7a4d; --review:#b26a00; --drop:#a33; --accent:#3b5bdb; }
@media (prefers-color-scheme: dark) { :root { --bg:#141416; --card:#1e1e22; --ink:#ececf0;
  --muted:#9a9aa3; --line:#2e2e34; --match:#4cc38a; --review:#f0a940; --drop:#ff7b7b; --accent:#8ea2ff; } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 -apple-system,system-ui,sans-serif }
main { max-width:860px; margin:0 auto; padding:20px 16px 60px }
h1 { font-size:22px; margin:0 0 4px } h2 { font-size:17px; margin:28px 0 10px }
.sub { color:var(--muted); font-size:13px }
.card { display:flex; gap:12px; background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:12px; margin-bottom:10px }
.card img { width:92px; height:92px; object-fit:cover; border-radius:8px; flex:none; background:var(--line) }
.body { min-width:0; flex:1 }
.title { font-weight:600; overflow-wrap:anywhere }
.title a { color:inherit; text-decoration:none }
.orig { color:var(--muted); font-size:12px; overflow-wrap:anywhere }
.row { display:flex; flex-wrap:wrap; gap:6px 12px; font-size:13px; margin-top:6px; color:var(--muted) }
.price { color:var(--ink); font-weight:600 }
.pill { display:inline-block; padding:1px 8px; border-radius:99px; font-size:12px; border:1px solid var(--line) }
.match { color:var(--match); border-color:var(--match) } .review { color:var(--review); border-color:var(--review) }
.drop { color:var(--drop); border-color:var(--drop) }
ul.reasons { margin:6px 0 0; padding-left:18px; font-size:13px }
table { width:100%; border-collapse:collapse; font-size:13px; background:var(--card); border-radius:8px }
td, th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line) }
.btn { display:inline-block; margin-top:8px; padding:5px 12px; border-radius:8px; background:var(--accent);
  color:#fff; text-decoration:none; font-size:13px }
.empty { color:var(--muted); font-style:italic }
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _type_label(listing: dict) -> str:
    if listing["listing_type"] == "auction":
        bits = ["Auction"]
        if listing.get("ends_at"):
            bits.append(f"ends {listing['ends_at']}")
        if listing.get("bid_count") is not None:
            bits.append(f"{listing['bid_count']} bids")
        return " · ".join(bits)
    return "Buy It Now + Best Offer" if listing.get("best_offer") else "Buy It Now"


def _source_label(source: str) -> str:
    from .models import Source

    return Source(source).label


def _card(listing: dict, contents: dict, verdict: dict, tag: str) -> str:
    img = (listing.get("image_urls") or [""])[0]
    ship = listing.get("shipping_usd")
    in_us = (listing.get("ships_from") or "").upper() == "US"
    if in_us:
        ship_txt = "ship ?" if ship is None else ("free ship" if ship == 0 else f"+ ${ship:.2f} ship")
    else:
        ship_txt = "intl ship not counted"
    total, cap = verdict.get("total_usd"), verdict.get("cap_usd")
    vs = f"${total:.2f} / cap ${cap:g}" if total is not None and cap is not None else ""
    pill_cls = {"match": "match", "review": "review", "reject": "drop"}[verdict["status"]]
    pill_txt = {"match": "Match", "review": "Needs a look", "reject": "Dropped"}[verdict["status"]]
    orig = f'<div class="orig">{_e(listing["title_original"])}</div>' if listing.get("title_original") else ""
    reasons = "".join(f"<li>{_e(r)}</li>" for r in verdict.get("reasons") or [])
    notes = "; ".join(contents.get("notes") or [])
    return f"""
<div class="card">
  {'<img loading="lazy" src="' + _e(img) + '" alt="">' if img else ''}
  <div class="body">
    <div class="title"><a href="{_e(listing['url'])}" target="_blank" rel="noopener">{_e(listing['title'])}</a></div>
    {orig}
    <div class="row">
      <span class="pill {pill_cls}">{pill_txt}</span>
      <span>{_e(_source_label(listing['source']))}</span>
      <span class="price">${listing['price_usd']:.2f}</span>
      {'<span>(' + _e(listing['price_original']) + ')</span>' if listing.get('price_original') else ''}
      <span>{_e(ship_txt)}</span>
      <span>{_e(vs)}</span>
    </div>
    <div class="row">
      <span>{_e(_type_label(listing))}</span>
      <span>Condition: {_e(listing.get('condition') or 'not stated')}</span>
      <span>Ships from {_e(listing.get('ships_from') or '?')}</span>
      <span>Box: {_e(contents.get('box'))}</span>
    </div>
    <div class="row"><span>{_e(verdict.get('rule'))}</span></div>
    {'<ul class="reasons">' + reasons + '</ul>' if reasons else ''}
    {'<div class="orig">' + _e(notes) + '</div>' if notes else ''}
    <a class="btn" href="{_e(listing['url'])}" target="_blank" rel="noopener">Open listing</a>
  </div>
</div>"""


def _row_card(row, tag: str) -> str:
    return _card(json.loads(row["listing_json"]), json.loads(row["contents_json"]), json.loads(row["verdict_json"]), tag)


def _judged_card(j: Judged, tag: str) -> str:
    from dataclasses import asdict

    v = asdict(j.verdict)
    v["status"] = j.verdict.status.value
    return _card(j.listing.to_dict(), asdict(j.contents), v, tag)


def render_html(result: ScanResult) -> str:
    active = result.store.active()
    new_keys = {j.listing.key for j in result.new}
    matches = [r for r in active if r["status"] == "match"]
    review = [r for r in active if r["status"] == "review"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def section(title: str, cards: list[str], empty: str) -> str:
        body = "".join(cards) if cards else f'<p class="empty">{empty}</p>'
        return f"<h2>{title} ({len(cards)})</h2>{body}"

    src_rows = "".join(
        f"<tr><td>{_e(s.name)}</td><td>{'OK' if s.ok else 'Problem'}</td><td>{s.found}</td><td>{_e(s.message)}</td></tr>"
        for s in result.sources
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bearbrick Scout</title><style>{CSS}</style></head>
<body><main>
<h1>Bearbrick Scout</h1>
<div class="sub">Run #{result.run_id} · {now} · {result.judged} listings checked</div>
{section("New since last run", [_judged_card(j, "new") for j in result.new], "Nothing new this run.")}
{section("All current matches", [_row_card(r, "match") for r in matches if r["key"] not in new_keys], "No other matches.")}
{section("Needs a look", [_row_card(r, "review") for r in review if r["key"] not in new_keys], "Nothing waiting for review.")}
{section("Dropped out", [_judged_card(j, "drop") for j in result.dropped], "Nothing dropped this run.")}
<h2>Sites this run</h2>
<table><tr><th>Site</th><th>Status</th><th>Listings</th><th>Note</th></tr>{src_rows}</table>
</main></body></html>"""


def write_report(result: ScanResult, path: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(result), encoding="utf-8")
    return out


def text_summary(result: ScanResult) -> str:
    lines = [f"Run #{result.run_id}: {result.judged} listings checked"]
    for s in result.sources:
        lines.append(f"  {s.name}: {'ok' if s.ok else 'PROBLEM'}, {s.found} found {s.message}".rstrip())
    lines.append(f"New matches/needs-a-look: {len(result.new)}")
    for j in result.new:
        v = j.verdict
        total = f"${v.total_usd:.2f}" if v.total_usd is not None else "?"
        lines.append(f"  [{v.status.value}] {total} / cap ${v.cap_usd:g}  {j.listing.title[:70]}  {j.listing.url}")
    if result.dropped:
        lines.append(f"Dropped out: {len(result.dropped)}")
    return "\n".join(lines)
