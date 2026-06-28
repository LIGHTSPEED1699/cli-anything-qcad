#!/usr/bin/env python3
"""
Review Queue: SQLite-backed queue for below-threshold annotations.

When confidence scorer flags an annotation for human review, this queue:
  1. Stores the annotation + before/after paths + VLM reasoning
  2. Assigns a review_id
  3. Tracks status: PENDING → APPROVED / REJECTED / ESCALATED
  4. Can export to Discord notification or email digest

Schema:
  review_id      TEXT PRIMARY KEY
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  status         TEXT (PENDING, APPROVED, REJECTED, ESCALATED)
  annotation     TEXT
  parsed_json    TEXT
  tier           INTEGER
  confidence_report TEXT
  original_file  TEXT
  modified_file  TEXT
  before_png     TEXT
  after_png      TEXT
  reviewer_notes TEXT
  reviewed_by    TEXT
  reviewed_at    TIMESTAMP
"""

import os
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict


@dataclass
class ReviewEntry:
    review_id: str
    created_at: str
    status: str
    annotation: str
    parsed_json: Optional[str]
    tier: int
    confidence_report: Optional[str]
    original_file: Optional[str]
    modified_file: Optional[str]
    before_png: Optional[str]
    after_png: Optional[str]
    reviewer_notes: Optional[str]
    reviewed_by: Optional[str]
    reviewed_at: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReviewQueue:
    """SQLite queue for human review of low-confidence annotations."""

    def __init__(self, db_path: str = "review_queue.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'PENDING',
                    annotation TEXT NOT NULL,
                    parsed_json TEXT,
                    tier INTEGER,
                    confidence_report TEXT,
                    original_file TEXT,
                    modified_file TEXT,
                    before_png TEXT,
                    after_png TEXT,
                    reviewer_notes TEXT,
                    reviewed_by TEXT,
                    reviewed_at TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON reviews(status)
            """)
            conn.commit()

    def enqueue(
        self,
        annotation: str,
        tier: int,
        parsed_json: Optional[Dict[str, Any]] = None,
        confidence_report: Optional[Dict[str, Any]] = None,
        original_file: Optional[str] = None,
        modified_file: Optional[str] = None,
        before_png: Optional[str] = None,
        after_png: Optional[str] = None,
    ) -> str:
        """Add a new entry to the review queue. Returns review_id."""
        review_id = str(uuid.uuid4())[:8]
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO reviews
                (review_id, created_at, status, annotation, parsed_json, tier,
                 confidence_report, original_file, modified_file, before_png, after_png)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                review_id, now, "PENDING", annotation,
                json.dumps(parsed_json) if parsed_json else None,
                tier,
                json.dumps(confidence_report) if confidence_report else None,
                original_file, modified_file, before_png, after_png,
            ))
            conn.commit()
        return review_id

    def list_pending(self, limit: int = 50) -> List[ReviewEntry]:
        """Return all PENDING reviews."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM reviews WHERE status = 'PENDING' ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get(self, review_id: str) -> Optional[ReviewEntry]:
        """Get a single review entry."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,)).fetchone()
        return self._row_to_entry(row) if row else None

    def update_status(
        self,
        review_id: str,
        status: str,  # APPROVED, REJECTED, ESCALATED
        reviewer: str = "system",
        notes: str = "",
    ) -> bool:
        """Mark a review as resolved."""
        if status not in {"APPROVED", "REJECTED", "ESCALATED"}:
            raise ValueError(f"Invalid status: {status}")
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                UPDATE reviews
                SET status = ?, reviewed_by = ?, reviewer_notes = ?, reviewed_at = ?
                WHERE review_id = ?
            """, (status, reviewer, notes, now, review_id))
            conn.commit()
            return cur.rowcount > 0

    def stats(self) -> Dict[str, int]:
        """Count per status."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall()
        return {status: count for status, count in rows}

    def export_for_discord(self, review_id: str) -> str:
        """Format a review entry for Discord notification."""
        entry = self.get(review_id)
        if not entry:
            return f"Review {review_id} not found."

        lines = [
            f"**🔍 Human Review Required — `{review_id}`**",
            f"**Annotation:** `{entry.annotation}`",
            f"**Tier:** T{entry.tier}",
            f"**Status:** {entry.status}",
            f"**Created:** {entry.created_at}",
        ]
        if entry.confidence_report:
            try:
                cr = json.loads(entry.confidence_report)
                lines.append(f"**Composite Confidence:** {cr.get('composite_score', 'N/A')}")
                for layer in cr.get("layers", []):
                    emoji = "✅" if layer["passed"] else "❌"
                    lines.append(f"  {emoji} {layer['name']}: {layer['score']:.2f} (threshold {layer['threshold']})")
            except json.JSONDecodeError:
                pass
        if entry.before_png and Path(entry.before_png).exists():
            lines.append(f"**Before:** {entry.before_png}")
        if entry.after_png and Path(entry.after_png).exists():
            lines.append(f"**After:** {entry.after_png}")

        return "\n".join(lines)

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> ReviewEntry:
        return ReviewEntry(
            review_id=row["review_id"],
            created_at=row["created_at"],
            status=row["status"],
            annotation=row["annotation"],
            parsed_json=row["parsed_json"],
            tier=row["tier"],
            confidence_report=row["confidence_report"],
            original_file=row["original_file"],
            modified_file=row["modified_file"],
            before_png=row["before_png"],
            after_png=row["after_png"],
            reviewer_notes=row["reviewer_notes"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"],
        )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Review Queue CLI")
    ap.add_argument("--db", default="review_queue.db", help="SQLite DB path")
    sub = ap.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Enqueue a review")
    p_add.add_argument("annotation")
    p_add.add_argument("--tier", type=int, default=4)
    p_add.add_argument("--before", default=None)
    p_add.add_argument("--after", default=None)

    p_list = sub.add_parser("list", help="List pending reviews")
    p_list.add_argument("--limit", type=int, default=20)

    p_stats = sub.add_parser("stats", help="Show stats")

    p_resolve = sub.add_parser("resolve", help="Resolve a review")
    p_resolve.add_argument("review_id")
    p_resolve.add_argument("status", choices=["APPROVED", "REJECTED", "ESCALATED"])
    p_resolve.add_argument("--reviewer", default="human")
    p_resolve.add_argument("--notes", default="")

    args = ap.parse_args()

    q = ReviewQueue(db_path=args.db)
    if args.cmd == "add":
        rid = q.enqueue(args.annotation, tier=args.tier, before_png=args.before, after_png=args.after)
        print(f"Enqueued: {rid}")
    elif args.cmd == "list":
        for e in q.list_pending(limit=args.limit):
            print(f"{e.review_id} | T{e.tier} | {e.status} | {e.annotation[:60]}")
    elif args.cmd == "stats":
        print(json.dumps(q.stats(), indent=2))
    elif args.cmd == "resolve":
        ok = q.update_status(args.review_id, args.status, reviewer=args.reviewer, notes=args.notes)
        print("Updated." if ok else "Not found.")
    else:
        ap.print_help()
