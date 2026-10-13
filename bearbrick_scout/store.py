"""SQLite memory between runs: what we've seen, what it was judged, and when.

This is what makes "new since last run" and "dropped out (over budget)" work.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import Contents, Listing, Status, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    sources_json TEXT
);
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    first_seen_run INTEGER NOT NULL,
    last_seen_run INTEGER NOT NULL,
    status TEXT NOT NULL,
    status_changed_run INTEGER NOT NULL,
    listing_json TEXT NOT NULL,
    contents_json TEXT,
    verdict_json TEXT,
    user_state TEXT  -- reserved for the app: pursue / dismiss
);
CREATE INDEX IF NOT EXISTS listings_status ON listings(status);
"""

ACTIVE = {Status.MATCH.value, Status.REVIEW.value}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def start_run(self) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at) VALUES (?)", (_now(),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, sources: list[dict]) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at=?, sources_json=? WHERE id=?",
            (_now(), json.dumps(sources), run_id),
        )
        self.db.commit()

    def cached_listing(self, key: str) -> Listing | None:
        row = self.db.execute("SELECT listing_json FROM listings WHERE key=?", (key,)).fetchone()
        return Listing.from_dict(json.loads(row["listing_json"])) if row else None

    def save(self, run_id: int, listing: Listing, contents: Contents, verdict: Verdict) -> str | None:
        """Upserts and returns the change event: "new", "dropped" or None."""
        row = self.db.execute("SELECT status FROM listings WHERE key=?", (listing.key,)).fetchone()
        prev = row["status"] if row else None
        status = verdict.status.value
        payload = (
            json.dumps(listing.to_dict()),
            json.dumps(asdict(contents)),
            json.dumps({**asdict(verdict), "status": status}),
        )
        if row is None:
            self.db.execute(
                "INSERT INTO listings(key, source, first_seen_run, last_seen_run, status, status_changed_run,"
                " listing_json, contents_json, verdict_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (listing.key, listing.source.value, run_id, run_id, status, run_id, *payload),
            )
        else:
            changed = prev != status
            self.db.execute(
                "UPDATE listings SET last_seen_run=?, status=?, listing_json=?, contents_json=?, verdict_json=?,"
                " status_changed_run=CASE WHEN ? THEN ? ELSE status_changed_run END WHERE key=?",
                (run_id, status, *payload, changed, run_id, listing.key),
            )
        self.db.commit()
        if status in ACTIVE and prev not in ACTIVE:
            return "new"
        if prev in ACTIVE and status not in ACTIVE:
            return "dropped"
        return None

    def active(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM listings WHERE status IN ('match','review') AND COALESCE(user_state,'') != 'dismiss'"
            " ORDER BY status_changed_run DESC"
        ).fetchall()
