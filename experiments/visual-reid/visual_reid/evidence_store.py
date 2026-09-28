"""Seven-day persistence for scores and quality, never embedding vectors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


RETENTION_DAYS = 7


class EvidenceStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidate_evidence (
                candidate_id TEXT PRIMARY KEY,
                candidate_observed_at TEXT NOT NULL,
                candidate_observed_us INTEGER NOT NULL,
                candidate_fingerprint TEXT NOT NULL DEFAULT '',
                evaluated_at TEXT NOT NULL,
                evaluated_us INTEGER NOT NULL,
                model_version TEXT NOT NULL,
                ranking_score REAL NOT NULL,
                visual_coverage REAL NOT NULL,
                evidence_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS candidate_evidence_retention
                ON candidate_evidence(candidate_observed_us);
            """
        )
        columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(candidate_evidence)")
        }
        if "candidate_fingerprint" not in columns:
            self._connection.execute(
                "ALTER TABLE candidate_evidence "
                "ADD COLUMN candidate_fingerprint TEXT NOT NULL DEFAULT ''"
            )
        self._connection.commit()

    def current_fingerprints(self, model_version: str) -> dict[str, str]:
        rows = self._connection.execute(
            """
            SELECT candidate_id, candidate_fingerprint
            FROM candidate_evidence
            WHERE model_version = ?
            """,
            (model_version,),
        )
        return {str(row[0]): str(row[1]) for row in rows}

    def save(self, evidence: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        self._connection.execute(
            """
            INSERT INTO candidate_evidence (
                candidate_id, candidate_observed_at, candidate_observed_us,
                candidate_fingerprint,
                evaluated_at, evaluated_us, model_version,
                ranking_score, visual_coverage, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                candidate_observed_at=excluded.candidate_observed_at,
                candidate_observed_us=excluded.candidate_observed_us,
                candidate_fingerprint=excluded.candidate_fingerprint,
                evaluated_at=excluded.evaluated_at,
                evaluated_us=excluded.evaluated_us,
                model_version=excluded.model_version,
                ranking_score=excluded.ranking_score,
                visual_coverage=excluded.visual_coverage,
                evidence_json=excluded.evidence_json
            """,
            (
                evidence["candidate_id"],
                evidence["candidate_observed_at"],
                evidence["candidate_observed_us"],
                evidence["candidate_fingerprint"],
                now.isoformat(),
                round(now.timestamp() * 1_000_000),
                evidence["model_version"],
                evidence["ranking_score"],
                evidence["visual_coverage"],
                json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        self._connection.commit()

    def cleanup(self, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(days=RETENTION_DAYS)
        cursor = self._connection.execute(
            "DELETE FROM candidate_evidence WHERE candidate_observed_us < ?",
            (round(cutoff.timestamp() * 1_000_000),),
        )
        self._connection.commit()
        return cursor.rowcount

    def count(self) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) FROM candidate_evidence"
            ).fetchone()[0]
        )

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT evidence_json FROM candidate_evidence WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> EvidenceStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
