"""Local SQLite persistence for normalized simulation runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def history_path(project_root: Path) -> Path:
    return project_root / ".veriforge" / "history.sqlite3"


def record_run(project_root: Path, result: dict, log_path: str) -> int:
    """Append a run and its test results; return the durable run ID."""
    database = history_path(project_root)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                simulator TEXT NOT NULL,
                top_module TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'blocked')),
                duration_seconds REAL NOT NULL,
                return_code INTEGER,
                waveform TEXT,
                log_path TEXT NOT NULL,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS run_tests (
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                test_id TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                PRIMARY KEY (run_id, test_id)
            );
            CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs(started_at DESC, id DESC);
            """
        )
        cursor = connection.execute(
            """INSERT INTO runs (
                   started_at, simulator, top_module, status, duration_seconds,
                   return_code, waveform, log_path, message
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result["started_at"],
                result["simulator"],
                result["top_module"],
                result["status"],
                float(result.get("duration_seconds", 0.0)),
                result.get("return_code"),
                result.get("waveform"),
                log_path,
                result.get("message"),
            ),
        )
        run_id = int(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO run_tests (run_id, test_id, status, detail) VALUES (?, ?, ?, ?)",
            [
                (run_id, item["id"], item["status"], item.get("detail", ""))
                for item in result.get("tests", [])
            ],
        )
    return run_id


def list_runs(project_root: Path, limit: int = 20) -> list[dict]:
    """Return recent runs with compact test totals, newest first."""
    if limit < 1:
        raise ValueError("limit must be a positive integer")
    database = history_path(project_root)
    if not database.is_file():
        return []
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT r.*,
                      COUNT(t.test_id) AS test_count,
                      SUM(CASE WHEN t.status = 'pass' THEN 1 ELSE 0 END) AS passed_tests
               FROM runs AS r
               LEFT JOIN run_tests AS t ON t.run_id = r.id
               GROUP BY r.id
               ORDER BY r.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
