#!/usr/bin/env python3
"""
Audit Logger: Tamper-evident action log for compliance.

Every pipeline action gets logged with:
  - timestamp (UTC ISO8601)
  - action_id (UUID)
  - tier (1–4)
  - annotation text
  - parsed instruction JSON
  - before_file hash (SHA-256)
  - after_file hash (SHA-256)
  - confidence report JSON
  - verification status
  - reviewer_id (if human review)

The log is append-only. Each entry includes the previous entry's hash,
forming a simple hash chain. If any entry is tampered with, the chain breaks.

Storage options:
  - SQLite (default): structured, queryable
  - JSON Lines: append-only, easy to stream
"""

import os
import json
import hashlib
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict


@dataclass
class AuditEntry:
    action_id: str
    timestamp: str
    tier: int
    annotation: str
    parsed_instruction: Dict[str, Any]
    confidence_report: Dict[str, Any]
    verification_status: str
    before_hash: str
    after_hash: str
    reviewer_id: Optional[str] = None
    review_notes: Optional[str] = None
    previous_hash: str = ""
    entry_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AuditLogger:
    """Tamper-evident append-only audit log."""

    def __init__(self, db_path: str = "audit_log.db", jsonl_path: Optional[str] = "audit_log.jsonl"):
        self.db_path = Path(db_path)
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    action_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    tier INTEGER NOT NULL,
                    annotation TEXT NOT NULL,
                    parsed_instruction TEXT NOT NULL,
                    confidence_report TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    before_hash TEXT NOT NULL,
                    after_hash TEXT NOT NULL,
                    reviewer_id TEXT,
                    review_notes TEXT,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
            """)
            conn.commit()

    @staticmethod
    def file_hash(path: Optional[str]) -> str:
        """SHA-256 of file contents. Empty string if path is None or missing."""
        if not path or not Path(path).exists():
            return ""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _hash_entry(entry: AuditEntry) -> str:
        """Compute hash of entry data + previous_hash."""
        data = json.dumps({
            "action_id": entry.action_id,
            "timestamp": entry.timestamp,
            "tier": entry.tier,
            "annotation": entry.annotation,
            "parsed_instruction": entry.parsed_instruction,
            "confidence_report": entry.confidence_report,
            "verification_status": entry.verification_status,
            "before_hash": entry.before_hash,
            "after_hash": entry.after_hash,
            "reviewer_id": entry.reviewer_id,
            "review_notes": entry.review_notes,
            "previous_hash": entry.previous_hash,
        }, sort_keys=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _get_last_hash(self) -> str:
        """Get the entry_hash of the most recent audit entry."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT entry_hash FROM audit_log ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else ""

    def log(
        self,
        tier: int,
        annotation: str,
        parsed_instruction: Dict[str, Any],
        confidence_report: Dict[str, Any],
        verification_status: str,
        before_file: Optional[str] = None,
        after_file: Optional[str] = None,
        reviewer_id: Optional[str] = None,
        review_notes: Optional[str] = None,
    ) -> str:
        """
        Log an action. Returns the action_id.
        """
        action_id = str(uuid.uuid4())[:12]
        now = datetime.utcnow().isoformat() + "Z"
        before_hash = self.file_hash(before_file)
        after_hash = self.file_hash(after_file)
        prev_hash = self._get_last_hash()

        entry = AuditEntry(
            action_id=action_id,
            timestamp=now,
            tier=tier,
            annotation=annotation,
            parsed_instruction=parsed_instruction,
            confidence_report=confidence_report,
            verification_status=verification_status,
            before_hash=before_hash,
            after_hash=after_hash,
            reviewer_id=reviewer_id,
            review_notes=review_notes,
            previous_hash=prev_hash,
            entry_hash="",  # computed below
        )
        entry.entry_hash = self._hash_entry(entry)

        # Insert to SQLite
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.action_id, entry.timestamp, entry.tier, entry.annotation,
                json.dumps(entry.parsed_instruction),
                json.dumps(entry.confidence_report),
                entry.verification_status,
                entry.before_hash, entry.after_hash,
                entry.reviewer_id, entry.review_notes,
                entry.previous_hash, entry.entry_hash,
            ))
            conn.commit()

        # Append to JSONL
        if self.jsonl_path:
            with open(self.jsonl_path, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")

        return action_id

    def verify_chain(self) -> Dict[str, Any]:
        """
        Verify hash chain integrity. Returns summary of tampered entries.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp"
            ).fetchall()

        tampered = []
        for idx, row in enumerate(rows):
            entry = AuditEntry(
                action_id=row[0], timestamp=row[1], tier=row[2], annotation=row[3],
                parsed_instruction=json.loads(row[4]),
                confidence_report=json.loads(row[5]),
                verification_status=row[6],
                before_hash=row[7], after_hash=row[8],
                reviewer_id=row[9], review_notes=row[10],
                previous_hash=row[11], entry_hash=row[12],
            )
            # Recompute hash
            recomputed = self._hash_entry(entry)
            if recomputed != entry.entry_hash:
                tampered.append({"action_id": entry.action_id, "index": idx, "reason": "entry_hash_mismatch"})
                continue

            # Check chain link
            if idx > 0:
                prev_entry_hash = rows[idx - 1][12]
                if entry.previous_hash != prev_entry_hash:
                    tampered.append({"action_id": entry.action_id, "index": idx, "reason": "chain_break"})

        return {
            "total_entries": len(rows),
            "tampered_count": len(tampered),
            "tampered": tampered,
            "integrity": "OK" if not tampered else "COMPROMISED",
        }

    def get_entry(self, action_id: str) -> Optional[AuditEntry]:
        """Retrieve a single audit entry."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE action_id = ?", (action_id,)
            ).fetchone()
        if not row:
            return None
        return AuditEntry(
            action_id=row[0], timestamp=row[1], tier=row[2], annotation=row[3],
            parsed_instruction=json.loads(row[4]),
            confidence_report=json.loads(row[5]),
            verification_status=row[6],
            before_hash=row[7], after_hash=row[8],
            reviewer_id=row[9], review_notes=row[10],
            previous_hash=row[11], entry_hash=row[12],
        )

    def summary(self, limit: int = 100) -> Dict[str, Any]:
        """Quick stats of recent activity."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            tier_counts = conn.execute(
                "SELECT tier, COUNT(*) FROM audit_log GROUP BY tier"
            ).fetchall()
            status_counts = conn.execute(
                "SELECT verification_status, COUNT(*) FROM audit_log GROUP BY verification_status"
            ).fetchall()
            recent = conn.execute(
                "SELECT action_id, timestamp, tier, annotation, verification_status "
                "FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()

        return {
            "total_actions": total,
            "tier_distribution": {f"T{t}": c for t, c in tier_counts},
            "status_distribution": {s: c for s, c in status_counts},
            "recent_actions": [
                {"action_id": r[0], "timestamp": r[1], "tier": r[2], "annotation": r[3][:60], "status": r[4]}
                for r in recent
            ],
        }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Audit Logger CLI")
    ap.add_argument("--db", default="audit_log.db")
    ap.add_argument("--jsonl", default="audit_log.jsonl")
    sub = ap.add_subparsers(dest="cmd")

    p_log = sub.add_parser("log", help="Log a dummy action")
    p_log.add_argument("--tier", type=int, default=1)
    p_log.add_argument("--annotation", default="Test annotation")
    p_log.add_argument("--status", default="PASSED")

    p_verify = sub.add_parser("verify", help="Verify chain integrity")
    p_summary = sub.add_parser("summary", help="Show summary")

    args = ap.parse_args()
    logger = AuditLogger(db_path=args.db, jsonl_path=args.jsonl)

    if args.cmd == "log":
        aid = logger.log(
            tier=args.tier,
            annotation=args.annotation,
            parsed_instruction={"action_type": "replace_text", "target": "A", "replacement": "B"},
            confidence_report={"composite": 0.92, "passed": True},
            verification_status=args.status,
        )
        print(f"Logged: {aid}")
    elif args.cmd == "verify":
        print(json.dumps(logger.verify_chain(), indent=2))
    elif args.cmd == "summary":
        print(json.dumps(logger.summary(), indent=2))
    else:
        ap.print_help()
