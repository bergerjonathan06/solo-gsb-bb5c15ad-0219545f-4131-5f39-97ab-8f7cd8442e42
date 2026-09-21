"""SQLite 数据访问层：启动时初始化表结构，按请求提供连接。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "intermod.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    scan_start_hz     INTEGER NOT NULL,
    scan_end_hz       INTEGER NOT NULL,
    guard_interval_hz INTEGER NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS carriers (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    freq_hz INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS guard_bands (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id  INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    start_hz INTEGER NOT NULL,
    end_hz   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id     INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    result_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_carriers_site ON carriers(site_id);
CREATE INDEX IF NOT EXISTS idx_guard_bands_site ON guard_bands(site_id);
CREATE INDEX IF NOT EXISTS idx_analyses_site ON analyses(site_id);
"""


def db_path() -> str:
    """数据库文件路径，可用环境变量 IMD_DB_PATH 覆盖。"""
    return os.getenv("IMD_DB_PATH", DEFAULT_DB_PATH)


def init_db() -> None:
    """创建数据库目录与表结构（幂等，应用启动时调用）。"""
    path = db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI 依赖：每个请求一个连接。"""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
