"""Usage: python -m bearbrick_scout [--config config.yaml]"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import load_config
from .report import text_summary, write_report
from .scan import run_scan


def main() -> None:
    ap = argparse.ArgumentParser(prog="bearbrick_scout", description="Scan for Be@rbrick listings within budget.")
    ap.add_argument("--config", help="path to config YAML (default: $SCOUT_CONFIG_YAML or ./config.yaml)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_config(args.config)
    result = asyncio.run(run_scan(cfg))
    path = write_report(result, cfg.report_path)
    print(text_summary(result))
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
