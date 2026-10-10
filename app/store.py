from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


def empty_session() -> dict:
    return {
        "job_description": "",
        "pdf_name": "",
        "extraction_status": "",
        "extraction_message": "",
        "contact": {"name": "", "email": "", "phone": "", "location": "", "links": []},
        "experience": [],
        "education": [],
        "skills_text": "",
        "summary_text": "",
        "other_sections": [],
        "experience_source": "",
        "raw_text": "",
        "github": {
            "visibility": "public",
            "username": "",
            "token": "",
            "auth_method": "",
            "oauth_state": "",
        },
        "github_warning": "",
        "eligible_repos": [],
        "recommendations": [],
        "draft": None,
    }


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path = data_dir / "sessions.sqlite"
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, session_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return empty_session()
        data = json.loads(row["payload"])
        base = empty_session()
        base.update(data)
        base["github"] = {**empty_session()["github"], **(data.get("github") or {})}
        return base

    def save(self, session_id: str, payload: dict) -> None:
        encoded = json.dumps(payload)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (session_id, encoded, time.time()),
            )
