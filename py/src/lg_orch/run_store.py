from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

_COLUMNS = (
    "run_id",
    "request",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "exit_code",
    "trace_out_dir",
    "trace_path",
    "request_id",
    "auth_subject",
    "client_ip",
)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    request      TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    exit_code    INTEGER,
    trace_out_dir TEXT NOT NULL,
    trace_path   TEXT NOT NULL,
    request_id   TEXT NOT NULL DEFAULT '',
    auth_subject TEXT NOT NULL DEFAULT '',
    client_ip    TEXT NOT NULL DEFAULT ''
)
"""


class RunStore:
    def __init__(self, *, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_CREATE_TABLE)
            self._conn.commit()

    def upsert(self, record: dict[str, Any]) -> None:
        filtered = {k: record[k] for k in _COLUMNS if k in record}
        if not filtered:
            return
        cols = ", ".join(filtered.keys())
        placeholders = ", ".join("?" for _ in filtered)
        sql = f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute(sql, list(filtered.values()))
            self._conn.commit()

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row is not None else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
