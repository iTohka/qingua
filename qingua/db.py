from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import DEFAULT_SETTINGS, SECRET_KEYS

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    added_at REAL NOT NULL,
    last_scan_at REAL
);

CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    root_path TEXT NOT NULL,
    parsed_title TEXT,
    parsed_year INTEGER,
    title TEXT,
    year INTEGER,
    season_count INTEGER NOT NULL DEFAULT 1,
    episode_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    provider TEXT,
    provider_id TEXT,
    meta_json TEXT,
    poster_path TEXT,
    fanart_path TEXT,
    nfo_path TEXT,
    confidence INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(folder_id, kind, root_path)
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    season INTEGER,
    episode INTEGER,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    nfo_path TEXT,
    probed_json TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_work ON files(work_id);
CREATE INDEX IF NOT EXISTS idx_works_folder ON works(folder_id);
CREATE INDEX IF NOT EXISTS idx_works_status ON works(status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            with self._connections_lock:
                self._connections.append(conn)
        return conn

    def init(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        self._local.conn = None
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    @contextmanager
    def tx(self):
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        result = dict(DEFAULT_SETTINGS)
        for row in self.connect().execute("SELECT key, value FROM settings"):
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result

    def public_settings(self) -> dict[str, Any]:
        result = self.settings()
        key = result.get("tmdb_api_key", "")
        result["tmdb_key_set"] = bool(key)
        result["tmdb_api_key"] = ""
        return result

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        conn = self.connect()
        with self.tx():
            for key, value in patch.items():
                if key not in allowed:
                    continue
                if key in SECRET_KEYS and value == "":
                    continue
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, _json(value)),
                )
        return self.public_settings()

    def clear_secret(self, key: str) -> None:
        if key in SECRET_KEYS:
            self.connect().execute("DELETE FROM settings WHERE key=?", (key,))

    # ------------------------------------------------------------------ folders
    def add_folder(self, path: str) -> dict[str, Any]:
        now = time.time()
        conn = self.connect()
        conn.execute(
            "INSERT INTO folders(path, added_at) VALUES(?, ?) "
            "ON CONFLICT(path) DO NOTHING",
            (path, now),
        )
        row = conn.execute("SELECT * FROM folders WHERE path=?", (path,)).fetchone()
        return dict(row)

    def remove_folder(self, folder_id: int) -> None:
        self.connect().execute("DELETE FROM folders WHERE id=?", (folder_id,))

    def folders(self) -> list[dict[str, Any]]:
        rows = self.connect().execute(
            "SELECT f.*, "
            "(SELECT COUNT(*) FROM works w WHERE w.folder_id=f.id) AS work_count "
            "FROM folders f ORDER BY f.added_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_folder(self, folder_id: int) -> dict[str, Any] | None:
        row = self.connect().execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
        return dict(row) if row else None

    def touch_folder(self, folder_id: int) -> None:
        self.connect().execute("UPDATE folders SET last_scan_at=? WHERE id=?", (time.time(), folder_id))

    # ------------------------------------------------------------------ works
    def work_exists(self, folder_id: int, kind: str, root_path: str) -> dict[str, Any] | None:
        row = self.connect().execute(
            "SELECT * FROM works WHERE folder_id=? AND kind=? AND root_path=?",
            (folder_id, kind, root_path),
        ).fetchone()
        return dict(row) if row else None

    def create_work(
        self,
        folder_id: int,
        kind: str,
        root_path: str,
        parsed_title: str,
        parsed_year: int | None,
        season_count: int,
        episode_count: int,
        confidence: int,
    ) -> int:
        now = time.time()
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO works(folder_id, kind, root_path, parsed_title, parsed_year, "
                "title, year, season_count, episode_count, status, confidence, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,'new',?,?,?)",
                (
                    folder_id,
                    kind,
                    root_path,
                    parsed_title,
                    parsed_year,
                    parsed_title,
                    parsed_year,
                    season_count,
                    episode_count,
                    confidence,
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def update_work_scan(
        self,
        work_id: int,
        parsed_title: str,
        parsed_year: int | None,
        season_count: int,
        episode_count: int,
        confidence: int,
    ) -> None:
        self.connect().execute(
            "UPDATE works SET parsed_title=?, parsed_year=?, season_count=?, episode_count=?, "
            "confidence=?, updated_at=? WHERE id=?",
            (parsed_title, parsed_year, season_count, episode_count, confidence, time.time(), work_id),
        )

    def replace_files(self, work_id: int, files: Iterable[dict[str, Any]]) -> None:
        conn = self.connect()
        with self.tx():
            existing = {
                row["path"]: row["id"]
                for row in conn.execute("SELECT id, path FROM files WHERE work_id=?", (work_id,))
            }
            seen = set()
            for item in files:
                path = item["path"]
                seen.add(path)
                row = conn.execute(
                    "SELECT id, work_id FROM files WHERE path=?", (path,)
                ).fetchone()
                if row:
                    # A rescan can regroup files that previously belonged to a
                    # movie work into one TV work; move the row instead of
                    # violating the UNIQUE(path) constraint.
                    conn.execute(
                        "UPDATE files SET work_id=?, season=?, episode=?, size=?, mtime=? WHERE id=?",
                        (
                            work_id,
                            item.get("season"),
                            item.get("episode"),
                            item.get("size", 0),
                            item.get("mtime", 0),
                            row["id"],
                        ),
                    )
                else:
                    conn.execute(
                        "INSERT INTO files(work_id, path, season, episode, size, mtime) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            work_id,
                            path,
                            item.get("season"),
                            item.get("episode"),
                            item.get("size", 0),
                            item.get("mtime", 0),
                        ),
                    )
            for path, file_id in existing.items():
                if path not in seen:
                    conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    def delete_orphan_works(self) -> int:
        cur = self.connect().execute(
            "DELETE FROM works WHERE id NOT IN (SELECT DISTINCT work_id FROM files)"
        )
        return cur.rowcount

    def prune_folder_files(self, folder_id: int, seen_paths: set[str]) -> int:
        conn = self.connect()
        rows = conn.execute(
            "SELECT fi.id, fi.path FROM files fi JOIN works w ON w.id=fi.work_id "
            "WHERE w.folder_id=?",
            (folder_id,),
        ).fetchall()
        removed = 0
        with self.tx():
            for row in rows:
                if row["path"] not in seen_paths:
                    conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
                    removed += 1
            conn.execute(
                "DELETE FROM works WHERE folder_id=? AND id NOT IN "
                "(SELECT DISTINCT work_id FROM files)",
                (folder_id,),
            )
        return removed

    def get_work(self, work_id: int) -> dict[str, Any] | None:
        row = self.connect().execute(
            "SELECT w.*, f.path AS folder_path FROM works w JOIN folders f ON f.id=w.folder_id "
            "WHERE w.id=?",
            (work_id,),
        ).fetchone()
        if not row:
            return None
        work = dict(row)
        work["meta"] = json.loads(work["meta_json"]) if work.get("meta_json") else None
        work["files"] = self.files_for_work(work_id)
        work["poster"] = bool(work.get("poster_path") and Path(work["poster_path"]).exists())
        work["fanart"] = bool(work.get("fanart_path") and Path(work["fanart_path"]).exists())
        return work

    def files_for_work(self, work_id: int) -> list[dict[str, Any]]:
        rows = self.connect().execute(
            "SELECT * FROM files WHERE work_id=? ORDER BY season, episode, path", (work_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def all_works(self, folder_id: int | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT w.*, f.path AS folder_path, "
            "(SELECT COUNT(*) FROM files fi WHERE fi.work_id=w.id) AS file_count "
            "FROM works w JOIN folders f ON f.id=w.folder_id"
        )
        args: list[Any] = []
        if folder_id is not None:
            sql += " WHERE w.folder_id=?"
            args.append(folder_id)
        sql += " ORDER BY w.updated_at DESC, w.id DESC"
        return [dict(r) for r in self.connect().execute(sql, args).fetchall()]

    def list_works(
        self,
        *,
        folder_id: int | None = None,
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT w.*, f.path AS folder_path, "
            "(SELECT COUNT(*) FROM files fi WHERE fi.work_id=w.id) AS file_count "
            "FROM works w JOIN folders f ON f.id=w.folder_id WHERE 1=1"
        )
        args: list[Any] = []
        if folder_id is not None:
            sql += " AND w.folder_id=?"
            args.append(folder_id)
        if status and status != "all":
            if status == "attention":
                sql += " AND w.status IN ('review','error')"
            else:
                sql += " AND w.status=?"
                args.append(status)
        if kind and kind != "all":
            sql += " AND w.kind=?"
            args.append(kind)
        if query:
            like = f"%{query.strip()}%"
            sql += " AND (COALESCE(w.title,'') LIKE ? OR COALESCE(w.parsed_title,'') LIKE ? OR w.root_path LIKE ?)"
            args.extend([like, like, like])
        sql += " ORDER BY CASE w.status WHEN 'review' THEN 0 WHEN 'new' THEN 1 WHEN 'error' THEN 2 ELSE 3 END, w.updated_at DESC"
        sql += " LIMIT ?"
        args.append(max(1, min(limit, 2000)))
        rows = self.connect().execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def set_work_match(
        self,
        work_id: int,
        *,
        title: str,
        year: int | None,
        provider: str | None,
        provider_id: str | None,
        meta: dict[str, Any] | None,
        status: str,
        error: str | None = None,
        confidence: int | None = None,
    ) -> None:
        sets = [
            "title=?",
            "year=?",
            "provider=?",
            "provider_id=?",
            "meta_json=?",
            "status=?",
            "error=?",
            "updated_at=?",
        ]
        args: list[Any] = [
            title,
            year,
            provider,
            provider_id,
            _json(meta) if meta is not None else None,
            status,
            error,
            time.time(),
        ]
        if confidence is not None:
            sets.append("confidence=?")
            args.append(confidence)
        args.append(work_id)
        self.connect().execute(f"UPDATE works SET {', '.join(sets)} WHERE id=?", args)

    def set_work_status(self, work_id: int, status: str, error: str | None = None) -> None:
        self.connect().execute(
            "UPDATE works SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, time.time(), work_id),
        )

    def set_work_title(self, work_id: int, title: str) -> None:
        self.connect().execute(
            "UPDATE works SET title=?, updated_at=? WHERE id=?",
            (title, time.time(), work_id),
        )

    def set_work_artwork(
        self,
        work_id: int,
        poster_path: str | None,
        fanart_path: str | None,
        nfo_path: str | None,
    ) -> None:
        self.connect().execute(
            "UPDATE works SET poster_path=?, fanart_path=?, nfo_path=?, updated_at=? WHERE id=?",
            (poster_path, fanart_path, nfo_path, time.time(), work_id),
        )

    def update_work_paths(self, work_id: int, root_path: str, files: list[tuple[int, str]]) -> None:
        conn = self.connect()
        with self.tx():
            conn.execute("UPDATE works SET root_path=?, updated_at=? WHERE id=?", (root_path, time.time(), work_id))
            for file_id, path in files:
                conn.execute("UPDATE files SET path=? WHERE id=?", (path, file_id))

    def delete_work(self, work_id: int) -> None:
        self.connect().execute("DELETE FROM works WHERE id=?", (work_id,))

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict[str, Any]:
        conn = self.connect()
        totals = conn.execute(
            "SELECT COUNT(*) AS works, "
            "SUM(CASE WHEN kind='movie' THEN 1 ELSE 0 END) AS movies, "
            "SUM(CASE WHEN kind='tv' THEN 1 ELSE 0 END) AS shows, "
            "SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) AS applied, "
            "SUM(CASE WHEN status='matched' THEN 1 ELSE 0 END) AS matched, "
            "SUM(CASE WHEN status='review' THEN 1 ELSE 0 END) AS review, "
            "SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS new, "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
            "FROM works"
        ).fetchone()
        result = {k: int(v or 0) for k, v in dict(totals).items()}
        result["episodes"] = int(
            conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 0
        )
        result["folders"] = int(conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] or 0)
        return result

    # ------------------------------------------------------------------ cache
    def cache_get(self, key: str, ttl: float) -> Any | None:
        row = self.connect().execute("SELECT value, created_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if time.time() - row["created_at"] > ttl:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return None

    def cache_set(self, key: str, value: Any) -> None:
        self.connect().execute(
            "INSERT INTO cache(key, value, created_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at",
            (key, _json(value), time.time()),
        )

    def cache_delete(self, key: str) -> None:
        self.connect().execute("DELETE FROM cache WHERE key=?", (key,))

    def cache_clear(self) -> None:
        self.connect().execute("DELETE FROM cache")

    # ------------------------------------------------------------------ jobs
    def create_job(self, kind: str, total: int = 0) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.connect().execute(
            "INSERT INTO jobs(id, kind, status, total, done, message, created_at, updated_at) "
            "VALUES(?,?,?,?,0,?,?,?)",
            (job_id, kind, "queued", total, "等待开始", now, now),
        )
        return job_id

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        done: int | None = None,
        total: int | None = None,
        message: str | None = None,
        result: Any | None = None,
    ) -> None:
        sets: list[str] = ["updated_at=?"]
        args: list[Any] = [time.time()]
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if done is not None:
            sets.append("done=?")
            args.append(done)
        if total is not None:
            sets.append("total=?")
            args.append(total)
        if message is not None:
            sets.append("message=?")
            args.append(message)
        if result is not None:
            sets.append("result_json=?")
            args.append(_json(result))
        args.append(job_id)
        self.connect().execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", args)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.connect().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        job["result"] = json.loads(job.pop("result_json")) if job.get("result_json") else None
        job["cancel_requested"] = bool(job.get("cancel_requested"))
        return job

    def request_cancel(self, job_id: str) -> None:
        self.connect().execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))

    def job_cancelled(self, job_id: str) -> bool:
        row = self.connect().execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row[0])

    # ------------------------------------------------------------------ history
    def add_history(self, kind: str, payload: dict[str, Any]) -> None:
        self.connect().execute(
            "INSERT INTO history(kind, payload_json, created_at) VALUES(?,?,?)",
            (kind, _json(payload), time.time()),
        )

    def recent_history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connect().execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
