import os
import io
import re
import time
import json
import sqlite3
import threading
import urllib.parse
import logging
import tempfile
import requests
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, send_file, jsonify
from dotenv import load_dotenv
from synology_api import downloadstation

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)
logging.getLogger('urllib3').setLevel(logging.WARNING)

app = Flask(__name__)

# --- CONFIGURATION ---
SKT_USERNAME = os.environ.get('SKT_USERNAME', '')
SKT_PASSWORD = os.environ.get('SKT_PASSWORD', '')
LOGIN_URL = "https://sktorrent.eu/torrent/login.php?returnto=index.php"

SYNOLOGY_URL = os.environ.get('SYNOLOGY_URL', '').rstrip('/')
SYNOLOGY_USER = os.environ.get('SYNOLOGY_USER', '')
SYNOLOGY_PASSWORD = os.environ.get('SYNOLOGY_PASSWORD', '')
NAS_ALLOWED_EMAILS = [e.strip().lower() for e in os.environ.get('NAS_ALLOWED_EMAILS', '').split(',') if e.strip()]

skt_session = requests.Session()
skt_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

CACHE_EXPIRY = 300 
DB_PATH = 'cache.db'
COVERS_DIR = os.path.join('static', 'covers')
os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs('downloads', exist_ok=True)

image_downloader = ThreadPoolExecutor(max_workers=4)

# --- DATABASE SETUP ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS torrents (
                id TEXT PRIMARY KEY, title TEXT, category TEXT, category_id TEXT, size TEXT, 
                added_date TEXT, seeders INTEGER, leechers INTEGER, download_link TEXT, 
                is_new BOOLEAN, image_url TEXT, local_image TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS page_cache (
                cache_key TEXT PRIMARY KEY, last_scraped REAL, torrent_ids TEXT
            )
        ''')
        
def is_db_empty():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM torrents").fetchone()[0] == 0

init_db()
DB_WAS_EMPTY = is_db_empty()

# --- CACHE & SCRAPING LOGIC ---
def download_image_task(torrent_id, url):
    if not url: return
    
    ext = url.split('.')[-1].split('?')[0]
    if len(ext) > 4 or not ext: ext = 'jpg'
    
    local_filename = f"{torrent_id}.{ext}"
    local_path = os.path.join(COVERS_DIR, local_filename)

    if not os.path.exists(local_path):
        try:
            resp = skt_session.get(url, timeout=10)
            if resp.status_code == 200:
                with open(local_path, 'wb') as f:
                    f.write(resp.content)
        except Exception as e:
            logger.error(f"Image download failed for {url}: {e}")
            return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE torrents SET local_image = ? WHERE id = ?", (f"/static/covers/{local_filename}", torrent_id))

def parse_torrents(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    torrents = []
    
    for a_tag in soup.find_all('a', href=lambda href: href and 'details.php?name=' in href):
        parent_td = a_tag.find_parent('td')
        if not parent_td: continue

        href = a_tag['href']
        parsed_url = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed_url.query)
        torrent_id = qs.get('id', [''])[0]
        if not torrent_id: continue

        title = str(a_tag.contents[-1]).strip() if a_tag.contents else "Unknown Title"
        img_tag = a_tag.find('img')
        image_url = img_tag.get('data-src') or img_tag.get('src', '') if img_tag else ""

        cat_tag = parent_td.find('a', href=lambda h: h and 'category=' in h)
        category = cat_tag.text.strip() if cat_tag else "Unknown"
        category_id = urllib.parse.parse_qs(urllib.parse.urlparse(cat_tag['href']).query).get('category', ['0'])[0] if cat_tag and 'href' in cat_tag.attrs else "0"

        formatted_title = title.replace(' ', '_') + '.torrent'
        download_link = f"/proxy_download?id={torrent_id}&f={urllib.parse.quote(formatted_title)}"

        td_text = parent_td.get_text(separator=' ')
        size = re.search(r'Velkost\s+(.*?)\s+\|', td_text).group(1).strip() if re.search(r'Velkost\s+(.*?)\s+\|', td_text) else "N/A"
        added_date = re.search(r'Pridany\s+([\d/]+)', td_text).group(1).strip() if re.search(r'Pridany\s+([\d/]+)', td_text) else "N/A"
        seeders = int(re.search(r'Odosielaju\s*:\s*(\d+)', td_text).group(1)) if re.search(r'Odosielaju\s*:\s*(\d+)', td_text) else 0
        leechers = int(re.search(r'Stahuju\s*:\s*(\d+)', td_text).group(1)) if re.search(r'Stahuju\s*:\s*(\d+)', td_text) else 0

        torrents.append({
            'id': torrent_id, 'title': title, 'image_url': image_url, 'category': category, 'category_id': category_id,
            'size': size, 'added_date': added_date, 'seeders': seeders, 'leechers': leechers, 'download_link': download_link
        })
    return torrents

def scrape_and_update(page, category, cache_key):
    try:
        if not ensure_login(): return []

        url = f"https://sktorrent.eu/torrent/torrents_v2.php?active=0&page={page}"
        if category and str(category) != "0": url += f"&category={category}"

        resp = skt_session.get(url, timeout=10)
        resp.raise_for_status()
        torrents = parse_torrents(resp.text)
        
        torrent_ids = []
        now = time.time()

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for t in torrents:
                torrent_ids.append(t['id'])
                cursor.execute("SELECT is_new, local_image FROM torrents WHERE id=?", (t['id'],))
                row = cursor.fetchone()
                
                is_new = False
                local_img = ""
                
                if not row:
                    is_new = not DB_WAS_EMPTY
                    image_downloader.submit(download_image_task, t['id'], t['image_url'])
                else:
                    is_new = row[0]
                    local_img = row[1]
                    if not local_img and t['image_url']:
                        image_downloader.submit(download_image_task, t['id'], t['image_url'])

                cursor.execute('''
                    INSERT OR REPLACE INTO torrents
                    (id, title, category, category_id, size, added_date, seeders, leechers, download_link, is_new, image_url, local_image)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (t['id'], t['title'], t['category'], t['category_id'], t['size'], t['added_date'], t['seeders'], t['leechers'], t['download_link'], is_new, t['image_url'], local_img))
            
            cursor.execute('''INSERT OR REPLACE INTO page_cache (cache_key, last_scraped, torrent_ids) VALUES (?, ?, ?)''', (cache_key, now, json.dumps(torrent_ids)))
        return torrents
    except Exception as e:
        logger.error(f"Scrape failed for {cache_key}: {e}")
        return []

def get_torrents(page=0, category=0):
    cache_key = f"p{page}_c{category}"
    now = time.time()
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT last_scraped, torrent_ids FROM page_cache WHERE cache_key=?", (cache_key,)).fetchone()

    cached_torrents = []
    needs_scrape = True

    if row:
        last_scraped, ids_json = row
        needs_scrape = (now - last_scraped > CACHE_EXPIRY)
        
        torrent_ids = json.loads(ids_json)
        if torrent_ids:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                placeholders = ','.join('?' * len(torrent_ids))
                db_torrents = {str(r['id']): dict(r) for r in conn.execute(f"SELECT * FROM torrents WHERE id IN ({placeholders})", torrent_ids).fetchall()}
                
                for tid in torrent_ids:
                    if tid in db_torrents:
                        t = db_torrents[tid]
                        t['display_image'] = t['local_image'] if t['local_image'] else t['image_url']
                        cached_torrents.append(t)

    if needs_scrape:
        if not cached_torrents:
            cached_torrents = scrape_and_update(page, category, cache_key)
            for t in cached_torrents: t['display_image'] = t.get('local_image') or t.get('image_url')
        else:
            threading.Thread(target=scrape_and_update, args=(page, category, cache_key)).start()

    return cached_torrents

# --- NAS INTEGRATION ---
def get_current_user_email():
    return request.headers.get('X-Forwarded-Email', NAS_ALLOWED_EMAILS[0] if NAS_ALLOWED_EMAILS else 'debug@rodinny.cloud').lower()

def can_use_nas():
    return get_current_user_email() in NAS_ALLOWED_EMAILS

def ensure_login():
    if 'pass' in skt_session.cookies.get_dict() or 'uid' in skt_session.cookies.get_dict(): return True
    try:
        skt_session.post(LOGIN_URL, data={'uid': SKT_USERNAME, 'pwd': SKT_PASSWORD}, timeout=10)
    except requests.RequestException: return False
    return 'pass' in skt_session.cookies.get_dict() or 'uid' in skt_session.cookies.get_dict()

def push_to_synology(file_bytes, filename):
    temp_path = os.path.join(tempfile.gettempdir(), filename)
    try:
        with open(temp_path, 'wb') as f: f.write(file_bytes)
        parsed = urllib.parse.urlparse(SYNOLOGY_URL)
        ds = downloadstation.DownloadStation(
            parsed.hostname, str(parsed.port) if parsed.port else ('5001' if parsed.scheme == 'https' else '5000'), 
            SYNOLOGY_USER, SYNOLOGY_PASSWORD, secure=(parsed.scheme == 'https'), cert_verify=False, dsm_version=7, debug=False
        )
        result = ds.create_task(file=temp_path)
        if result and result.get('success'): return True, "Task added successfully"
        return False, f"Failed to add task (Code: {result.get('error', {}).get('code', 'Unknown') if isinstance(result, dict) else 'Unknown Format'})"
    except Exception as e: return False, str(e)
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

# --- ROUTES ---
@app.route('/')
def index():
    if not ensure_login(): return "Proxy unable to authenticate.", 502
    category = request.args.get('category', '0')
    torrents = get_torrents(page=0, category=category)
    return render_template('index.html', torrents=torrents, current_category=category, can_use_nas=can_use_nas())

@app.route('/api/torrents')
def api_torrents():
    if not ensure_login(): return jsonify({"error": "Unauthorized"}), 401
    page = request.args.get('page', 0, type=int)
    category = request.args.get('category', '0')
    torrents = get_torrents(page=page, category=category)
    return jsonify({'torrents': torrents, 'can_use_nas': can_use_nas()})

@app.route('/proxy_download')
def proxy_download():
    if not ensure_login(): return "Authentication failed.", 401
    tid, filename = request.args.get('id'), request.args.get('f')
    if not tid or not filename: return "Missing parameters", 400

    try:
        resp = skt_session.get(f"https://sktorrent.eu/torrent/download.php?id={tid}&f={filename}&seed=0", timeout=15)
        if resp.status_code == 200 and 'bittorrent' in resp.headers.get('Content-Type', '').lower():
            safe_filename = "".join(c for c in urllib.parse.unquote(filename) if c.isalnum() or c in (' ', '.', '-', '_'))
            with open(os.path.join('downloads', safe_filename), 'wb') as f: f.write(resp.content)
            return send_file(io.BytesIO(resp.content), as_attachment=True, download_name=safe_filename, mimetype='application/x-bittorrent')
        return f"Failed to download from tracker. Status: {resp.status_code}", 502
    except requests.RequestException as e: return f"Download failed: {e}", 502

@app.route('/api/send_to_nas', methods=['POST'])
def send_to_nas():
    if not can_use_nas(): return jsonify({"error": "Forbidden."}), 403
    tid, filename = request.json.get('id'), request.json.get('f')
    if not tid or not filename: return jsonify({"error": "Missing parameters"}), 400

    try:
        resp = skt_session.get(f"https://sktorrent.eu/torrent/download.php?id={tid}&f={filename}&seed=0", timeout=15)
        if resp.status_code == 200 and 'bittorrent' in resp.headers.get('Content-Type', '').lower():
            safe_filename = "".join(c for c in urllib.parse.unquote(filename) if c.isalnum() or c in (' ', '.', '-', '_'))
            success, msg = push_to_synology(resp.content, safe_filename)
            return jsonify({"success": True}) if success else (jsonify({"error": msg}), 502)
        return jsonify({"error": "Failed to fetch .torrent"}), 502
    except requests.RequestException as e: return jsonify({"error": str(e)}), 502

if __name__ == '__main__':
    app.run(debug=True, port=5000)