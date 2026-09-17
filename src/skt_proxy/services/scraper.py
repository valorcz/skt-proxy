import os
import re
import sys
import time
import json
import sqlite3
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import skt_proxy.config as config
import skt_proxy.database as database
from skt_proxy.services.http_client import get_http_session, ensure_login
from skt_proxy.services.torrent_parser import (
    parse_torrents,
    detect_content_type,
    parse_languages,
)
from skt_proxy.services.logger import setup_logger, log_siem_event
from skt_proxy.services.models import TorrentDTO

logger = setup_logger("skt-proxy.scraper")

background_worker = ThreadPoolExecutor(max_workers=4)
_sync_in_progress = set()
_sync_lock = threading.Lock()
DB_WAS_EMPTY = False


def set_db_was_empty(val: bool):
    global DB_WAS_EMPTY
    DB_WAS_EMPTY = val


def get_covers_dir():
    app_module = sys.modules.get("skt_proxy.app") or sys.modules.get("app")
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
    databazeknih_url = None
    if needs_csfd:
        try:
            resp = session.get(
                f"https://sktorrent.eu/torrent/details.php?id={torrent_id}", timeout=10
            )
            if resp.status_code == 200:
                match = re.search(r"csfd\.cz/film/(\d+)", resp.text)
                if match:
                    csfd_id = match.group(1)
                try:
                    from bs4 import BeautifulSoup
                    from skt_proxy.services.torrent_parser import extract_databazeknih_url

                    soup = BeautifulSoup(resp.text, "html.parser")
                    databazeknih_url = extract_databazeknih_url(soup, resp.text)
                except Exception as parse_err:
                    logger.debug(f"Could not extract databazeknih_url for {torrent_id}: {parse_err}")
        except Exception as e:
            log_siem_event(
                logger,
                logging.WARNING,
                f"Details extras fetch failed for {torrent_id}: {e}",
                event="extras_fetch_failed",
                torrent_id=torrent_id,
                error=str(e),
            )

    updates = []
    params = []
    if local_img_path:
        updates.append("local_image=?")
        params.append(local_img_path)
    if csfd_id:
        updates.append("csfd_id=?")
        params.append(csfd_id)
    if databazeknih_url:
        updates.append("databazeknih_url=?")
        params.append(databazeknih_url)

    if updates:
        params.append(torrent_id)
        with database.get_db_connection(db_path) as conn:
            conn.execute(
                f"UPDATE torrents SET {', '.join(updates)} WHERE id=?",
                tuple(params),
            )


def sync_tracker_feed(category="0", db_path=None, max_pages=3) -> int:
    """
    Synchronizes torrent feed from tracker into SQLite using watermark detection.
    Stops crawling as soon as it encounters already known records on the tracker.
    Returns the count of newly discovered torrents.
    """
    category_str = str(category or "0")
    resolved_db_path = database.get_db_path(db_path)
    sync_key = f"{resolved_db_path}:{category_str}"
    with _sync_lock:
        if sync_key in _sync_in_progress:
            logger.info(f"Sync already active for category {category_str}, skipping duplicate trigger.")
            return 0
        _sync_in_progress.add(sync_key)

    start_time = time.time()
    session = get_http_session()
    total_new = 0

    try:
        if not ensure_login(session):
            log_siem_event(
                logger,
                logging.ERROR,
                "Tracker sync aborted due to authentication failure",
                event="sync_aborted_auth",
                category=category_str,
            )
            return 0

        is_initialized = database.is_system_initialized(db_path)
        now = time.time()

        for page in range(max_pages):
            url = f"https://sktorrent.eu/torrent/torrents_v2.php?active=0&page={page}"
            if category_str and category_str != "0":
                url += f"&category={category_str}"

            try:
                resp = session.get(url, timeout=12)
                resp.raise_for_status()
            except Exception as e:
                log_siem_event(
                    logger,
                    logging.ERROR,
                    f"Tracker fetch failed on page {page} for category {category_str}: {e}",
                    event="sync_page_fetch_failed",
                    page=page,
                    category=category_str,
                    error=str(e),
                )
                break

            torrents = parse_torrents(resp.text)
            if not torrents:
                break

            tids = [t["id"] for t in torrents]
            with database.get_db_connection(db_path) as conn:
                placeholders = ",".join("?" * len(tids))
                rows = conn.execute(
                    f"SELECT id, is_new, local_image, csfd_id, csfd_score, created_at, databazeknih_url FROM torrents WHERE id IN ({placeholders})",
                    tids,
                ).fetchall()
                existing = {r[0]: r for r in rows}

                page_new_count = 0
                for t in torrents:
                    tid = t["id"]
                    if tid in existing:
                        row = existing[tid]
                        is_new = row[1]
                        local_img = row[2]
                        csfd_id = row[3]
                        csfd_score = t.get("csfd_score") or row[4]
                        created_at = row[5] or now
                        databazeknih_url = row[6] if len(row) > 6 else None
                    else:
                        # Newly encountered torrent
                        if is_initialized:
                            is_new = True
                            page_new_count += 1
                        else:
                            is_new = False
                        local_img = None
                        csfd_id = None
                        csfd_score = t.get("csfd_score")
                        created_at = now
                        databazeknih_url = None

                    if not local_img or (not csfd_id and not databazeknih_url):
                        background_worker.submit(
                            fetch_extras_task,
                            t["id"],
                            t["image_url"],
                            not local_img,
                            not csfd_id and not databazeknih_url,
                            db_path,
                        )

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO torrents
                        (id, title, category, category_id, genres, size, added_date, seeders, leechers, download_link, is_new, image_url, local_image, csfd_id, csfd_score, created_at, databazeknih_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            databazeknih_url,
                        ),
                    )

                # Keep page_cache table updated for backwards compatibility
                cache_key = f"p{page}_c{category_str}"
                conn.execute(
                    "INSERT OR REPLACE INTO page_cache (cache_key, last_scraped, torrent_ids) VALUES (?, ?, ?)",
                    (cache_key, now, json.dumps(tids)),
                )

            total_new += page_new_count

            # Watermark detection:
            # If we encountered previously known records on this page, older pages are already cached.
            known_on_this_page = len(existing)
            if known_on_this_page > 0:
                log_siem_event(
                    logger,
                    logging.INFO,
                    f"Watermark reached on page {page} for category {category_str} ({known_on_this_page} existing records found). Ending sync loop.",
                    event="sync_watermark_reached",
                    page=page,
                    category=category_str,
                    known_records=known_on_this_page,
                    new_records=page_new_count,
                )
                break

        if not is_initialized:
            database.set_system_initialized(db_path)

        database.update_sync_state(category_str, now, total_new, db_path)

        duration_ms = int((time.time() - start_time) * 1000)
        log_siem_event(
            logger,
            logging.INFO,
            f"Feed sync complete for category {category_str}: {total_new} new items in {duration_ms}ms",
            event="sync_success",
            category=category_str,
            new_items=total_new,
            duration_ms=duration_ms,
        )
        return total_new

    finally:
        with _sync_lock:
            _sync_in_progress.discard(sync_key)


def scrape_and_update(page, category, cache_key, db_path=None):
    """Backwards-compatible single-page scrape runner."""
    sync_tracker_feed(category=category, db_path=db_path, max_pages=1)
    with database.get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT torrent_ids FROM page_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if row and row[0]:
            tids = json.loads(row[0])
            placeholders = ",".join("?" * len(tids))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"SELECT * FROM torrents WHERE id IN ({placeholders})", tids).fetchall()
            return [dict(r) for r in rows]
    return []


def safe_background_scrape(page, category, cache_key, db_path=None):
    """Supervised wrapper for background threads."""
    try:
        sync_tracker_feed(category=category, db_path=db_path)
    except Exception as e:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Background thread sync crash for {category}: {e}",
            event="background_sync_crash",
            category=category,
            error=str(e),
            exc_info=True,
        )


def get_torrents(page=0, categories=None, genres=None, new_only=False, db_path=None):
    database.clear_expired_new_flags(db_path)
    if categories is None:
        categories = []
    if genres is None:
        genres = []

    scrape_cat = categories[0] if (categories and len(categories) == 1) else "0"
    now = time.time()

    sync_state = database.get_sync_state(scrape_cat, db_path)
    last_synced = sync_state.get("last_synced", 0)
    needs_sync = (now - last_synced) > getattr(config, "CACHE_EXPIRY", 300)

    # Cold start: if DB is completely empty for this feed, run synchronous first sync
    if database.is_db_empty(db_path):
        sync_tracker_feed(scrape_cat, db_path)
    elif needs_sync and page == 0:
        # Trigger background watermark sync without stalling user response
        threading.Thread(
            target=sync_tracker_feed,
            args=(scrape_cat, db_path),
            daemon=True,
        ).start()

    # Unified query directly from SQLite indexes
    query = "SELECT * FROM torrents WHERE 1=1"
    params = []

    if categories and "0" not in categories:
        query += f" AND category_id IN ({','.join(['?'] * len(categories))})"
        params.extend(categories)

    for g in genres:
        query += " AND genres LIKE ?"
        params.append(f'%"{g}"%')

    if new_only:
        query += " AND is_new = 1"

    page_size = getattr(config, "PAGE_SIZE", 40)
    query += " ORDER BY added_date DESC, rowid DESC LIMIT ? OFFSET ?"
    params.extend([page_size, page * page_size])

    with database.get_db_connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        db_torrents = [dict(r) for r in rows]

    covers_dir = get_covers_dir()
    for t in db_torrents:
        has_local = False
        if t["local_image"]:
            filename = os.path.basename(t["local_image"])
            has_local = os.path.exists(os.path.join(covers_dir, filename))

        t["display_image"] = t["local_image"] if has_local else f"/api/cover/{t['id']}"
        t["genres"] = json.loads(t["genres"]) if t["genres"] else []
        t["content_type"] = detect_content_type(t.get("category", ""), t.get("title", ""))
        t["languages"] = parse_languages(t.get("title", ""))

    return db_torrents
