"""Usage: python -m bearbrick_scout [--config config.yaml]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from .config import load_config
from .notify import send_alerts
from .report import text_summary, write_report
from .scan import run_scan


def main() -> None:
    ap = argparse.ArgumentParser(prog="bearbrick_scout", description="Scan for Be@rbrick listings within budget.")
    ap.add_argument("--config", help="path to config YAML (default: $SCOUT_CONFIG_YAML or ./config.yaml)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Public CI logs: keep listing details (and so budgets) out of them.
    public_log = os.environ.get("SCOUT_PUBLIC_LOG") == "1"

    cfg = load_config(args.config)
    result = asyncio.run(run_scan(cfg))
    path = write_report(result, cfg.report_path)
    alerted = send_alerts(result.alerts)
    result.store.mark_notified(alerted)
    alerts = len(alerted)

    if public_log:
        for s in result.sources:
            print(f"{s.name}: {'ok' if s.ok else 'PROBLEM'}, {s.found} listings. {s.message}".rstrip())
        print(f"{result.judged} checked, {len(result.new)} new, {len(result.dropped)} dropped, {alerts} alerts sent")
    else:
        print(text_summary(result))
        print(f"Alerts sent: {alerts}")
        print(f"Report: {path}")


if __name__ == "__main__":
    main()
