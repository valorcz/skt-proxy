import os
import io
import re
import sys
import logging
from flask import Flask, request, send_file, send_from_directory, jsonify
from werkzeug.exceptions import HTTPException

import skt_proxy.config as config
import skt_proxy.database as database
from skt_proxy.services.http_client import get_http_session, ensure_login, skt_get, default_session as skt_session
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

if "gunicorn" in os.environ.get("SERVER_SOFTWARE", ""):
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        logger.handlers = gunicorn_logger.handlers
        logger.setLevel(gunicorn_logger.level)

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


def get_torrents(page=0, categories=None, genres=None, new_only=False, search_query=""):
    return scraper.get_torrents(
        page=page,
        categories=categories,
        genres=genres,
        new_only=new_only,
        search_query=search_query,
        db_path=DB_PATH,
        covers_dir=COVERS_DIR,
    )


# --- ACCESS CONTROL & AUTH ---
def get_current_user_email():
    email = request.headers.get("X-Forwarded-Email", "").strip().lower()
    if not email:
        email = request.headers.get("Remote-Email", "").strip().lower()
    if not email:
        email = request.headers.get("Cf-Access-Authenticated-User-Email", "").strip().lower()
    return email


def can_use_nas():
    allowed_emails = getattr(config, "NAS_ALLOWED_EMAILS", [])
    if allowed_emails:
        email = get_current_user_email()
        return bool(email) and email in allowed_emails
    return True


VALID_TORRENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_torrent_id(tid) -> bool:
    return bool(isinstance(tid, str) and VALID_TORRENT_ID_RE.fullmatch(tid))


def create_app(config_override=None) -> Flask:
    """Application factory creating and configuring the Flask instance."""
    flask_app = Flask(__name__)
    if config_override:
        flask_app.config.update(config_override)

    db_path = flask_app.config.get("DB_PATH") or DB_PATH
    if not flask_app.config.get("SKIP_INIT_DB"):
        database.init_db(db_path)

    should_start_bg = flask_app.config.get("ENABLE_BACKGROUND_SYNC")
    if should_start_bg is None:
        is_pytest = "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))
        is_reloader_parent = (
            os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
            and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
        )
        should_start_bg = (
            getattr(config, "ENABLE_BACKGROUND_SYNC", True)
            and not flask_app.config.get("TESTING")
            and not is_pytest
            and not is_reloader_parent
        )

    if should_start_bg:
        bg_interval = flask_app.config.get("BACKGROUND_SYNC_INTERVAL")
        bg_delay = flask_app.config.get("BACKGROUND_SYNC_INITIAL_DELAY", 5.0)
        bg_thread, bg_stop = scraper.start_background_sync_loop(
            db_path=db_path,
            interval_seconds=bg_interval,
            initial_delay_seconds=bg_delay,
        )
        flask_app.extensions["background_sync_thread"] = bg_thread
        flask_app.extensions["background_sync_stop"] = bg_stop

    def _db_path():
        return flask_app.config.get("DB_PATH") or DB_PATH

    def _covers_dir():
        return flask_app.config.get("COVERS_DIR") or COVERS_DIR

    @flask_app.errorhandler(Exception)
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
            return jsonify({"error": "Internal server error"}), 500
        return "Internal server error", 500

    @flask_app.route("/favicon.ico")
    def favicon():
        return "", 204

    @flask_app.route("/static/covers/<path:filename>")
    def serve_cover(filename):
        """Serve cached cover images with 7-day immutable Cache-Control headers."""
        response = send_from_directory(_covers_dir(), filename)
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return response

    @flask_app.route("/api/cover/<tid>")
    def api_cover(tid):
        """On-demand proxy and caching endpoint for torrent cover images."""
        if not is_valid_torrent_id(tid):
            return jsonify({"error": "Invalid torrent ID"}), 400

        covers_dir = _covers_dir()
        db_path_cur = _db_path()
        os.makedirs(covers_dir, exist_ok=True)

        cached_filename = scraper.find_cached_cover(tid, covers_dir)
        if cached_filename:
            response = send_from_directory(covers_dir, cached_filename)
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
            return response

        image_url = None
        with database.get_db_connection(db_path_cur) as conn:
            row = conn.execute("SELECT image_url FROM torrents WHERE id = ?", (tid,)).fetchone()
            if row and row[0]:
                image_url = row[0]

        if not image_url:
            try:
                resp = skt_get(f"https://sktorrent.eu/torrent/details.php?id={tid}", session=skt_session, timeout=10)
                if resp.status_code == 200:
                    match = re.search(
                        r'src=["\'](//cdn\.sktorrent\.eu/obrazky/[^"\']+|https?://[^"\']+\.(?:jpg|jpeg|png|webp))["\']',
                        resp.text,
                        re.I,
                    )
                    if match:
                        src = match.group(1)
                        image_url = "https:" + src if src.startswith("//") else src
            except Exception as e:
                logger.warning(f"Failed to scrape cover URL for {tid}: {e}")

        if not image_url:
            return "", 404

        try:
            local_filename = scraper.cache_cover_image(
                tid, image_url, covers_dir, db_path=db_path_cur, session=skt_session
            )
            if local_filename:
                response = send_from_directory(covers_dir, local_filename)
                response.headers["Cache-Control"] = "public, max-age=604800, immutable"
                return response
        except Exception as e:
            logger.warning(f"Failed to download and cache cover for {tid}: {e}")

        return "", 404

    @flask_app.route("/")
    def index():
        if not ensure_login(skt_session):
            log_siem_event(
                logger,
                logging.ERROR,
                "Proxy failed to authenticate with SkTorrent on index request",
                event="index_auth_failed",
            )
            return "Proxy unable to authenticate with SkTorrent.", 502

        dist_index = os.path.join(flask_app.root_path, "static", "dist", "index.html")
        if os.path.exists(dist_index):
            return send_file(dist_index)

        return "Frontend bundle not built. Run 'cd frontend && npm run build'.", 503

    @flask_app.route("/api/config")
    def api_config():
        return jsonify({
            "app_version": config.APP_VERSION,
            "current_user": get_current_user_email(),
            "skt_username": config.SKT_USERNAME,
            "can_use_nas": can_use_nas(),
        })

    @flask_app.route("/api/genres")
    def api_genres():
        return jsonify(database.get_genres(_db_path()))

    @flask_app.route("/api/categories")
    def api_categories():
        return jsonify(database.get_categories(_db_path()))

    @flask_app.route("/api/torrents")
    def api_torrents():
        if not ensure_login(skt_session):
            return jsonify({"error": "Unauthorized"}), 401

        db_path_cur = _db_path()
        page = max(0, request.args.get("page", 0, type=int))
        cat_str = request.args.get("categories", "")
        genre_str = request.args.get("genres", "")
        new_only = request.args.get("new_only", "false").lower() in ("true", "1", "yes")
        search_query = request.args.get("q", "").strip()

        categories = [c for c in cat_str.split(",") if c]
        genres = [g for g in genre_str.split(",") if g]

        torrents, has_more = scraper.query_torrents(
            page=page,
            categories=categories,
            genres=genres,
            new_only=new_only,
            search_query=search_query,
            db_path=db_path_cur,
            covers_dir=_covers_dir(),
        )
        unread_count = database.get_unread_count(db_path_cur)
        scrape_cat = categories[0] if (categories and len(categories) == 1) else "0"
        sync_info = database.get_sync_state(scrape_cat, db_path_cur)

        return jsonify({
            "torrents": torrents,
            "can_use_nas": can_use_nas(),
            "unread_count": unread_count,
            "last_synced": sync_info.get("last_synced", 0),
            "page": page,
            "has_more": has_more,
        })

    @flask_app.route("/api/mark_all_read", methods=["POST"])
    def mark_all_read():
        if not ensure_login(skt_session):
            return jsonify({"error": "Unauthorized"}), 401

        count = database.mark_all_read(_db_path())
        return jsonify({"success": True, "count": count, "unread_count": 0})

    @flask_app.route("/api/sync_status")
    def api_sync_status():
        if not ensure_login(skt_session):
            return jsonify({"error": "Unauthorized"}), 401

        db_path_cur = _db_path()
        category = request.args.get("category", "0")
        state = database.get_sync_state(category, db_path_cur)
        unread_count = database.get_unread_count(db_path_cur)
        return jsonify({
            "last_synced": state.get("last_synced", 0),
            "new_items": state.get("new_items", 0),
            "unread_count": unread_count,
        })

    @flask_app.route("/api/sync", methods=["POST"])
    def api_sync():
        if not ensure_login(skt_session):
            return jsonify({"error": "Unauthorized"}), 401

        db_path_cur = _db_path()
        payload = request.json or {}
        category = payload.get("category", "0")
        new_count = scraper.sync_tracker_feed(category=category, db_path=db_path_cur)
        state = database.get_sync_state(category, db_path_cur)
        return jsonify({
            "success": True,
            "new_items": new_count,
            "last_synced": state.get("last_synced", 0),
            "unread_count": database.get_unread_count(db_path_cur),
        })

    @flask_app.route("/api/torrent_details")
    def api_torrent_details():
        if not ensure_login(skt_session):
            return jsonify({"error": "Unauthorized"}), 401

        tid = request.args.get("id")
        if not is_valid_torrent_id(tid):
            return jsonify({"error": "Invalid or missing torrent ID"}), 400

        db_path_cur = _db_path()
        try:
            url = f"https://sktorrent.eu/torrent/details.php?id={tid}"
            resp = skt_get(url, session=skt_session, timeout=12)
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

            category = ""
            with database.get_db_connection(db_path_cur) as conn:
                row = conn.execute("SELECT category FROM torrents WHERE id = ?", (tid,)).fetchone()
                if row and row[0]:
                    category = row[0]

            details = parse_skt_details_html(resp.text, category=category, tid=tid)
            if details.get("poster_url"):
                details["poster_url"] = f"/api/cover/{tid}"
            else:
                with database.get_db_connection(db_path_cur) as conn:
                    row = conn.execute("SELECT local_image, image_url FROM torrents WHERE id = ?", (tid,)).fetchone()
                    details["poster_url"] = f"/api/cover/{tid}" if (row and (row[0] or row[1])) else ""

            with database.get_db_connection(db_path_cur) as conn:
                row = conn.execute("SELECT databazeknih_url FROM torrents WHERE id = ?", (tid,)).fetchone()
                if details.get("databazeknih_url"):
                    conn.execute("UPDATE torrents SET databazeknih_url = ? WHERE id = ?", (details["databazeknih_url"], tid))
                elif row and row[0]:
                    details["databazeknih_url"] = row[0]

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
            return jsonify({"error": "Failed to load torrent details"}), 502

    @flask_app.route("/api/mark_read", methods=["POST"])
    def mark_read():
        if not ensure_login(skt_session):
            return jsonify({"error": "Unauthorized"}), 401

        payload = request.json or {}
        tid = payload.get("id")
        tids = list(payload.get("ids", []))
        if tid:
            tids.append(tid)

        if not tids:
            return jsonify({"error": "Missing torrent ID"}), 400

        database.mark_read(tids, _db_path())
        return jsonify({"success": True})

    @flask_app.route("/proxy_download")
    def proxy_download():
        if not ensure_login(skt_session):
            return "Authentication failed.", 401
        tid, filename = request.args.get("id"), request.args.get("f")
        if not is_valid_torrent_id(tid) or not filename:
            return "Invalid or missing parameters", 400

        try:
            ok, content, safe_filename, status_code = scraper.fetch_torrent_file(
                tid, filename, session=skt_session
            )
            if ok and content is not None:
                log_siem_event(
                    logger,
                    logging.INFO,
                    f"Proxy download served for tid={tid}",
                    event="proxy_download_success",
                    tid=tid,
                    filename=safe_filename,
                )
                return send_file(
                    io.BytesIO(content),
                    as_attachment=True,
                    download_name=safe_filename,
                    mimetype="application/x-bittorrent",
                )
            return f"Failed to download from tracker. Status: {status_code}", 502
        except Exception as e:
            log_siem_event(
                logger,
                logging.ERROR,
                f"Download proxy error for tid={tid}: {e}",
                event="proxy_download_error",
                tid=tid,
                error=str(e),
            )
            return "Download failed", 502

    @flask_app.route("/api/send_to_nas", methods=["POST"])
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
        if not is_valid_torrent_id(tid) or not filename:
            return jsonify({"error": "Invalid or missing parameters"}), 400

        try:
            ok, content, safe_filename, status_code = scraper.fetch_torrent_file(
                tid, filename, session=skt_session
            )
            if ok and content is not None:
                success, msg = push_to_synology(content, safe_filename)
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
                return jsonify({"error": msg}), 502

            log_siem_event(
                logger,
                logging.ERROR,
                f"Tracker download failed for NAS push tid={tid}. Status: {status_code}",
                event="nas_push_download_failed",
                tid=tid,
                status_code=status_code,
            )
            return jsonify({"error": f"Failed to fetch .torrent file from tracker (Status: {status_code})"}), 502
        except Exception as e:
            log_siem_event(
                logger,
                logging.ERROR,
                f"NAS push exception for tid={tid}: {e}",
                event="nas_push_exception",
                tid=tid,
                error=str(e),
            )
            return jsonify({"error": "Failed to send torrent to NAS"}), 502

    return flask_app


app = create_app()


def main():
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
