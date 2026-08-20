import os
import io
import urllib.parse
import logging
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.exceptions import HTTPException

import config
import database
from services.http_client import get_http_session, ensure_login, default_session as skt_session
from services.torrent_parser import (
    extract_csfd_score,
    torrent_bytes_to_magnet,
    parse_torrents,
    parse_skt_details_html,
)
from services import synology
from services import scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)

if "gunicorn" in os.environ.get("SERVER_SOFTWARE", "") or __name__ != "__main__":
    gunicorn_logger = logging.getLogger("gunicorn.error")
    logger.handlers = gunicorn_logger.handlers
    logger.setLevel(gunicorn_logger.level)

app = Flask(__name__)

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


def get_torrents(page=0, categories=None, genres=None):
    return scraper.get_torrents(page=page, categories=categories, genres=genres, db_path=DB_PATH)


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

    logger.error(f"Unhandled exception on {request.path}: {e}", exc_info=True)
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    return "Internal server error", 500


# --- ROUTES ---
@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/")
def index():
    if not ensure_login(skt_session):
        return "Proxy unable to authenticate with SkTorrent.", 502
    return render_template("index.html", can_use_nas=can_use_nas())


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

    categories = [c for c in cat_str.split(",") if c]
    genres = [g for g in genre_str.split(",") if g]

    torrents = get_torrents(page=page, categories=categories, genres=genres)
    return jsonify({"torrents": torrents, "can_use_nas": can_use_nas()})


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
            return jsonify({"error": f"Failed to fetch details from tracker (Status {resp.status_code})"}), 502

        details = parse_skt_details_html(resp.text, tid=tid)
        return jsonify(details)
    except Exception as e:
        logger.error(f"Details fetch exception for tid={tid}: {e}")
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
            return send_file(
                io.BytesIO(resp.content),
                as_attachment=True,
                download_name=safe_filename,
                mimetype="application/x-bittorrent",
            )
        return f"Failed to download from tracker. Status: {resp.status_code}", 502
    except Exception as e:
        return f"Download failed: {e}", 502


@app.route("/api/send_to_nas", methods=["POST"])
def send_to_nas():
    if not can_use_nas():
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
            return (
                jsonify({"success": True, "message": msg})
                if success
                else (jsonify({"error": msg}), 502)
            )

        logger.error(
            f"Tracker download failed for tid={tid}. Status: {resp.status_code}, Content-Type: {content_type}, Content prefix: {resp.content[:100]}"
        )
        return jsonify({"error": f"Failed to fetch .torrent file from tracker (Status: {resp.status_code})"}), 502
    except Exception as e:
        logger.error(f"Tracker download exception for tid={tid}: {e}")
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(debug=True, port=5000)
