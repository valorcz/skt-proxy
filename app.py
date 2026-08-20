import os
import io
import re
import time
import json
import sqlite3
import hashlib
import threading
import urllib.parse
import logging
import requests
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv

load_dotenv()

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

# --- CONFIGURATION ---
SKT_USERNAME = os.environ.get("SKT_USERNAME", "")
SKT_PASSWORD = os.environ.get("SKT_PASSWORD", "")
LOGIN_URL = "https://sktorrent.eu/torrent/login.php?returnto=index.php"

SYNOLOGY_URL = os.environ.get("SYNOLOGY_URL", "").rstrip("/")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER", "")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD", "")
SYNOLOGY_DSM_VERSION = int(os.environ.get("SYNOLOGY_DSM_VERSION", "7"))
SYNOLOGY_DESTINATION = os.environ.get("SYNOLOGY_DESTINATION", "")

NAS_ALLOWED_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("NAS_ALLOWED_EMAILS", "").split(",")
    if e.strip()
]

skt_session = requests.Session()
skt_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
login_lock = threading.Lock()

CACHE_EXPIRY = 300
DB_PATH = "cache.db"
COVERS_DIR = os.path.join("static", "covers")
os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs("downloads", exist_ok=True)

background_worker = ThreadPoolExecutor(max_workers=4)


# --- DATABASE HELPERS ---
def get_db_connection():
    return sqlite3.connect(DB_PATH, timeout=20.0)


def extract_csfd_score(*texts):
    for text in texts:
        if not text:
            continue
        m = re.search(r"(?:csfd|čsfd)[\s:=_\-]*(\d{1,3})\s*%", text, re.I)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 100:
                return f"{val}%"
        m2 = re.search(r"(\d{1,3})\s*%\s*(?:csfd|čsfd)", text, re.I)
        if m2:
            val = int(m2.group(1))
            if 0 <= val <= 100:
                return f"{val}%"
    return None


def init_db():
    with get_db_connection() as conn:
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


def clear_expired_new_flags():
    """Clears is_new flag for any torrent record created >48 hours ago (172800s)."""
    now = time.time()
    cutoff = now - 172800
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE torrents SET is_new = 0 WHERE is_new = 1 AND created_at IS NOT NULL AND created_at < ?",
            (cutoff,),
        )


def is_db_empty():
    with get_db_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0] == 0


init_db()
DB_WAS_EMPTY = is_db_empty()


# --- CACHE & SCRAPING LOGIC ---
def fetch_extras_task(torrent_id, image_url, needs_img, needs_csfd):
    local_img_path = None
    if needs_img and image_url:
        ext = image_url.split(".")[-1].split("?")[0]
        if len(ext) > 4 or not ext:
            ext = "jpg"
        local_filename = f"{torrent_id}.{ext}"
        local_path = os.path.join(COVERS_DIR, local_filename)

        if not os.path.exists(local_path):
            try:
                resp = skt_session.get(image_url, timeout=10)
                if resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    local_img_path = f"/static/covers/{local_filename}"
            except Exception as e:
                logger.error(f"Image fail {image_url}: {e}")
        else:
            local_img_path = f"/static/covers/{local_filename}"

    csfd_id = None
    if needs_csfd:
        try:
            resp = skt_session.get(
                f"https://sktorrent.eu/torrent/details.php?id={torrent_id}", timeout=10
            )
            if resp.status_code == 200:
                match = re.search(r"csfd\.cz/film/(\d+)", resp.text)
                if match:
                    csfd_id = match.group(1)
        except Exception as e:
            logger.error(f"CSFD fetch fail {torrent_id}: {e}")

    if local_img_path or csfd_id:
        with get_db_connection() as conn:
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


def parse_torrents(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    torrents = []

    for a_tag in soup.find_all(
        "a", href=lambda href: href and "details.php?name=" in href
    ):
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
        if image_url:
            image_url = urllib.parse.urljoin("https://sktorrent.eu/torrent/", image_url)

        cat_tag = parent_td.find("a", href=lambda h: h and "category=" in h)
        category = cat_tag.text.strip() if cat_tag else "Unknown"
        category_id = (
            urllib.parse.parse_qs(urllib.parse.urlparse(cat_tag["href"]).query).get(
                "category", ["0"]
            )[0]
            if cat_tag and "href" in cat_tag.attrs
            else "0"
        )

        genre_tags = parent_td.find_all("a", href=lambda h: h and "zaner=" in h)
        genres = [g.text.strip() for g in genre_tags]

        formatted_title = title.replace(" ", "_") + ".torrent"
        download_link = (
            f"/proxy_download?id={torrent_id}&f={urllib.parse.quote(formatted_title)}"
        )

        td_text = parent_td.get_text(separator=" ")
        size_match = re.search(r"Velkost\s+(.*?)\s+\|", td_text)
        size = size_match.group(1).strip() if size_match else "N/A"

        date_match = re.search(r"Pridany\s+([\d/]+)", td_text)
        added_date = "N/A"
        if date_match:
            parts = date_match.group(1).strip().split("/")
            if len(parts) == 3:
                added_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
            else:
                added_date = date_match.group(1).strip()

        seed_match = re.search(r"Odosielaju\s*:\s*(\d+)", td_text)
        seeders = int(seed_match.group(1)) if seed_match else 0

        leech_match = re.search(r"Stahuju\s*:\s*(\d+)", td_text)
        leechers = int(leech_match.group(1)) if leech_match else 0

        csfd_score = extract_csfd_score(title, td_text)

        torrents.append(
            {
                "id": torrent_id,
                "title": title,
                "csfd_score": csfd_score,
                "image_url": image_url,
                "category": category,
                "category_id": category_id,
                "genres": genres,
                "size": size,
                "added_date": added_date,
                "seeders": seeders,
                "leechers": leechers,
                "download_link": download_link,
            }
        )
    return torrents


def scrape_and_update(page, category, cache_key):
    global DB_WAS_EMPTY
    try:
        if not ensure_login():
            return []

        url = f"https://sktorrent.eu/torrent/torrents_v2.php?active=0&page={page}"
        if category and str(category) != "0":
            url += f"&category={category}"

        resp = skt_session.get(url, timeout=10)
        resp.raise_for_status()
        torrents = parse_torrents(resp.text)

        torrent_ids = []
        now = time.time()

        with get_db_connection() as conn:
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

        return torrents
    except Exception as e:
        logger.error(f"Scrape failed for {cache_key}: {e}")
        return []


def get_torrents(page=0, categories=None, genres=None):
    clear_expired_new_flags()
    if categories is None:
        categories = []
    if genres is None:
        genres = []

    scrape_cat = categories[0] if len(categories) == 1 else "0"
    cache_key = f"p{page}_c{scrape_cat}"
    now = time.time()

    needs_scrape = True
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT last_scraped FROM page_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        if row:
            needs_scrape = now - row[0] > CACHE_EXPIRY

    if needs_scrape:
        if row:
            threading.Thread(
                target=scrape_and_update, args=(page, scrape_cat, cache_key)
            ).start()
        else:
            scrape_and_update(page, scrape_cat, cache_key)

    # 1. Native Tracker View (Exact Order Match)
    if (not categories or len(categories) == 1) and not genres:
        with get_db_connection() as conn:
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

    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        db_torrents = [dict(r) for r in conn.execute(query, params).fetchall()]

    for t in db_torrents:
        t["display_image"] = t["local_image"] if t["local_image"] else t["image_url"]
        t["genres"] = json.loads(t["genres"]) if t["genres"] else []

    return db_torrents


# --- NAS INTEGRATION ---
def get_current_user_email():
    default_email = NAS_ALLOWED_EMAILS[0] if NAS_ALLOWED_EMAILS else ""
    return request.headers.get("X-Forwarded-Email", default_email).lower()


def can_use_nas():
    email = get_current_user_email()
    return bool(email) and email in NAS_ALLOWED_EMAILS


def ensure_login():
    if (
        "pass" in skt_session.cookies.get_dict()
        or "uid" in skt_session.cookies.get_dict()
    ):
        return True

    with login_lock:
        if (
            "pass" in skt_session.cookies.get_dict()
            or "uid" in skt_session.cookies.get_dict()
        ):
            return True

        if not SKT_USERNAME or not SKT_PASSWORD:
            logger.error("SKT_USERNAME or SKT_PASSWORD environment variables are not configured.")
            return False

        try:
            skt_session.post(
                LOGIN_URL, data={"uid": SKT_USERNAME, "pwd": SKT_PASSWORD}, timeout=10
            )
        except requests.RequestException as e:
            logger.error(f"Login failed due to network error: {e}")
            return False

        return (
            "pass" in skt_session.cookies.get_dict()
            or "uid" in skt_session.cookies.get_dict()
        )


def torrent_bytes_to_magnet(file_bytes):
    """
    Extracts infohash, title, and all tracker URLs from raw .torrent file bytes and builds a magnet URI.
    """
    try:
        info_start = file_bytes.find(b"4:info")
        if info_start == -1:
            return None
        dict_start = info_start + 6
        if file_bytes[dict_start:dict_start + 1] != b"d":
            return None

        depth = 0
        i = dict_start
        while i < len(file_bytes):
            char = file_bytes[i:i + 1]
            if char in (b"d", b"l"):
                depth += 1
                i += 1
            elif char == b"e":
                depth -= 1
                i += 1
                if depth == 0:
                    break
            elif char.isdigit():
                colon = file_bytes.find(b":", i)
                if colon == -1:
                    break
                length = int(file_bytes[i:colon])
                i = colon + 1 + length
            elif char == b"i":
                e_pos = file_bytes.find(b"e", i)
                if e_pos == -1:
                    break
                i = e_pos + 1
            else:
                i += 1

        info_bytes = file_bytes[dict_start:i]
        infohash = hashlib.sha1(info_bytes).hexdigest()

        name_match = re.search(rb"4:name(\d+):", info_bytes)
        name_str = ""
        if name_match:
            try:
                nl = int(name_match.group(1))
                ns = name_match.end()
                name_str = info_bytes[ns:ns + nl].decode("utf-8", errors="ignore")
            except Exception:
                pass

        trackers = []
        for m in re.finditer(rb'(https?://[^\s\"\'<>]+|udp://[^\s\"\'<>]+)', file_bytes):
            start = m.start(1)
            if start > 0 and file_bytes[start - 1:start] == b':':
                digits_end = start - 1
                digits_start = digits_end
                while digits_start > 0 and file_bytes[digits_start - 1:digits_start].isdigit():
                    digits_start -= 1
                if digits_start < digits_end:
                    try:
                        length = int(file_bytes[digits_start:digits_end])
                        tr_url = file_bytes[start:start + length].decode("utf-8", errors="ignore")
                        if tr_url.startswith(("http://", "https://", "udp://")) and tr_url not in trackers:
                            trackers.append(tr_url)
                    except Exception:
                        pass

        magnet = f"magnet:?xt=urn:btih:{infohash}"
        if name_str:
            magnet += f"&dn={urllib.parse.quote(name_str)}"
        for tr in trackers:
            magnet += f"&tr={urllib.parse.quote(tr, safe='')}"

        return magnet
    except Exception as e:
        logger.warning(f"Failed to extract magnet from torrent bytes: {e}")
        return None


def push_to_synology(file_bytes, filename):
    """
    Pushes a torrent to Synology DownloadStation by converting its bencoded contents
    to a Magnet URI (with infohash, title, and all tracker URLs) and submitting it
    via SYNO.DownloadStation.Task (V1 API).
    """
    if not SYNOLOGY_URL or not SYNOLOGY_USER or not SYNOLOGY_PASSWORD:
        return False, "Synology NAS configuration (SYNOLOGY_URL, SYNOLOGY_USER, or SYNOLOGY_PASSWORD) is missing."

    magnet_uri = torrent_bytes_to_magnet(file_bytes)
    if not magnet_uri:
        return False, "Failed to parse magnet link from torrent file."

    base_url = SYNOLOGY_URL
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    base_url = base_url.rstrip("/")

    session = requests.Session()

    # 1. Login to Synology DSM (session=DownloadStation)
    login_url = f"{base_url}/webapi/auth.cgi"
    login_params = {
        "api": "SYNO.API.Auth",
        "version": "3",
        "method": "login",
        "account": SYNOLOGY_USER,
        "passwd": SYNOLOGY_PASSWORD,
        "session": "DownloadStation",
        "format": "cookie",
    }
    try:
        login_resp = session.get(login_url, params=login_params, timeout=10, verify=False)
        login_data = login_resp.json()
        if not login_data.get("success"):
            error_code = login_data.get("error", {}).get("code", "Unknown")
            return False, f"Synology DSM login failed (Code: {error_code})"

        sid = login_data.get("data", {}).get("sid", "")
        synotoken = login_data.get("data", {}).get("synotoken", "")
    except Exception as exc:
        logger.error(f"Synology DSM login exception: {exc}")
        return False, f"Synology login error: {exc}"

    # 2. Post Magnet URI to SYNO.DownloadStation.Task (V1 API)
    task_url = f"{base_url}/webapi/DownloadStation/task.cgi"
    task_data = {
        "api": "SYNO.DownloadStation.Task",
        "method": "create",
        "version": "1",
        "uri": magnet_uri,
        "_sid": sid,
    }
    if SYNOLOGY_DESTINATION:
        task_data["destination"] = SYNOLOGY_DESTINATION

    headers = {}
    if synotoken:
        headers["X-SYNO-TOKEN"] = synotoken

    try:
        resp = session.post(task_url, data=task_data, headers=headers, timeout=15, verify=False)
        result = resp.json()
        if result and result.get("success"):
            logger.info("Successfully pushed magnet task to Synology DownloadStation via POST")
            return True, "Task added successfully"

        # Fallback to GET request
        resp_get = session.get(task_url, params=task_data, headers=headers, timeout=15, verify=False)
        result_get = resp_get.json()
        if result_get and result_get.get("success"):
            logger.info("Successfully pushed magnet task to Synology DownloadStation via GET")
            return True, "Task added successfully"

        code = result.get("error", {}).get("code", "Unknown")
        return False, f"Failed to add task to DownloadStation (Code: {code})"
    except Exception as exc:
        logger.error(f"Synology task creation exception: {exc}")
        return False, f"Synology task creation error: {exc}"


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
    if not ensure_login():
        return "Proxy unable to authenticate with SkTorrent.", 502
    return render_template("index.html", can_use_nas=can_use_nas())


@app.route("/api/genres")
def api_genres():
    with get_db_connection() as conn:
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
    return jsonify(sorted(list(all_genres)))


@app.route("/api/torrents")
def api_torrents():
    if not ensure_login():
        return jsonify({"error": "Unauthorized"}), 401

    page = request.args.get("page", 0, type=int)
    cat_str = request.args.get("categories", "")
    genre_str = request.args.get("genres", "")

    categories = [c for c in cat_str.split(",") if c]
    genres = [g for g in genre_str.split(",") if g]

    torrents = get_torrents(page=page, categories=categories, genres=genres)
    return jsonify({"torrents": torrents, "can_use_nas": can_use_nas()})


def parse_skt_details_html(html_text):
    soup = BeautifulSoup(html_text, "html.parser")

    details = {
        "title": "",
        "poster_url": "",
        "csfd_url": "",
        "csfd_id": "",
        "synopsis": "",
        "mediainfo_text": "",
        "trailer_url": "",
        "infohash": "",
        "size": "",
        "uploader": "",
        "added_date": "",
        "files": [],
    }

    meta_name = soup.find("meta", {"itemprop": "name"})
    if meta_name and meta_name.get("content"):
        details["title"] = meta_name["content"].strip()

    csfd_link = soup.find("a", href=re.compile(r"csfd\.cz/film/"))
    if csfd_link and csfd_link.get("href"):
        details["csfd_url"] = csfd_link["href"]
        m = re.search(r"csfd\.cz/film/(\d+)", csfd_link["href"])
        if m:
            details["csfd_id"] = m.group(1)

    poster_img = soup.find("img", src=re.compile(r"cdn\.sktorrent\.eu/obrazky/"))
    if poster_img and poster_img.get("src"):
        src = poster_img["src"]
        if src.startswith("//"):
            src = "https:" + src
        details["poster_url"] = src

    mediainfo_div = (
        soup.find("div", {"id": re.compile(r"filesMediainfo", re.I)}) or
        soup.find("div", {"name": re.compile(r"filesMediainfo", re.I)})
    )
    if mediainfo_div:
        details["mediainfo_text"] = mediainfo_div.get_text(separator="\n", strip=True)

    iframe = soup.find("iframe", src=re.compile(r"youtube\.com"))
    if iframe and iframe.get("src"):
        details["trailer_url"] = iframe["src"]

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            k = tds[0].get_text(strip=True).lower()
            v = tds[1].get_text(strip=True)
            if "názov" in k or "nazov" in k or "title" in k:
                if not details["title"]:
                    details["title"] = v
            elif "info_hash" in k or "hash" in k:
                details["infohash"] = v
            elif "veľkosť" in k or "velkost" in k or "size" in k:
                details["size"] = v
            elif "pridal" in k or "uploader" in k:
                details["uploader"] = v
            elif "súbory" in k or "subory" in k or "files" in k:
                files_text = tds[1].get_text(separator="\n", strip=True)
                details["files"] = [f.strip() for f in files_text.split("\n") if f.strip()]

    lista_td = soup.find("td", class_="lista")
    if lista_td:
        text_lines = []
        for text in lista_td.stripped_strings:
            if any(skip in text for skip in ["Rolovatelne Media Info", "Mediainfo", "Podakuj za torrent", "Text bude automaticky centrovany", "Pridaj vlastnu verziu"]):
                continue
            if text.startswith(("http://", "https://", "//cdn.")):
                continue
            if len(text) > 10 and not text.startswith(("Formát", "Format version", "Veľkosť súboru", "Trvanie", "Overall bit rate", "Celkový dátový tok", "Frekvencia snímok", "Dátum zakódovania", "Použitý software", "Zakódoval", "Video", "Audio", "ID", "Stream size", "Bit depth", "Pomer strán", "Channel layout", "Sampling rate", "Compression mode", "Service kind", "Default", "Forced", "Color range", "Color primaries", "Matrix coefficients", "Count of elements", "Muxing mode")):
                if text not in text_lines:
                    text_lines.append(text)
        details["synopsis"] = "\n\n".join(text_lines)

    details["csfd_score"] = extract_csfd_score(details["title"], details["synopsis"], html_text)
    return details


@app.route("/api/torrent_details")
def api_torrent_details():
    if not ensure_login():
        return jsonify({"error": "Unauthorized"}), 401

    tid = request.args.get("id")
    if not tid:
        return jsonify({"error": "Missing torrent ID"}), 400

    try:
        url = f"https://sktorrent.eu/torrent/details.php?id={tid}"
        resp = skt_session.get(url, timeout=12)
        if resp.status_code != 200:
            return jsonify({"error": f"Failed to fetch details from tracker (Status {resp.status_code})"}), 502

        details = parse_skt_details_html(resp.text)
        details["id"] = tid
        return jsonify(details)
    except Exception as e:
        logger.error(f"Details fetch exception for tid={tid}: {e}")
        return jsonify({"error": str(e)}), 502


@app.route("/api/mark_read", methods=["POST"])
def mark_read():
    if not ensure_login():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.json or {}
    tid = payload.get("id")
    tids = payload.get("ids", [])
    if tid:
        tids.append(tid)

    if not tids:
        return jsonify({"error": "Missing torrent ID"}), 400

    with get_db_connection() as conn:
        placeholders = ",".join("?" * len(tids))
        conn.execute(
            f"UPDATE torrents SET is_new = 0 WHERE id IN ({placeholders})", tids
        )

    return jsonify({"success": True})


@app.route("/proxy_download")
def proxy_download():
    if not ensure_login():
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
    except requests.RequestException as e:
        return f"Download failed: {e}", 502


@app.route("/api/send_to_nas", methods=["POST"])
def send_to_nas():
    if not can_use_nas():
        return jsonify({"error": "Forbidden."}), 403
    if not ensure_login():
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
    except requests.RequestException as e:
        logger.error(f"Tracker download exception for tid={tid}: {e}")
        return jsonify({"error": str(e)}), 502


@app.route("/api/categories")
def api_categories():
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category_id, category FROM torrents WHERE category_id IS NOT NULL AND category_id != '0' ORDER BY category"
        ).fetchall()
    return jsonify([{"id": r[0], "name": r[1]} for r in rows])


if __name__ == "__main__":
    app.run(debug=True, port=5000)
