# Bearbrick Scout

Scans eBay, Mercari US and Buyee (Yahoo Auctions Japan + Mercari Japan) for
Be@rbrick 100% / 400% / 1000% listings within budget, and reports new matches.

## How a listing is judged

1. **Relevant?** Title must mention Be@rbrick / ベアブリック. Titles containing an
   excluded word (junk, ジャンク, custom, empty box, ...) are skipped.
2. **What's in it?** Sizes and piece counts are read from the title (English or
   Japanese): `400% x2`, `12pcs`, `2体セット`, `100% & 400%` sets, blind-box
   "Series 45" figures (100%).
3. **Budget cap** adds up per piece:
   - 400% figure, or 100%+400% set (the companion 100% is free)
   - loose 100% figures, per piece
   - 1000% figures (US only)
   - US listings compare **price + shipping** to your ZIP; listings shipping
     from outside the US compare **item price only**.
   - eBay US Buy It Now with Best Offer and all Mercari US listings get the
     higher 400% cap.
   - Auctions use the current bid.
4. **Box check** for every 400% and 1000%: confirmed from the description
   (`with box`, `箱あり`, `未開封`, ...). `no box` / `箱なし` / `本体のみ` rejects.
5. Result: **Match**, **Needs a look** (box unconfirmed, piece count unclear,
   shipping unknown), or rejected.

Detail pages (description, all photos, shipping) are fetched only for listings
that could pass on price, and are cached so later runs don't refetch them.

## Run it locally

```bash
pip install -r requirements.txt
playwright install chromium
cp config.example.yaml config.yaml   # then put in your real budgets
export EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=...   # optional; eBay is skipped without them
python -m bearbrick_scout
```

The report is written to `out/report.html`; listing history lives in
`data/scout.db` (SQLite).

## Configuration

`config.yaml` is git-ignored so budgets stay private. On GitHub Actions the
whole file goes in the `SCOUT_CONFIG_YAML` secret. See `config.example.yaml`
for every option.

| Env var | Purpose |
|---|---|
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | eBay Browse API production keyset |
| `SCOUT_CONFIG_YAML` | full config as a string (overrides `config.yaml`) |
| `CHROMIUM_PATH` | use a specific Chromium binary |
| `SCRAPER_PROXY` | route the browser through a proxy if a site blocks datacenter IPs |

## Roadmap

1. ✅ Scanners + budget rules + report
2. Photo checks (box visible, piece counts) with Claude vision
3. Hourly GitHub Actions schedule
4. Private home-screen app + Pushover alerts
5. Approve-to-buy
