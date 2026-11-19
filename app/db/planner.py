"""SQLite persistence for planner memory, saved plans, and append-only events."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4


class PlannerRepository:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS planner_memory (
                    session_id TEXT PRIMARY KEY, conditions TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS planner_turns (
                    session_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS planner_calendar (
                    session_id TEXT NOT NULL, event_id TEXT NOT NULL, title TEXT NOT NULL,
                    date TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS planner_plans (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, title TEXT NOT NULL,
                    date TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_planner_plans ON planner_plans(session_id, date);
                CREATE TABLE IF NOT EXISTS planner_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp TEXT NOT NULL,
                    event TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_planner_events ON planner_events(session_id, run_id, sequence);
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def memory(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT conditions FROM planner_memory WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row["conditions"]) if row else {}

    def previous_turn(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM planner_turns WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row["payload"]) if row else {}

    def update_memory(self, session_id, before, after, turn=None):
        changes = {key: value for key, value in after.items() if before.get(key) != value}
        if not changes and turn is None:
            return
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT conditions FROM planner_memory WHERE session_id=?", (session_id,)).fetchone()
            latest = json.loads(row["conditions"]) if row else {}
            if changes:
                db.execute("INSERT INTO planner_memory VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET conditions=excluded.conditions",
                           (session_id, json.dumps({**latest, **changes}, ensure_ascii=False)))
            if turn is not None:
                db.execute("INSERT INTO planner_turns VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload",
                           (session_id, json.dumps(turn, ensure_ascii=False)))

    def calendar_intervals(self, session_id, day):
        with self.connect() as db:
            rows = db.execute("""
                SELECT starts_at, ends_at FROM planner_calendar WHERE session_id=? AND date=? ORDER BY starts_at
            """, (session_id, day)).fetchall()
        return [dict(row) for row in rows]

    def register_calendar_plan(self, session_id, plan):
        with self.connect() as db:
            db.execute("""
                INSERT INTO planner_calendar VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id,event_id) DO UPDATE SET title=excluded.title, date=excluded.date,
                    starts_at=excluded.starts_at, ends_at=excluded.ends_at
            """, (session_id, plan["id"], plan["title"], plan["date"], plan["starts_at"], plan["ends_at"]))

    def save_event(self, session_id, run_id, entry):
        with self.connect() as db:
            db.execute(
                "INSERT INTO planner_events(session_id, run_id, sequence, timestamp, event, data) VALUES (?,?,?,?,?,?)",
                (session_id, run_id, entry["sequence"], entry["timestamp"], entry["event"], json.dumps(entry["data"], ensure_ascii=False)),
            )

    def runs(self, session_id):
        with self.connect() as db:
            rows = db.execute("""
                SELECT run_id, MIN(timestamp) AS started_at, COUNT(*) AS event_count
                FROM planner_events WHERE session_id=? GROUP BY run_id ORDER BY MIN(id) DESC
            """, (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def events(self, session_id, run_id):
        with self.connect() as db:
            rows = db.execute("""
                SELECT sequence, timestamp, event, data FROM planner_events
                WHERE session_id=? AND run_id=? ORDER BY sequence
            """, (session_id, run_id)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def delete_run(self, session_id, run_id):
        with self.connect() as db:
            return db.execute("DELETE FROM planner_events WHERE session_id=? AND run_id=?", (session_id, run_id)).rowcount

    def plans(self, session_id):
        with self.connect() as db:
            rows = db.execute("""
                SELECT id, title, date, starts_at, ends_at, created_at FROM planner_plans
                WHERE session_id=? ORDER BY date, starts_at, id
            """, (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def plan(self, session_id, plan_id):
        with self.connect() as db:
            row = db.execute("""
                SELECT id, title, date, starts_at, ends_at, created_at FROM planner_plans
                WHERE session_id=? AND id=?
            """, (session_id, plan_id)).fetchone()
        return dict(row) if row else None

    def write_plan(self, session_id, data, plan_id=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            fields = (data["title"], data["date"], data["starts_at"], data["ends_at"])
            if plan_id:
                result = db.execute("""
                    UPDATE planner_plans SET title=?, date=?, starts_at=?, ends_at=?
                    WHERE session_id=? AND id=?
                """, (*fields, session_id, plan_id))
                if not result.rowcount:
                    return None
            else:
                plan_id = str(uuid4())
                db.execute("INSERT INTO planner_plans VALUES (?,?,?,?,?,?,?)",
                           (plan_id, session_id, *fields, datetime.now(timezone.utc).isoformat()))
            plan = db.execute("SELECT created_at FROM planner_plans WHERE id=?", (plan_id,)).fetchone()
            overlaps = db.execute("""
                SELECT id, title, date, starts_at, ends_at, created_at FROM planner_plans
                WHERE session_id=? AND date=? AND id<>? AND starts_at<? AND ends_at>?
                ORDER BY starts_at
            """, (session_id, data["date"], plan_id, data["ends_at"], data["starts_at"])).fetchall()
            return {"plan": {"id": plan_id, **data, "created_at": plan["created_at"]},
                    "overlaps": [dict(row) for row in overlaps]}

    def delete_plan(self, session_id, plan_id):
        with self.connect() as db:
            return db.execute("DELETE FROM planner_plans WHERE session_id=? AND id=?", (session_id, plan_id)).rowcount
