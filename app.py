import os
import io
import re
import time
import threading
import urllib.parse
import logging
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, send_file, jsonify
from dotenv import load_dotenv

load_dotenv()

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.DEBUG,  # Change to logging.INFO for production
    format="%(asctime)s [%(levelname)s] %(funcName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Silence noisy external libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)

# --- CONFIGURATION ---
SKT_USERNAME = os.environ.get("SKT_USERNAME", "")
SKT_PASSWORD = os.environ.get("SKT_PASSWORD", "")
LOGIN_URL = "https://sktorrent.eu/torrent/login.php?returnto=index.php"

SYNOLOGY_URL = os.environ.get("SYNOLOGY_URL", "").rstrip("/")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER", "")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD", "")
NAS_ALLOWED_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("NAS_ALLOWED_EMAILS", "").split(",")
    if e.strip()
]

# --- STATE & CACHING ---
skt_session = requests.Session()
skt_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

CACHE_EXPIRY = 300
PAGE_CACHE = {}
SEEN_IDS_FILE = "seen_ids.txt"
FILE_LOCK = threading.Lock()


def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE, "r") as f:
            ids = set(f.read().splitlines())
            logger.debug(f"Loaded {len(ids)} seen IDs from cache.")
            return ids
    return set()


def save_seen_ids(seen_ids):
    with FILE_LOCK:
        with open(SEEN_IDS_FILE, "w") as f:
            f.write("\n".join(seen_ids))
            logger.debug("Saved updated seen IDs to file.")


SEEN_IDS = load_seen_ids()


# --- HELPER FUNCTIONS ---
def get_current_user_email():
    debug_email = NAS_ALLOWED_EMAILS[0] if NAS_ALLOWED_EMAILS else "debug@rodinny.cloud"
    email = request.headers.get("X-Forwarded-Email", debug_email).lower()
    logger.debug(f"Current user email resolved to: {email}")
    return email


def can_use_nas():
    allowed = get_current_user_email() in NAS_ALLOWED_EMAILS
    logger.debug(f"NAS access permitted: {allowed}")
    return allowed


def ensure_login():
    cookies = skt_session.cookies.get_dict()
    if "pass" in cookies or "uid" in cookies:
        return True

    logger.info("Session missing SkT cookies. Attempting login...")
    payload = {"uid": SKT_USERNAME, "pwd": SKT_PASSWORD}

    try:
        skt_session.post(LOGIN_URL, data=payload, timeout=10)
    except requests.RequestException as e:
        logger.error(f"Tracker login request failed: {e}")
        return False

    cookies = skt_session.cookies.get_dict()
    if "pass" in cookies or "uid" in cookies:
        logger.info("Successfully authenticated with tracker.")
        return True

    logger.error("Authentication failed. Invalid credentials or blocked IP.")
    return False


def parse_torrents(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    torrents = []

    tags = soup.find_all("a", href=lambda href: href and "details.php?name=" in href)
    logger.debug(f"Found {len(tags)} raw torrent links to parse.")

    for a_tag in tags:
        parent_td = a_tag.find_parent("td")
        if not parent_td:
            continue

        href = a_tag["href"]
        parsed_url = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed_url.query)
        torrent_id = qs.get("id", [""])[0]
        if not torrent_id:
            continue

        title = str(a_tag.contents[-1]).strip() if a_tag.contents else "Unknown Title"
        img_tag = a_tag.find("img")
        image_url = img_tag.get("data-src") or img_tag.get("src", "") if img_tag else ""

        cat_tag = parent_td.find("a", href=lambda h: h and "category=" in h)
        category = cat_tag.text.strip() if cat_tag else "Unknown"

        formatted_title = title.replace(" ", "_") + ".torrent"
        encoded_title = urllib.parse.quote(formatted_title)
        download_link = f"/proxy_download?id={torrent_id}&f={encoded_title}"

        td_text = parent_td.get_text(separator=" ")

        size_match = re.search(r"Velkost\s+(.*?)\s+\|", td_text)
        size = size_match.group(1).strip() if size_match else "N/A"

        date_match = re.search(r"Pridany\s+([\d/]+)", td_text)
        added_date = date_match.group(1).strip() if date_match else "N/A"

        seed_match = re.search(r"Odosielaju\s*:\s*(\d+)", td_text)
        seeders = int(seed_match.group(1)) if seed_match else 0

        leech_match = re.search(r"Stahuju\s*:\s*(\d+)", td_text)
        leechers = int(leech_match.group(1)) if leech_match else 0

        torrents.append(
            {
                "id": torrent_id,
                "title": title,
                "image_url": image_url,
                "category": category,
                "size": size,
                "added_date": added_date,
                "seeders": seeders,
                "leechers": leechers,
                "download_link": download_link,
            }
        )

    return torrents


def get_torrents(page=0, category=0):
    cache_key = f"p{page}_c{category}"
    now = time.time()

    if cache_key in PAGE_CACHE:
        cached_time, cached_data = PAGE_CACHE[cache_key]
        if now - cached_time < CACHE_EXPIRY:
            logger.debug(f"Serving page {page} (cat {category}) from cache.")
            return cached_data

    url = f"https://sktorrent.eu/torrent/torrents_v2.php?active=0&page={page}"
    if category and str(category) != "0":
        url += f"&category={category}"

    logger.info(f"Fetching fresh data from tracker: {url}")
    try:
        resp = skt_session.get(url, timeout=10)
        resp.raise_for_status()
        torrents = parse_torrents(resp.text)

        new_items_found = False
        for t in torrents:
            if t["id"] not in SEEN_IDS:
                t["is_new"] = True
                SEEN_IDS.add(t["id"])
                new_items_found = True
            else:
                t["is_new"] = False

        if new_items_found:
            save_seen_ids(SEEN_IDS)

        PAGE_CACHE[cache_key] = (now, torrents)
        return torrents
    except requests.RequestException as e:
        logger.error(f"Failed to fetch data from tracker: {e}")
        return []


def push_to_synology(file_bytes, filename):
    logger.info(f"Initiating Synology push for: {filename}")
    try:
        # DSM 7 requires version 6 or 7 for Auth
        auth_url = f"{SYNOLOGY_URL}/webapi/auth.cgi"
        auth_params = {
            "api": "SYNO.API.Auth",
            "version": "6",
            "method": "login",
            "account": SYNOLOGY_USER,
            "passwd": SYNOLOGY_PASSWORD,
            "session": "DownloadStation",
            "format": "sid",
        }

        logger.debug(f"Authenticating with NAS at {SYNOLOGY_URL}")
        auth_req = requests.get(auth_url, params=auth_params, timeout=10)
        auth_resp = auth_req.json()
        logger.debug(f"NAS Auth Response: {auth_resp}")

        if not auth_resp.get("success"):
            err = auth_resp.get("error", {}).get("code", "Unknown")
            logger.error(f"NAS Authentication failed. Code: {err}")
            return False, f"NAS Auth failed (Code: {err})"

        sid = auth_resp["data"]["sid"]

        # 2. Create Task
        task_url = f"{SYNOLOGY_URL}/webapi/DownloadStation/task.cgi"

        # We pass these as 'data' (form body) instead of 'params' (URL query)
        task_data = {
            "api": "SYNO.DownloadStation.Task",
            "version": "3",  # Try '1' if 3 throws an error
            "method": "create",
            "_sid": sid,
        }

        # 'application/x-bittorrent' is technically correct, but if it still
        # rejects it, try changing it to 'application/octet-stream'
        files = {"file": (filename, file_bytes, "application/x-bittorrent")}

        logger.debug(f"Pushing {len(file_bytes)} bytes to Download Station")

        # CHANGED: params=task_params -> data=task_data
        task_req = requests.post(task_url, data=task_data, files=files, timeout=15)
        task_resp = task_req.json()

        logger.debug(f"NAS Task Response: {task_resp}")

        if task_resp.get("success"):
            logger.info(f"Successfully pushed {filename} to NAS.")
            return True, "Task added successfully"

        err = task_resp.get("error", {}).get("code", "Unknown")
        logger.error(f"NAS failed to create task. Code: {err}")
        return False, f"Failed to add task (Code: {err})"

    except requests.exceptions.RequestException as e:
        logger.error(f"NAS Network connection error: {e}")
        return False, f"NAS Network Error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error during NAS push: {e}")
        return False, f"Unexpected Error: {str(e)}"


# --- ROUTES ---
@app.route("/")
def index():
    if not ensure_login():
        return "Proxy unable to reach tracker or authenticate. Check logs.", 502

    category = request.args.get("category", "0")
    torrents = get_torrents(page=0, category=category)
    return render_template(
        "index.html",
        torrents=torrents,
        current_category=category,
        can_use_nas=can_use_nas(),
    )


@app.route("/api/torrents")
def api_torrents():
    if not ensure_login():
        return jsonify({"error": "Unauthorized"}), 401

    page = request.args.get("page", 0, type=int)
    category = request.args.get("category", "0")
    torrents = get_torrents(page=page, category=category)
    return jsonify({"torrents": torrents, "can_use_nas": can_use_nas()})


@app.route("/proxy_download")
def proxy_download():
    if not ensure_login():
        return "Authentication failed.", 401

    torrent_id = request.args.get("id")
    filename = request.args.get("f")

    if not torrent_id or not filename:
        return "Missing parameters", 400

    remote_url = (
        f"https://sktorrent.eu/torrent/download.php?id={torrent_id}&f={filename}&seed=0"
    )

    try:
        logger.info(f"Proxying download request for: {filename}")
        resp = skt_session.get(remote_url, timeout=15)
        if (
            resp.status_code == 200
            and "bittorrent" in resp.headers.get("Content-Type", "").lower()
        ):
            os.makedirs("downloads", exist_ok=True)
            safe_filename = "".join(
                c
                for c in urllib.parse.unquote(filename)
                if c.isalnum() or c in (" ", ".", "-", "_")
            )
            local_path = os.path.join("downloads", safe_filename)

            with open(local_path, "wb") as f:
                f.write(resp.content)
            logger.debug(f"Saved local copy to {local_path}")

            return send_file(
                io.BytesIO(resp.content),
                as_attachment=True,
                download_name=safe_filename,
                mimetype="application/x-bittorrent",
            )
        else:
            logger.warning(
                f"Tracker rejected download. HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}"
            )
            return f"Failed to download from tracker. Status: {resp.status_code}", 502
    except requests.RequestException as e:
        logger.error(f"Proxy download connection failed: {e}")
        return f"Download connection failed: {e}", 502


@app.route("/api/send_to_nas", methods=["POST"])
def send_to_nas():
    if not can_use_nas():
        logger.warning(f"Unauthorized NAS push attempt by {get_current_user_email()}")
        return jsonify({"error": "Forbidden. User not allowed."}), 403

    data = request.json
    torrent_id = data.get("id")
    filename = data.get("f")

    if not torrent_id or not filename:
        return jsonify({"error": "Missing parameters"}), 400

    remote_url = (
        f"https://sktorrent.eu/torrent/download.php?id={torrent_id}&f={filename}&seed=0"
    )

    try:
        logger.info(f"Fetching torrent from tracker to push to NAS: {filename}")
        resp = skt_session.get(remote_url, timeout=15)
        if (
            resp.status_code == 200
            and "bittorrent" in resp.headers.get("Content-Type", "").lower()
        ):
            safe_filename = "".join(
                c
                for c in urllib.parse.unquote(filename)
                if c.isalnum() or c in (" ", ".", "-", "_")
            )
            success, msg = push_to_synology(resp.content, safe_filename)

            if success:
                return jsonify({"success": True})
            return jsonify({"error": msg}), 502
        else:
            logger.error(
                "Tracker returned invalid payload (likely HTML login redirect)."
            )
            return jsonify({"error": "Failed to fetch .torrent from tracker"}), 502
    except requests.RequestException as e:
        logger.error(f"Network error while fetching torrent for NAS: {e}")
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    ensure_login()
    app.run(debug=True, port=5000)
