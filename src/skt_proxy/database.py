import sqlite3
import time
import json
import sys
import logging
import skt_proxy.config as config
from skt_proxy.services.logger import setup_logger, log_siem_event

logger = setup_logger("skt-proxy.db")


def get_db_path(db_path=None):
    if db_path is not None:
        return db_path
    app_module = sys.modules.get("skt_proxy.app") or sys.modules.get("app")
    if app_module and hasattr(app_module, "DB_PATH"):
        return app_module.DB_PATH
    return config.DB_PATH


def get_db_connection(db_path=None):
    path = get_db_path(db_path)
    conn = sqlite3.connect(path, timeout=20.0)
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


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
        try:
            conn.execute("ALTER TABLE torrents ADD COLUMN databazeknih_url TEXT")
        except sqlite3.OperationalError:
            pass

        # Indexes for fast pagination, filtering, and unread lookups
        conn.execute("CREATE INDEX IF NOT EXISTS idx_torrents_added_date ON torrents (added_date DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_torrents_category_id ON torrents (category_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_torrents_is_new ON torrents (is_new);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_torrents_created_at ON torrents (created_at);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS page_cache (
                cache_key TEXT PRIMARY KEY, last_scraped REAL, torrent_ids TEXT
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                category_id TEXT PRIMARY KEY, last_synced REAL, new_items INTEGER DEFAULT 0
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_metadata (
                key TEXT PRIMARY KEY, value TEXT
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
    """Clears is_new flag for any torrent record created older than expiry window."""
    now = time.time()
    cutoff = now - getattr(config, "NEW_FLAG_EXPIRY_SECONDS", 172800)
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
    try:
        with get_db_connection(db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0] == 0
    except sqlite3.OperationalError:
        return True


def is_system_initialized(db_path=None) -> bool:
    try:
        with get_db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM system_metadata WHERE key = 'initialized'"
            ).fetchone()
            return bool(row and row[0] == "1")
    except sqlite3.OperationalError:
        return False


def set_system_initialized(db_path=None):
    with get_db_connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_metadata (key, value) VALUES ('initialized', '1')"
        )


def get_sync_state(category_id="0", db_path=None) -> dict:
    try:
        with get_db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT last_synced, new_items FROM sync_state WHERE category_id = ?",
                (str(category_id),),
            ).fetchone()
            if row:
                return {"last_synced": row[0], "new_items": row[1]}
    except sqlite3.OperationalError:
        pass
    return {"last_synced": 0, "new_items": 0}


def update_sync_state(category_id, last_synced, new_items=0, db_path=None):
    with get_db_connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sync_state (category_id, last_synced, new_items) VALUES (?, ?, ?)",
            (str(category_id), float(last_synced), int(new_items)),
        )


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


def mark_all_read(db_path=None) -> int:
    with get_db_connection(db_path) as conn:
        cursor = conn.execute("UPDATE torrents SET is_new = 0 WHERE is_new = 1")
        count = cursor.rowcount
        log_siem_event(
            logger,
            logging.INFO,
            f"Marked all {count} new torrents as read",
            event="mark_all_read",
            count=count,
        )
        return count


def get_unread_count(db_path=None) -> int:
    try:
        with get_db_connection(db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM torrents WHERE is_new = 1").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


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
