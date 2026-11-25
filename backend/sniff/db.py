import sqlite3
from datetime import datetime

import sqlite_vec

from . import config

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  size INTEGER, mtime REAL, sha256 TEXT,
  doc_type TEXT,
  indexed_at TEXT,
  status TEXT NOT NULL DEFAULT 'new',   -- new | indexed | error
  error TEXT,
  copy_of INTEGER                       -- set when this path holds the same bytes as an indexed file; it has no rows of its own
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  page INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{config.EMBED_DIM}] distance_metric=cosine);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  value_type TEXT NOT NULL,             -- date | money | text | id
  page INTEGER NOT NULL DEFAULT 1,
  verified INTEGER NOT NULL DEFAULT 0,  -- 1 if on the page, next to its field's label, and passes the field's rules
  source_text TEXT,
  status TEXT NOT NULL DEFAULT 'needs_review',  -- verified | needs_review | corrected | approved
  reason TEXT                           -- why it needs review
);
CREATE INDEX IF NOT EXISTS facts_file ON facts(file_id);
CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY,
  fact_id INTEGER NOT NULL,
  title TEXT, date TEXT,
  calendar_event_id TEXT,
  status TEXT NOT NULL,                 -- created | failed
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS watched(folder TEXT PRIMARY KEY, added_at TEXT);  -- folders kept in step live; read again on restart
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- JSON values, e.g. key "mcp"
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, payload TEXT);
"""

MIGRATIONS = [("files", "copy_of", "INTEGER"), ("facts", "status", "TEXT NOT NULL DEFAULT 'needs_review'"), ("facts", "reason", "TEXT")]


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # the API reads while the indexer writes
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:  # databases made before these columns existed
        if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return conn


def log(conn: sqlite3.Connection, kind: str, payload: str) -> None:
    conn.execute("INSERT INTO actions(ts, kind, payload) VALUES (?,?,?)", (datetime.now().isoformat(timespec="seconds"), kind, payload))


def delete_file_data(conn: sqlite3.Connection, file_id: int) -> None:
    """Remove a file's chunks, vectors and facts (not the file row)."""
    conn.execute("DELETE FROM chunk_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id=?)", (file_id,))
    conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM facts WHERE file_id=?", (file_id,))


def release(conn: sqlite3.Connection, file_id: int) -> None:
    """Free a file row's chunks, vectors and facts before it is deleted or re-indexed. If other paths hold copies of
    it, the first copy takes over the rows (and any corrections), so the document survives."""
    heir = conn.execute("SELECT id FROM files WHERE copy_of=? ORDER BY id LIMIT 1", (file_id,)).fetchone()
    if not heir:
        delete_file_data(conn, file_id)
        return
    conn.execute("UPDATE chunks SET file_id=? WHERE file_id=?", (heir["id"], file_id))
    conn.execute("UPDATE facts SET file_id=? WHERE file_id=?", (heir["id"], file_id))
    conn.execute("UPDATE files SET copy_of=NULL WHERE id=?", (heir["id"],))
    conn.execute("UPDATE files SET copy_of=? WHERE copy_of=?", (heir["id"], file_id))
