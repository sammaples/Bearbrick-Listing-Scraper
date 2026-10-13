"""Loads the YAML config (search terms + budget rules).

Lookup order: --config path, $SCOUT_CONFIG_YAML (the whole YAML as a string,
used on GitHub Actions so budgets stay out of the public repo), ./config.yaml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Caps:
    set_400_us: float
    set_400_us_ebay_best_offer: float
    set_400_us_mercari: float
    set_400_intl: float
    loose_100_us: float
    loose_100_intl: float
    figure_1000_us: float
    figure_1000_intl: float | None  # None = not wanted outside the US


@dataclass
class SourceConfig:
    enabled: bool = True
    queries: list[str] = field(default_factory=list)
    pages: int = 1


@dataclass
class Config:
    caps: Caps
    zip_code: str
    exclude_keywords: list[str]
    sources: dict[str, SourceConfig]
    db_path: str = "data/scout.db"
    report_path: str = "out/report.html"
    max_detail_fetches: int = 60  # per source per run, keeps runs short
    auction_alert_hours: float = 12  # alert on auctions only once they end within this


def _source(raw: dict | None) -> SourceConfig:
    raw = raw or {}
    return SourceConfig(
        enabled=raw.get("enabled", True),
        queries=list(raw.get("queries", [])),
        pages=int(raw.get("pages", 1)),
    )


def parse_config(data: dict) -> Config:
    c = data["caps"]
    caps = Caps(
        set_400_us=float(c["set_400"]["us"]),
        set_400_us_ebay_best_offer=float(c["set_400"]["us_ebay_best_offer"]),
        set_400_us_mercari=float(c["set_400"]["us_mercari"]),
        set_400_intl=float(c["set_400"]["intl"]),
        loose_100_us=float(c["loose_100_each"]["us"]),
        loose_100_intl=float(c["loose_100_each"]["intl"]),
        figure_1000_us=float(c["figure_1000"]["us"]),
        figure_1000_intl=(
            None if c["figure_1000"].get("intl") is None else float(c["figure_1000"]["intl"])
        ),
    )
    return Config(
        caps=caps,
        zip_code=str(data.get("zip_code", "")),
        exclude_keywords=list(data.get("exclude_keywords", [])),
        sources={name: _source(raw) for name, raw in (data.get("sources") or {}).items()},
        db_path=data.get("db_path", "data/scout.db"),
        report_path=data.get("report_path", "out/report.html"),
        max_detail_fetches=int(data.get("max_detail_fetches", 60)),
        auction_alert_hours=float(data.get("auction_alert_hours", 12)),
    )


def load_config(path: str | None = None) -> Config:
    if path:
        return parse_config(yaml.safe_load(Path(path).read_text()))
    env = os.environ.get("SCOUT_CONFIG_YAML")
    if env:
        return parse_config(yaml.safe_load(env))
    default = Path("config.yaml")
    if default.exists():
        return parse_config(yaml.safe_load(default.read_text()))
    raise FileNotFoundError(
        "No config found. Copy config.example.yaml to config.yaml, "
        "or set SCOUT_CONFIG_YAML."
    )
