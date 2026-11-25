import os
import re
import sys
import time
import json
import sqlite3
import logging
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import skt_proxy.config as config
import skt_proxy.database as database
from skt_proxy.services.http_client import get_http_session, ensure_login, skt_get
from skt_proxy.services.torrent_parser import (
    parse_torrents,
    detect_content_type,
    parse_languages,
)
from skt_proxy.services.logger import setup_logger, log_siem_event

logger = setup_logger("skt-proxy.scraper")

background_worker = ThreadPoolExecutor(max_workers=4)
sync_worker = ThreadPoolExecutor(max_workers=1)
_sync_in_progress = set()
_sync_lock = threading.Lock()
_extras_in_progress = set()
_extras_lock = threading.Lock()

ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}


def get_covers_dir(covers_dir=None):
    if covers_dir is not None:
        return covers_dir
    app_module = sys.modules.get("skt_proxy.app") or sys.modules.get("app")
    return getattr(app_module, "COVERS_DIR", config.COVERS_DIR)


def find_cached_cover(tid: str, covers_dir: str) -> str | None:
    """Returns cached cover filename if {tid}.* exists in covers_dir."""
    if not os.path.isdir(covers_dir):
        return None
    for fname in os.listdir(covers_dir):
        if fname.startswith(f"{tid}."):
            return fname
    return None


def cache_cover_image(tid: str, image_url: str, covers_dir: str, db_path=None, session=None) -> str | None:
    """Downloads and caches a cover image to disk, updating local_image in SQLite. Returns local filename."""
    if not image_url:
        return None
    os.makedirs(covers_dir, exist_ok=True)
    ext = image_url.split(".")[-1].split("?")[0].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        ext = "jpg"
    local_filename = f"{tid}.{ext}"
    local_path = os.path.join(covers_dir, local_filename)

    if not os.path.exists(local_path):
        s = session if session is not None else get_http_session()
        resp = skt_get(image_url, session=s, timeout=12)
        if resp.status_code != 200 or not resp.content:
            return None
        with open(local_path, "wb") as f:
            f.write(resp.content)

    local_rel = f"/static/covers/{local_filename}"
    with database.get_db_connection(db_path) as conn:
        conn.execute("UPDATE torrents SET local_image = ? WHERE id = ?", (local_rel, tid))
    return local_filename


def fetch_torrent_file(tid: str, filename: str, session=None) -> tuple[bool, bytes | None, str | None, int]:
    """
    Fetches and validates a .torrent binary from SkTorrent.
    Returns (ok, file_bytes, safe_filename, status_code).
    """
    s = session if session is not None else get_http_session()
    url = f"https://sktorrent.eu/torrent/download.php?id={tid}&f={filename}&seed=0"
    resp = skt_get(url, session=s, timeout=15)
    content_type = resp.headers.get("Content-Type", "").lower()
    is_torrent = resp.status_code == 200 and (
        "bittorrent" in content_type
        or "octet-stream" in content_type
        or "torrent" in content_type
        or resp.content.startswith(b"d8:")
    )
    if not is_torrent:
        return False, None, None, resp.status_code

    safe_filename = "".join(
        c
        for c in urllib.parse.unquote(filename)
        if c.isalnum() or c in (" ", ".", "-", "_")
    )
    return True, resp.content, safe_filename, resp.status_code


def fetch_extras_task(torrent_id, image_url, needs_img, needs_csfd, db_path=None):
    try:
        session = get_http_session()
        covers_dir = get_covers_dir()

        if needs_img and image_url:
            try:
                cache_cover_image(torrent_id, image_url, covers_dir, db_path=db_path, session=session)
            except Exception as e:
                log_siem_event(
                    logger,
                    logging.WARNING,
                    f"Image fetch failed for {torrent_id}: {e}",
                    event="cover_fetch_failed",
                    torrent_id=torrent_id,
                    error=str(e),
                )

        csfd_id = None
        databazeknih_url = None
        if needs_csfd:
            try:
                resp = skt_get(
                    f"https://sktorrent.eu/torrent/details.php?id={torrent_id}",
                    session=session,
                    timeout=10,
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
    finally:
        with _extras_lock:
            _extras_in_progress.discard(str(torrent_id))


def _submit_extras_task(torrent_id, image_url, needs_img, needs_csfd, db_path=None):
    tid_key = str(torrent_id)
    with _extras_lock:
        if tid_key in _extras_in_progress:
            return
        _extras_in_progress.add(tid_key)
    try:
        background_worker.submit(
            fetch_extras_task,
            torrent_id,
            image_url,
            needs_img,
            needs_csfd,
            db_path,
        )
    except Exception:
        with _extras_lock:
            _extras_in_progress.discard(tid_key)
        raise


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

        fetched_pages = []
        seen_tids_in_sync = set()
        new_tids_order = []

        for page in range(max_pages):
            url = f"https://sktorrent.eu/torrent/torrents_v2.php?active=0&page={page}"
            if category_str and category_str != "0":
                url += f"&category={category_str}"

            try:
                resp = skt_get(url, session=session, timeout=12)
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
                    f"SELECT id FROM torrents WHERE id IN ({placeholders})",
                    tids,
                ).fetchall()
                existing_ids = {r[0] for r in rows}

            fetched_pages.append((page, torrents, tids))
            for t in torrents:
                tid = t["id"]
                if tid not in existing_ids and tid not in seen_tids_in_sync:
                    new_tids_order.append(tid)
                seen_tids_in_sync.add(tid)

            # Watermark detection:
            # If we encountered previously known records on this page, older pages are already cached.
            known_on_this_page = len(existing_ids)
            if known_on_this_page > 0:
                log_siem_event(
                    logger,
                    logging.INFO,
                    f"Watermark reached on page {page} for category {category_str} ({known_on_this_page} existing records found). Ending sync loop.",
                    event="sync_watermark_reached",
                    page=page,
                    category=category_str,
                    known_records=known_on_this_page,
                    new_records=len([tid for tid in tids if tid not in existing_ids]),
                )
                break

        if fetched_pages:
            total_batch_new = len(new_tids_order)
            with database.get_db_connection(db_path) as conn:
                max_row = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM torrents").fetchone()
                base_sort_order = max_row[0] if max_row and max_row[0] is not None else 0

                # Assign descending sort_order across the sync batch so newest-first tracker order is preserved
                new_sort_orders = {
                    tid: base_sort_order + (total_batch_new - idx)
                    for idx, tid in enumerate(new_tids_order)
                }

                all_tids = list(seen_tids_in_sync)
                placeholders = ",".join("?" * len(all_tids))
                existing_rows = conn.execute(
                    f"SELECT id, local_image, csfd_id, databazeknih_url FROM torrents WHERE id IN ({placeholders})",
                    all_tids,
                ).fetchall()
                existing_map = {r[0]: r for r in existing_rows}

                written_in_sync = set()
                for page, torrents, _tids in fetched_pages:
                    for t in torrents:
                        tid = t["id"]
                        if tid in written_in_sync:
                            continue
                        written_in_sync.add(tid)

                        if tid in existing_map:
                            row = existing_map[tid]
                            local_img = row[1]
                            csfd_id = row[2]
                            databazeknih_url = row[3]
                            sort_order = None
                            is_new = False
                        else:
                            is_new = bool(is_initialized)
                            if is_new:
                                total_new += 1
                            local_img = None
                            csfd_id = None
                            databazeknih_url = None
                            sort_order = new_sort_orders[tid]

                        if not local_img or (not csfd_id and not databazeknih_url):
                            _submit_extras_task(
                                tid,
                                t["image_url"],
                                not local_img,
                                not csfd_id and not databazeknih_url,
                                db_path,
                            )

                        conn.execute(
                            """
                            INSERT INTO torrents
                            (id, title, category, category_id, genres, size, added_date, seeders, leechers, download_link, is_new, image_url, local_image, csfd_id, csfd_score, created_at, databazeknih_url, sort_order)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                title = excluded.title,
                                category = excluded.category,
                                category_id = excluded.category_id,
                                genres = excluded.genres,
                                size = excluded.size,
                                added_date = excluded.added_date,
                                seeders = excluded.seeders,
                                leechers = excluded.leechers,
                                download_link = excluded.download_link,
                                image_url = excluded.image_url,
                                csfd_score = COALESCE(excluded.csfd_score, torrents.csfd_score)
                            """,
                            (
                                tid,
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
                                t.get("csfd_score"),
                                now,
                                databazeknih_url,
                                sort_order,
                            ),
                        )

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


def safe_background_scrape(page, category, cache_key="", db_path=None):
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


def start_background_sync_loop(
    db_path=None,
    interval_seconds=None,
    initial_delay_seconds: float = 5.0,
    stop_event: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """
    Starts a daemon thread that periodically syncs the main tracker feed ("0")
    and expires stale 'new' flags even when no clients are viewing the UI.
    """
    event = stop_event or threading.Event()
    interval = max(
        1,
        int(
            interval_seconds
            if interval_seconds is not None
            else getattr(config, "BACKGROUND_SYNC_INTERVAL", getattr(config, "CACHE_EXPIRY", 300))
        ),
    )

    def _loop():
        if initial_delay_seconds > 0 and event.wait(initial_delay_seconds):
            return
        while not event.is_set():
            try:
                database.clear_expired_new_flags(db_path)
                state = database.get_sync_state("0", db_path)
                last_synced = state.get("last_synced", 0)
                if (time.time() - last_synced) >= interval:
                    safe_background_scrape(0, "0", "", db_path)
            except Exception as e:
                log_siem_event(
                    logger,
                    logging.ERROR,
                    f"Periodic background sync loop error: {e}",
                    event="periodic_sync_loop_error",
                    error=str(e),
                    exc_info=True,
                )
            if event.wait(interval):
                break

    thread = threading.Thread(target=_loop, name="skt-background-sync", daemon=True)
    thread.start()
    log_siem_event(
        logger,
        logging.INFO,
        f"Started periodic background sync loop (interval={interval}s)",
        event="background_sync_started",
        interval_seconds=interval,
    )
    return thread, event


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query_torrents(
    page=0,
    categories=None,
    genres=None,
    new_only=False,
    search_query="",
    db_path=None,
    covers_dir=None,
) -> tuple[list[dict], bool]:
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
        sync_worker.submit(safe_background_scrape, page, scrape_cat, "", db_path)

    # Unified query directly from SQLite indexes
    query = "SELECT * FROM torrents WHERE 1=1"
    params = []

    if categories and "0" not in categories:
        query += f" AND category_id IN ({','.join(['?'] * len(categories))})"
        params.extend(categories)

    for g in genres:
        query += " AND EXISTS (SELECT 1 FROM json_each(torrents.genres) WHERE json_each.value = ?)"
        params.append(g)

    if new_only:
        query += " AND is_new = 1"

    q_clean = (search_query or "").strip().lower()
    if q_clean:
        query += " AND LOWER(title) LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(q_clean)}%")

    page_size = getattr(config, "PAGE_SIZE", 40)
    query += " ORDER BY added_date DESC, COALESCE(sort_order, 0) DESC, rowid DESC LIMIT ? OFFSET ?"
    params.extend([page_size + 1, page * page_size])

    with database.get_db_connection(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        has_more = len(rows) > page_size
        db_torrents = [dict(r) for r in rows[:page_size]]

    resolved_covers_dir = get_covers_dir(covers_dir)
    for t in db_torrents:
        has_local = False
        if t["local_image"]:
            filename = os.path.basename(t["local_image"])
            has_local = os.path.exists(os.path.join(resolved_covers_dir, filename))

        t["display_image"] = t["local_image"] if has_local else f"/api/cover/{t['id']}"
        t["genres"] = json.loads(t["genres"]) if t["genres"] else []
        t["content_type"] = detect_content_type(t.get("category", ""), t.get("title", ""))
        t["languages"] = parse_languages(t.get("title", ""))

    return db_torrents, has_more


def get_torrents(page=0, categories=None, genres=None, new_only=False, search_query="", db_path=None, covers_dir=None):
    items, _ = query_torrents(
        page=page,
        categories=categories,
        genres=genres,
        new_only=new_only,
        search_query=search_query,
        db_path=db_path,
        covers_dir=covers_dir,
    )
    return items
