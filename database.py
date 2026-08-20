import sqlite3
import time
import json
import sys
import logging
import config
from services.logger import setup_logger, log_siem_event

logger = setup_logger("skt-proxy.db")


def get_db_path(db_path=None):
    if db_path is not None:
        return db_path
    app_module = sys.modules.get("app")
    if app_module and hasattr(app_module, "DB_PATH"):
        return app_module.DB_PATH
    return config.DB_PATH


def get_db_connection(db_path=None):
    path = get_db_path(db_path)
    return sqlite3.connect(path, timeout=20.0)


def init_db(db_path=None):
    with get_db_connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS torrents (
                id TEXT PRIMARY KEY, title TEXT, category TEXT, category_id TEXT, 
                genres TEXT, size TEXT, added_date TEXT, seeders INTEGER, 
                leechers INTEGER, download_link TEXT, is_new BOOLEAN, 
                image_url TEXT, local_image TEXT, csfd_id TEXT, csfd_score TEXT,
                created_at REAL
            )
        """
        )
        try:
            conn.execute("ALTER TABLE torrents ADD COLUMN csfd_score TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE torrents ADD COLUMN created_at REAL")
        except sqlite3.OperationalError:
            pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS page_cache (
                cache_key TEXT PRIMARY KEY, last_scraped REAL, torrent_ids TEXT
            )
        """
        )
    optimize_db(db_path)


def optimize_db(db_path=None):
    """Executes passive WAL checkpointing and query optimizer stats compilation."""
    try:
        with get_db_connection(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
            conn.execute("PRAGMA optimize;")
    except Exception as e:
        logger.warning(f"WAL checkpoint optimize warning: {e}")


def clear_expired_new_flags(db_path=None):
    """Clears is_new flag for any torrent record created >48 hours ago (172800s)."""
    now = time.time()
    cutoff = now - 172800
    with get_db_connection(db_path) as conn:
        cursor = conn.execute(
            "UPDATE torrents SET is_new = 0 WHERE is_new = 1 AND created_at IS NOT NULL AND created_at < ?",
            (cutoff,),
        )
        if cursor.rowcount > 0:
            log_siem_event(
                logger,
                logging.INFO,
                f"Cleared expired new flags on {cursor.rowcount} records",
                event="clear_expired_new_flags",
                count=cursor.rowcount,
            )


def is_db_empty(db_path=None):
    with get_db_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0] == 0


def mark_read(tids, db_path=None):
    if not tids:
        return
    with get_db_connection(db_path) as conn:
        placeholders = ",".join("?" * len(tids))
        conn.execute(
            f"UPDATE torrents SET is_new = 0 WHERE id IN ({placeholders})", tids
        )
        log_siem_event(
            logger,
            logging.INFO,
            f"Marked {len(tids)} torrents as read",
            event="mark_read",
            count=len(tids),
        )


def get_categories(db_path=None):
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT category_id, category FROM torrents WHERE category_id IS NOT NULL AND category_id != '0' ORDER BY category"
        ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


def get_genres(db_path=None):
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT genres FROM torrents WHERE genres IS NOT NULL AND genres != '[]'"
        ).fetchall()

    all_genres = set()
    for r in rows:
        try:
            for g in json.loads(r[0]):
                all_genres.add(g)
        except (json.JSONDecodeError, TypeError):
            pass
    return sorted(list(all_genres))
