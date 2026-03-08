import re
import urllib.parse
import os
import io
import requests
from flask import Flask, render_template, request, send_file
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- CONFIGURATION ---
SKT_USERNAME = os.environ.get("SKT_USERNAME", "YOUR_USERNAME")
SKT_PASSWORD = os.environ.get("SKT_PASSWORD", "YOUR_PASSWORD")
INDEX_URL = "https://sktorrent.eu/torrent/torrents_v2.php?active=0"
LOGIN_URL = "https://sktorrent.eu/torrent/login.php?returnto=index.php"

# Persistent session to keep cookies alive
skt_session = requests.Session()
skt_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})


def ensure_login():
    cookies = skt_session.cookies.get_dict()
    if "pass" in cookies or "uid" in cookies:
        return True

    print("Logging in to SkT...")
    payload = {"uid": SKT_USERNAME, "pwd": SKT_PASSWORD}
    skt_session.post(LOGIN_URL, data=payload)

    cookies = skt_session.cookies.get_dict()
    if "pass" in cookies or "uid" in cookies:
        print("Login successful.")
        return True

    print("Login failed. Check credentials.")
    return False


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

        cat_tag = parent_td.find("a", href=lambda h: h and "category=" in h)
        category = cat_tag.text.strip() if cat_tag else "Unknown"

        formatted_title = title.replace(" ", "_") + ".torrent"
        encoded_title = urllib.parse.quote(formatted_title)

        # Route through our Flask proxy instead of the external URL
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


@app.route("/")
def index():
    if not ensure_login():
        return "Authentication failed. Check server console.", 401

    try:
        response = skt_session.get(INDEX_URL, timeout=10)
        response.raise_for_status()
        html_data = response.text
    except requests.RequestException as e:
        return f"Failed to fetch data from tracker: {e}", 502

    torrents = parse_torrents(html_data)
    return render_template("index.html", torrents=torrents)


@app.route("/proxy_download")
def proxy_download():
    if not ensure_login():
        return "Authentication failed.", 401

    torrent_id = request.args.get("id")
    filename = request.args.get("f")

    if not torrent_id or not filename:
        return "Missing parameters", 400

    # Request the file from the tracker
    remote_url = (
        f"https://sktorrent.eu/torrent/download.php?id={torrent_id}&f={filename}&seed=0"
    )
    resp = skt_session.get(remote_url)

    if (
        resp.status_code == 200
        and "bittorrent" in resp.headers.get("Content-Type", "").lower()
    ):
        # Save to local server directory
        os.makedirs("downloads", exist_ok=True)
        safe_filename = "".join(
            c
            for c in urllib.parse.unquote(filename)
            if c.isalnum() or c in (" ", ".", "-", "_")
        )
        local_path = os.path.join("downloads", safe_filename)

        with open(local_path, "wb") as f:
            f.write(resp.content)

        # Serve the file directly to the user's browser through the Flask app
        return send_file(
            io.BytesIO(resp.content),
            as_attachment=True,
            download_name=safe_filename,
            mimetype="application/x-bittorrent",
        )
    else:
        return f"Failed to download from tracker. Status: {resp.status_code}", 502


if __name__ == "__main__":
    ensure_login()
    app.run(debug=True, port=5000)
