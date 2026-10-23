"""Persistent cache of completed document collection queries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def query_key(source: str, movie_id: str, term: str) -> str:
    normalized = " ".join(term.casefold().split())
    value = json.dumps([source, movie_id, normalized], ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CollectionCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS collection_cache (
                    query_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    movie_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    documents_json TEXT NOT NULL,
                    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, source: str, movie_id: str, term: str) -> list[dict[str, Any]] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT documents_json FROM collection_cache WHERE query_key = ?",
                (query_key(source, movie_id, term),),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def put(self, source: str, movie_id: str, term: str, documents: list[dict[str, Any]]) -> None:
        encoded = json.dumps(documents, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO collection_cache(query_key, source, movie_id, term, documents_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(query_key) DO UPDATE SET
                     documents_json=excluded.documents_json,
                     collected_at=CURRENT_TIMESTAMP""",
                (query_key(source, movie_id, term), source, movie_id, term, encoded),
            )
