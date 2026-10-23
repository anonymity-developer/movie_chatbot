"""SQLite state for collection jobs, scoped to a browser session."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


class JobRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("""
                CREATE TABLE IF NOT EXISTS collection_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    movie_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def create(self, session_id: str, movie_id: str) -> str:
        job_id = str(uuid4())
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO collection_jobs (job_id, session_id, movie_id, stage, status) VALUES (?, ?, ?, 'queued', 'pending')",
                    (job_id, session_id, movie_id),
                )
        return job_id

    def update(self, job_id: str, stage: str, status: str = "running", *, result: dict | None = None, error_code: str | None = None) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE collection_jobs SET stage=?, status=?, result_json=?, error_code=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                    (stage, status, json.dumps(result, ensure_ascii=False) if result is not None else None, error_code, job_id),
                )

    def get(self, job_id: str, session_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT movie_id, stage, status, result_json, error_code FROM collection_jobs WHERE job_id=? AND session_id=?",
                (job_id, session_id),
            ).fetchone()
        if row is None:
            return None
        movie_id, stage, status, result_json, error_code = row
        return {
            "job_id": job_id,
            "movie_id": movie_id,
            "stage": stage,
            "status": status,
            "result": json.loads(result_json) if result_json else None,
            "error_code": error_code,
        }
