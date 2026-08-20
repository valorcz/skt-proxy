import os
import re
import sys
import time
import json
import sqlite3
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import config
import database
from services.http_client import get_http_session, ensure_login
from services.torrent_parser import parse_torrents
from services.logger import setup_logger, log_siem_event
from services.models import TorrentDTO

logger = setup_logger("skt-proxy.scraper")

background_worker = ThreadPoolExecutor(max_workers=4)
DB_WAS_EMPTY = database.is_db_empty()


def set_db_was_empty(val: bool):
    global DB_WAS_EMPTY
    DB_WAS_EMPTY = val


def get_covers_dir():
    app_module = sys.modules.get("app")
    return getattr(app_module, "COVERS_DIR", config.COVERS_DIR)


def fetch_extras_task(torrent_id, image_url, needs_img, needs_csfd, db_path=None):
    session = get_http_session()
    local_img_path = None
    covers_dir = get_covers_dir()

    if needs_img and image_url:
        ext = image_url.split(".")[-1].split("?")[0]
        if len(ext) > 4 or not ext:
            ext = "jpg"
        local_filename = f"{torrent_id}.{ext}"
        local_path = os.path.join(covers_dir, local_filename)

        if not os.path.exists(local_path):
            try:
                resp = session.get(image_url, timeout=10)
                if resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    local_img_path = f"/static/covers/{local_filename}"
            except Exception as e:
                log_siem_event(
                    logger,
                    logging.WARNING,
                    f"Image fetch failed for {torrent_id}: {e}",
                    event="cover_fetch_failed",
                    torrent_id=torrent_id,
                    error=str(e),
                )
        else:
            local_img_path = f"/static/covers/{local_filename}"

    csfd_id = None
    if needs_csfd:
        try:
            resp = session.get(
                f"https://sktorrent.eu/torrent/details.php?id={torrent_id}", timeout=10
            )
            if resp.status_code == 200:
                match = re.search(r"csfd\.cz/film/(\d+)", resp.text)
                if match:
                    csfd_id = match.group(1)
        except Exception as e:
            log_siem_event(
                logger,
                logging.WARNING,
                f"CSFD ID fetch failed for {torrent_id}: {e}",
                event="csfd_fetch_failed",
                torrent_id=torrent_id,
                error=str(e),
            )

    if local_img_path or csfd_id:
        with database.get_db_connection(db_path) as conn:
            if local_img_path and csfd_id:
                conn.execute(
                    "UPDATE torrents SET local_image=?, csfd_id=? WHERE id=?",
                    (local_img_path, csfd_id, torrent_id),
                )
            elif local_img_path:
                conn.execute(
                    "UPDATE torrents SET local_image=? WHERE id=?",
                    (local_img_path, torrent_id),
                )
            elif csfd_id:
                conn.execute(
                    "UPDATE torrents SET csfd_id=? WHERE id=?", (csfd_id, torrent_id)
                )


def scrape_and_update(page, category, cache_key, db_path=None):
    global DB_WAS_EMPTY
    start_time = time.time()
    session = get_http_session()
    try:
        if not ensure_login(session):
            log_siem_event(
                logger,
                logging.ERROR,
                "Scrape aborted due to authentication failure",
                event="scrape_aborted_auth",
                page=page,
                category=category,
            )
            return []

        url = f"https://sktorrent.eu/torrent/torrents_v2.php?active=0&page={page}"
        if category and str(category) != "0":
            url += f"&category={category}"

        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        torrents = parse_torrents(resp.text)

        torrent_ids = []
        now = time.time()

        with database.get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            for t in torrents:
                torrent_ids.append(t["id"])
                cursor.execute(
                    "SELECT is_new, local_image, csfd_id, csfd_score, created_at FROM torrents WHERE id=?",
                    (t["id"],),
                )
                row = cursor.fetchone()

                is_new = not DB_WAS_EMPTY if not row else row[0]
                local_img = row[1] if row else None
                csfd_id = row[2] if row else None
                stored_score = row[3] if row and len(row) > 3 else None
                csfd_score = t.get("csfd_score") or stored_score
                created_at = row[4] if row and len(row) > 4 and row[4] else now

                if created_at < (now - 172800):
                    is_new = False

                if not local_img or not csfd_id:
                    background_worker.submit(
                        fetch_extras_task,
                        t["id"],
                        t["image_url"],
                        not local_img,
                        not csfd_id,
                        db_path,
                    )

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO torrents
                    (id, title, category, category_id, genres, size, added_date, seeders, leechers, download_link, is_new, image_url, local_image, csfd_id, csfd_score, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        t["id"],
                        t["title"],
                        t["category"],
                        t["category_id"],
                        json.dumps(t["genres"]),
                        t["size"],
                        t["added_date"],
                        t["seeders"],
                        t["leechers"],
                        t["download_link"],
                        is_new,
                        t["image_url"],
                        local_img,
                        csfd_id,
                        csfd_score,
                        created_at,
                    ),
                )

            cursor.execute(
                """INSERT OR REPLACE INTO page_cache (cache_key, last_scraped, torrent_ids) VALUES (?, ?, ?)""",
                (cache_key, now, json.dumps(torrent_ids)),
            )

        if DB_WAS_EMPTY:
            DB_WAS_EMPTY = False

        duration_ms = int((time.time() - start_time) * 1000)
        log_siem_event(
            logger,
            logging.INFO,
            f"Successfully scraped {len(torrents)} torrents for {cache_key}",
            event="scrape_success",
            page=page,
            category=category,
            count=len(torrents),
            duration_ms=duration_ms,
        )
        return torrents
    except Exception as e:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Scrape failed for {cache_key}: {e}",
            event="scrape_failed",
            cache_key=cache_key,
            error=str(e),
            exc_info=True,
        )
        return []


def safe_background_scrape(page, category, cache_key, db_path=None):
    """Supervised wrapper for background threads capturing uncaught exceptions."""
    try:
        scrape_and_update(page, category, cache_key, db_path)
    except Exception as e:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Background thread scrape crash for {cache_key}: {e}",
            event="background_scrape_crash",
            cache_key=cache_key,
            error=str(e),
            exc_info=True,
        )


def get_torrents(page=0, categories=None, genres=None, db_path=None):
    database.clear_expired_new_flags(db_path)
    if categories is None:
        categories = []
    if genres is None:
        genres = []

    scrape_cat = categories[0] if len(categories) == 1 else "0"
    cache_key = f"p{page}_c{scrape_cat}"
    now = time.time()

    needs_scrape = True
    with database.get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT last_scraped FROM page_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if row:
            needs_scrape = now - row[0] > config.CACHE_EXPIRY

    if needs_scrape:
        if row:
            threading.Thread(
                target=safe_background_scrape,
                args=(page, scrape_cat, cache_key, db_path),
                daemon=True,
            ).start()
        else:
            scrape_and_update(page, scrape_cat, cache_key, db_path)

    # 1. Native Tracker View (Exact Order Match)
    if (not categories or len(categories) == 1) and not genres:
        with database.get_db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT torrent_ids FROM page_cache WHERE cache_key=?", (cache_key,)
            ).fetchone()
            if row and row[0]:
                torrent_ids = json.loads(row[0])
                if not torrent_ids:
                    return []

                placeholders = ",".join("?" * len(torrent_ids))
                conn.row_factory = sqlite3.Row
                unsorted = {
                    str(r["id"]): dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM torrents WHERE id IN ({placeholders})",
                        torrent_ids,
                    ).fetchall()
                }

                db_torrents = []
                for tid in torrent_ids:
                    if tid in unsorted:
                        t = unsorted[tid]
                        t["display_image"] = (
                            t["local_image"] if t["local_image"] else t["image_url"]
                        )
                        t["genres"] = json.loads(t["genres"]) if t["genres"] else []
                        db_torrents.append(t)
                return db_torrents
        return []

    # 2. Multi-Filter View (Dynamic SQL)
    query = "SELECT * FROM torrents WHERE 1=1"
    params = []

    if categories and "0" not in categories:
        query += f" AND category_id IN ({','.join(['?'] * len(categories))})"
        params.extend(categories)

    for g in genres:
        query += " AND genres LIKE ?"
        params.append(f'%"{g}"%')

    query += " ORDER BY added_date DESC, rowid DESC LIMIT 40 OFFSET ?"
    params.append(page * 40)

    with database.get_db_connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        db_torrents = [dict(r) for r in conn.execute(query, params).fetchall()]

    for t in db_torrents:
        t["display_image"] = t["local_image"] if t["local_image"] else t["image_url"]
        t["genres"] = json.loads(t["genres"]) if t["genres"] else []

    return db_torrents
