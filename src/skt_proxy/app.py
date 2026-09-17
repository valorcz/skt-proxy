import os
import io
import re
import urllib.parse
import logging
from flask import Flask, render_template, request, send_file, send_from_directory, jsonify
from werkzeug.exceptions import HTTPException

import skt_proxy.config as config
import skt_proxy.database as database
from skt_proxy.services.http_client import get_http_session, ensure_login, default_session as skt_session
from skt_proxy.services.torrent_parser import (
    extract_csfd_score,
    torrent_bytes_to_magnet,
    parse_torrents,
    parse_skt_details_html,
)
from skt_proxy.services import synology
from skt_proxy.services import scraper
from skt_proxy.services.logger import setup_logger, log_siem_event

logger = setup_logger("skt-proxy.app")

if "gunicorn" in os.environ.get("SERVER_SOFTWARE", "") or __name__ != "__main__":
    gunicorn_logger = logging.getLogger("gunicorn.error")
    logger.handlers = gunicorn_logger.handlers
    logger.setLevel(gunicorn_logger.level)

app = Flask(__name__, template_folder="templates")

# --- MODULE ATTRIBUTES FOR BACKWARDS COMPATIBILITY & PYTEST PATCHING ---
DB_PATH = config.DB_PATH
COVERS_DIR = config.COVERS_DIR
NAS_ALLOWED_EMAILS = config.NAS_ALLOWED_EMAILS
SYNOLOGY_URL = config.SYNOLOGY_URL
SYNOLOGY_USER = config.SYNOLOGY_USER
SYNOLOGY_PASSWORD = config.SYNOLOGY_PASSWORD
SYNOLOGY_DESTINATION = config.SYNOLOGY_DESTINATION
SYNOLOGY_VERIFY_SSL = config.SYNOLOGY_VERIFY_SSL


def push_to_synology(file_bytes, filename):
    return synology.push_to_synology(file_bytes, filename)


def init_db():
    database.init_db(DB_PATH)


def clear_expired_new_flags():
    database.clear_expired_new_flags(DB_PATH)


def is_db_empty():
    return database.is_db_empty(DB_PATH)


def get_db_connection():
    return database.get_db_connection(DB_PATH)


def get_torrents(page=0, categories=None, genres=None, new_only=False):
    return scraper.get_torrents(
        page=page, categories=categories, genres=genres, new_only=new_only, db_path=DB_PATH
    )


# Initialize DB schema on module load
init_db()
DB_WAS_EMPTY = is_db_empty()


# --- ACCESS CONTROL & AUTH ---
def get_current_user_email():
    email = request.headers.get("X-Forwarded-Email", "").strip().lower()
    if not email:
        email = request.headers.get("Remote-Email", "").strip().lower()
    return email


def can_use_nas():
    allowed_emails = getattr(config, "NAS_ALLOWED_EMAILS", [])
    if allowed_emails:
        email = get_current_user_email()
        return bool(email) and email in allowed_emails
    return True


# --- GLOBAL ERROR HANDLERS ---
@app.errorhandler(Exception)
def handle_global_exception(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return e

    log_siem_event(
        logger,
        logging.ERROR,
        f"Unhandled exception on {request.path}: {e}",
        event="http_unhandled_exception",
        path=request.path,
        error=str(e),
        exc_info=True,
    )
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    return "Internal server error", 500


# --- ROUTES ---
@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/static/covers/<path:filename>")
def serve_cover(filename):
    """Serve cached cover images with 7-day immutable Cache-Control headers."""
    response = send_from_directory(COVERS_DIR, filename)
    response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return response


@app.route("/api/cover/<tid>")
def api_cover(tid):
    """On-demand proxy and caching endpoint for torrent cover images."""
    covers_dir = COVERS_DIR
    os.makedirs(covers_dir, exist_ok=True)

    # 1. Check if {tid}.* already exists on disk
    for fname in os.listdir(covers_dir):
        if fname.startswith(f"{tid}."):
            response = send_from_directory(covers_dir, fname)
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return response

    # 2. Discover image URL from SQLite or tracker details page
    image_url = None
    with database.get_db_connection(DB_PATH) as conn:
        row = conn.execute("SELECT image_url FROM torrents WHERE id = ?", (tid,)).fetchone()
        if row and row[0]:
            image_url = row[0]

    if not image_url:
        try:
            resp = skt_session.get(f"https://sktorrent.eu/torrent/details.php?id={tid}", timeout=10)
            if resp.status_code == 200:
                match = re.search(
                    r'src=["\'](//cdn\.sktorrent\.eu/obrazky/[^"\']+|https?://[^"\']+\.(?:jpg|jpeg|png|webp))["\']',
                    resp.text,
                    re.I,
                )
                if match:
                    src = match.group(1)
                    if src.startswith("//"):
                        src = "https:" + src
                    image_url = src
        except Exception as e:
            logger.warning(f"Failed to scrape cover URL for {tid}: {e}")

    if not image_url:
        return "", 404

    # 3. Fetch image on server and cache to disk
    try:
        ext = image_url.split(".")[-1].split("?")[0].lower()
        if len(ext) > 4 or not ext or ext not in ["jpg", "jpeg", "png", "webp"]:
            ext = "jpg"
        local_filename = f"{tid}.{ext}"
        local_path = os.path.join(covers_dir, local_filename)

        resp = skt_session.get(image_url, timeout=12)
        if resp.status_code == 200 and resp.content:
            with open(local_path, "wb") as f:
                f.write(resp.content)
            local_rel = f"/static/covers/{local_filename}"
            with database.get_db_connection(DB_PATH) as conn:
                conn.execute("UPDATE torrents SET local_image = ? WHERE id = ?", (local_rel, tid))

            response = send_from_directory(covers_dir, local_filename)
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return response
    except Exception as e:
        logger.warning(f"Failed to download and cache cover for {tid}: {e}")

    return "", 404


@app.route("/")
def index():
    if not ensure_login(skt_session):
        log_siem_event(
            logger,
            logging.ERROR,
            "Proxy failed to authenticate with SkTorrent on index request",
            event="index_auth_failed",
        )
        return "Proxy unable to authenticate with SkTorrent.", 502

    dist_index = os.path.join(app.root_path, "static", "dist", "index.html")
    if os.path.exists(dist_index):
        return send_file(dist_index)

    return render_template(
        "index.html",
        can_use_nas=can_use_nas(),
        app_version=config.APP_VERSION,
        current_user=get_current_user_email(),
        skt_username=config.SKT_USERNAME,
    )


@app.route("/api/config")
def api_config():
    return jsonify({
        "app_version": config.APP_VERSION,
        "current_user": get_current_user_email(),
        "skt_username": config.SKT_USERNAME,
        "can_use_nas": can_use_nas(),
    })


@app.route("/api/genres")
def api_genres():
    return jsonify(database.get_genres(DB_PATH))


@app.route("/api/categories")
def api_categories():
    return jsonify(database.get_categories(DB_PATH))


@app.route("/api/torrents")
def api_torrents():
    if not ensure_login(skt_session):
        return jsonify({"error": "Unauthorized"}), 401

    page = request.args.get("page", 0, type=int)
    cat_str = request.args.get("categories", "")
    genre_str = request.args.get("genres", "")
    new_only = request.args.get("new_only", "false").lower() in ("true", "1", "yes")

    categories = [c for c in cat_str.split(",") if c]
    genres = [g for g in genre_str.split(",") if g]

    torrents = get_torrents(page=page, categories=categories, genres=genres, new_only=new_only)
    unread_count = database.get_unread_count(DB_PATH)
    scrape_cat = categories[0] if (categories and len(categories) == 1) else "0"
    sync_info = database.get_sync_state(scrape_cat, DB_PATH)

    return jsonify({
        "torrents": torrents,
        "can_use_nas": can_use_nas(),
        "unread_count": unread_count,
        "last_synced": sync_info.get("last_synced", 0),
        "page": page,
        "has_more": len(torrents) >= getattr(config, "PAGE_SIZE", 40),
    })


@app.route("/api/mark_all_read", methods=["POST"])
def mark_all_read():
    if not ensure_login(skt_session):
        return jsonify({"error": "Unauthorized"}), 401

    count = database.mark_all_read(DB_PATH)
    return jsonify({"success": True, "count": count, "unread_count": 0})


@app.route("/api/sync_status")
def api_sync_status():
    if not ensure_login(skt_session):
        return jsonify({"error": "Unauthorized"}), 401

    category = request.args.get("category", "0")
    state = database.get_sync_state(category, DB_PATH)
    unread_count = database.get_unread_count(DB_PATH)
    return jsonify({
        "last_synced": state.get("last_synced", 0),
        "new_items": state.get("new_items", 0),
        "unread_count": unread_count,
    })


@app.route("/api/sync", methods=["POST"])
def api_sync():
    if not ensure_login(skt_session):
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.json or {}
    category = payload.get("category", "0")
    new_count = scraper.sync_tracker_feed(category=category, db_path=DB_PATH)
    return jsonify({
        "success": True,
        "new_items": new_count,
        "unread_count": database.get_unread_count(DB_PATH),
    })


@app.route("/api/torrent_details")
def api_torrent_details():
    if not ensure_login(skt_session):
        return jsonify({"error": "Unauthorized"}), 401

    tid = request.args.get("id")
    if not tid:
        return jsonify({"error": "Missing torrent ID"}), 400

    try:
        url = f"https://sktorrent.eu/torrent/details.php?id={tid}"
        resp = skt_session.get(url, timeout=12)
        if resp.status_code != 200:
            log_siem_event(
                logger,
                logging.WARNING,
                f"Failed to fetch details for tid={tid} status={resp.status_code}",
                event="details_fetch_status_error",
                tid=tid,
                status_code=resp.status_code,
            )
            return jsonify({"error": f"Failed to fetch details from tracker (Status {resp.status_code})"}), 502

        details = parse_skt_details_html(resp.text, tid=tid)
        # Ensure poster image is served strictly through the local proxy cache
        if details.get("poster_url"):
            details["poster_url"] = f"/api/cover/{tid}"
        else:
            with database.get_db_connection(DB_PATH) as conn:
                row = conn.execute("SELECT local_image, image_url FROM torrents WHERE id = ?", (tid,)).fetchone()
                if row and (row[0] or row[1]):
                    details["poster_url"] = f"/api/cover/{tid}"
                else:
                    details["poster_url"] = ""

        return jsonify(details)
    except Exception as e:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Details fetch exception for tid={tid}: {e}",
            event="details_fetch_exception",
            tid=tid,
            error=str(e),
        )
        return jsonify({"error": str(e)}), 502


@app.route("/api/mark_read", methods=["POST"])
def mark_read():
    if not ensure_login(skt_session):
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.json or {}
    tid = payload.get("id")
    tids = payload.get("ids", [])
    if tid:
        tids.append(tid)

    if not tids:
        return jsonify({"error": "Missing torrent ID"}), 400

    database.mark_read(tids, DB_PATH)
    return jsonify({"success": True})


@app.route("/proxy_download")
def proxy_download():
    if not ensure_login(skt_session):
        return "Authentication failed.", 401
    tid, filename = request.args.get("id"), request.args.get("f")
    if not tid or not filename:
        return "Missing parameters", 400

    try:
        resp = skt_session.get(
            f"https://sktorrent.eu/torrent/download.php?id={tid}&f={filename}&seed=0",
            timeout=15,
        )
        content_type = resp.headers.get("Content-Type", "").lower()
        is_torrent = (
            resp.status_code == 200
            and (
                "bittorrent" in content_type
                or "octet-stream" in content_type
                or "torrent" in content_type
                or resp.content.startswith(b"d8:")
            )
        )
        if is_torrent:
            safe_filename = "".join(
                c
                for c in urllib.parse.unquote(filename)
                if c.isalnum() or c in (" ", ".", "-", "_")
            )
            log_siem_event(
                logger,
                logging.INFO,
                f"Proxy download served for tid={tid}",
                event="proxy_download_success",
                tid=tid,
                filename=safe_filename,
            )
            return send_file(
                io.BytesIO(resp.content),
                as_attachment=True,
                download_name=safe_filename,
                mimetype="application/x-bittorrent",
            )
        return f"Failed to download from tracker. Status: {resp.status_code}", 502
    except Exception as e:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Download proxy error for tid={tid}: {e}",
            event="proxy_download_error",
            tid=tid,
            error=str(e),
        )
        return f"Download failed: {e}", 502


@app.route("/api/send_to_nas", methods=["POST"])
def send_to_nas():
    user_email = get_current_user_email()
    if not can_use_nas():
        log_siem_event(
            logger,
            logging.WARNING,
            f"Unauthorized NAS push attempt by user={user_email}",
            event="nas_push_forbidden",
            user_email=user_email,
        )
        return jsonify({"error": "Forbidden."}), 403

    if not ensure_login(skt_session):
        return jsonify({"error": "Failed to authenticate with SkTorrent tracker."}), 502

    payload = request.json or {}
    tid, filename = payload.get("id"), payload.get("f")
    if not tid or not filename:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        url = f"https://sktorrent.eu/torrent/download.php?id={tid}&f={filename}&seed=0"
        resp = skt_session.get(url, timeout=15)
        content_type = resp.headers.get("Content-Type", "").lower()
        is_torrent = (
            resp.status_code == 200
            and (
                "bittorrent" in content_type
                or "octet-stream" in content_type
                or "torrent" in content_type
                or resp.content.startswith(b"d8:")
            )
        )

        if is_torrent:
            safe_filename = "".join(
                c
                for c in urllib.parse.unquote(filename)
                if c.isalnum() or c in (" ", ".", "-", "_")
            )

            success, msg = push_to_synology(resp.content, safe_filename)
            if success:
                log_siem_event(
                    logger,
                    logging.INFO,
                    f"NAS push executed by user={user_email} for tid={tid}",
                    event="nas_push_executed",
                    user_email=user_email,
                    tid=tid,
                    filename=safe_filename,
                )
                return jsonify({"success": True, "message": msg})
            else:
                return jsonify({"error": msg}), 502

        log_siem_event(
            logger,
            logging.ERROR,
            f"Tracker download failed for NAS push tid={tid}. Status: {resp.status_code}",
            event="nas_push_download_failed",
            tid=tid,
            status_code=resp.status_code,
        )
        return jsonify({"error": f"Failed to fetch .torrent file from tracker (Status: {resp.status_code})"}), 502
    except Exception as e:
        log_siem_event(
            logger,
            logging.ERROR,
            f"NAS push exception for tid={tid}: {e}",
            event="nas_push_exception",
            tid=tid,
            error=str(e),
        )
        return jsonify({"error": str(e)}), 502


def main():
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
