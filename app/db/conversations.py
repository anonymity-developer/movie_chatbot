"""Movie-specific conversation history scoped to an anonymous browser session."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


class ConversationRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    movie_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(session_id, movie_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_messages_conversation
                    ON messages(conversation_id, message_id);"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def history(self, session_id: str, movie_id: str) -> tuple[str | None, list[dict]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT conversation_id FROM conversations WHERE session_id=? AND movie_id=?",
                (session_id, movie_id),
            ).fetchone()
            if row is None:
                return None, []
            conversation_id = row[0]
            rows = connection.execute(
                "SELECT role,content,citations_json,created_at FROM messages "
                "WHERE conversation_id=? ORDER BY message_id",
                (conversation_id,),
            ).fetchall()
        return conversation_id, [
            {"role": role, "content": content, "citations": json.loads(citations), "created_at": created_at}
            for role, content, citations, created_at in rows
        ]

    def append_exchange(
        self, session_id: str, movie_id: str, question: str,
        answer: str, citations: list[dict],
    ) -> str:
        with self._connect() as connection:
            proposed_id = str(uuid4())
            connection.execute(
                "INSERT OR IGNORE INTO conversations(conversation_id,session_id,movie_id) VALUES(?,?,?)",
                (proposed_id, session_id, movie_id),
            )
            row = connection.execute(
                "SELECT conversation_id FROM conversations WHERE session_id=? AND movie_id=?",
                (session_id, movie_id),
            ).fetchone()
            conversation_id = row[0]
            connection.executemany(
                "INSERT INTO messages(conversation_id,role,content,citations_json) VALUES(?,?,?,?)",
                [
                    (conversation_id, "user", question, "[]"),
                    (conversation_id, "assistant", answer, json.dumps(citations, ensure_ascii=False)),
                ],
            )
        return conversation_id
