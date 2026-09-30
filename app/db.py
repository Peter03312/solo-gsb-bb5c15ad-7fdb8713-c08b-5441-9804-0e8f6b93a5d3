"""SQLite 持久化层。

资料修订号（slots/containers/ship）随对应资料整套替换而递增；
方案修订号（plan_revision）为库内方案序号，每次求解保存草稿 +1。
occupancies 对 slot_id 设唯一约束，保证已确认槽位绝不被重复占用。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

from . import config

# 进程内串行化“写事务 + 确认”，配合库级 UNIQUE 约束双保险防并发冲突
_commit_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS slots (
    slot_id           TEXT PRIMARY KEY,
    stack             TEXT NOT NULL,
    tier              INTEGER NOT NULL,
    max_weight        REAL NOT NULL,
    longitudinal_arm  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS adjacency (
    slot_a TEXT NOT NULL,
    slot_b TEXT NOT NULL,
    PRIMARY KEY (slot_a, slot_b)
);

CREATE TABLE IF NOT EXISTS containers (
    container_no    TEXT PRIMARY KEY,
    weight          REAL NOT NULL,
    hazmat_class    TEXT,
    discharge_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS incompatible_pairs (
    class_a TEXT NOT NULL,
    class_b TEXT NOT NULL,
    PRIMARY KEY (class_a, class_b)
);

CREATE TABLE IF NOT EXISTS ship_config (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    min_moment REAL NOT NULL,
    max_moment REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    plan_revision       INTEGER PRIMARY KEY,
    status              TEXT NOT NULL,           -- draft / confirmed
    slots_revision      INTEGER NOT NULL,
    containers_revision INTEGER NOT NULL,
    max_occupied_tier   INTEGER NOT NULL,
    total_moment        REAL NOT NULL,
    assignments_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS occupancies (
    slot_id       TEXT PRIMARY KEY,   -- 唯一：槽位已确认占用即不可再用
    plan_revision INTEGER NOT NULL,
    container_no  TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    if config.DB_PATH != ":memory:":
        parent = os.path.dirname(os.path.abspath(config.DB_PATH))
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db(seed_sample: bool = False) -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for key in ("slots_revision", "containers_revision", "ship_revision",
                    "plan_seq"):
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES (?, 0)", (key,))
    if seed_sample:
        from .sample import seed_if_empty
        seed_if_empty()


# ---------------- 修订号 ----------------

def get_revisions(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT key, value FROM meta").fetchall()
    return {r["key"]: r["value"] for r in rows}


def bump_revision(conn: sqlite3.Connection, key: str) -> int:
    conn.execute("UPDATE meta SET value = value + 1 WHERE key = ?", (key,))
    return conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()["value"]


def next_plan_revision(conn: sqlite3.Connection) -> int:
    return bump_revision(conn, "plan_seq")


@contextmanager
def write_tx(immediate: bool = False):
    """写事务上下文。immediate=True 时以 BEGIN IMMEDIATE 立即取写锁。"""
    with _commit_lock:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
