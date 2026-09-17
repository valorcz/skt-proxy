import os
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from skt_proxy import app
from skt_proxy.services.torrent_parser import parse_torrents

@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_cache.db")
    covers_dir = str(tmp_path / "covers")
    with patch.object(app, "DB_PATH", db_file), patch.object(app, "COVERS_DIR", covers_dir):
        app.init_db()
        app.app.config["TESTING"] = True
        with app.app.test_client() as client:
            yield client

def test_db_init_and_journal_mode(tmp_path):
    db_file = str(tmp_path / "test_init.db")
    with patch.object(app, "DB_PATH", db_file):
        app.init_db()
        with sqlite3.connect(db_file) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"

def test_parse_torrents_html():
    sample_html = """
    <html>
    <body>
        <td>
            <a href="details.php?name=Test+Movie&id=12345">
                <img src="images/cover12345.jpg" />
                Test Movie 2026
            </a>
            <a href="torrents_v2.php?category=1">Movies</a>
            <a href="torrents_v2.php?zaner=Action">Action</a>
            <a href="torrents_v2.php?zaner=Sci-Fi">Sci-Fi</a>
            Velkost 2.5 GB | Pridany 19/08/2026 Odosielaju : 15 Stahuju : 3
        </td>
    </body>
    </html>
    """
    torrents = parse_torrents(sample_html)
    assert len(torrents) == 1
    t = torrents[0]
    assert t["id"] == "12345"
    assert t["title"] == "Test Movie 2026"
    assert t["category"] == "Movies"
    assert t["category_id"] == "1"
    assert t["genres"] == ["Action", "Sci-Fi"]
    assert t["size"] == "2.5 GB"
    assert t["added_date"] == "2026-08-19"
    assert t["seeders"] == 15
    assert t["leechers"] == 3
    assert t["image_url"] == "https://sktorrent.eu/torrent/images/cover12345.jpg"

def test_api_categories_endpoint(client, tmp_path):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, category_id, category) VALUES (?, ?, ?, ?)",
            ("1", "Movie 1", "10", "Filmy")
        )
    resp = client.get("/api/categories")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0] == {"id": "10", "name": "Filmy"}

def test_api_genres_endpoint(client, tmp_path):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, genres) VALUES (?, ?, ?)",
            ("1", "Movie 1", json.dumps(["Action", "Comedy"]))
        )
    resp = client.get("/api/genres")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "Action" in data
    assert "Comedy" in data

def test_send_to_nas_unauthorized(client):
    with patch.object(app, "can_use_nas", return_value=False):
        resp = client.post("/api/send_to_nas", json={"id": "123", "f": "test.torrent"})
        assert resp.status_code == 403
        assert "Forbidden" in resp.get_json()["error"]

def test_favicon_endpoint(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 204

def test_api_torrent_details_endpoint(client):
    mock_details_html = """
    <html>
    <body>
        <table class="main">
            <tr><td class="heading">Názov</td><td class="rowhead">Test Movie 2026</td></tr>
            <tr><td class="heading">Popis</td><td class="rowhead"><p>Sample description with <a href="https://www.csfd.cz/film/987654">CSFD link</a></p></td></tr>
            <tr><td class="heading">Info_Hash</td><td class="rowhead">abc123def456</td></tr>
            <tr><td class="heading">Súbory</td><td class="rowhead">movie.mkv<nfo.txt</td></tr>
        </table>
    </body>
    </html>
    """
    with patch.object(app, "ensure_login", return_value=True), \
         patch.object(app.skt_session, "get") as mock_get:
        
        mock_get.return_value = MagicMock(status_code=200, text=mock_details_html)
        
        resp = client.get("/api/torrent_details?id=12345")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "12345"
        assert data["title"] == "Test Movie 2026"
        assert data["csfd_id"] == "987654"
        assert data["infohash"] == "abc123def456"

def test_api_mark_read_endpoint(client, tmp_path):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT INTO torrents (id, title, is_new) VALUES (?, ?, ?)", ("999", "New Movie", True))
    
    resp = client.post("/api/mark_read", json={"id": "999"})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT is_new FROM torrents WHERE id='999'").fetchone()
        assert row[0] == 0

def test_clear_expired_new_flags(tmp_path):
    db_file = str(tmp_path / "test_expire.db")
    import time
    old_time = time.time() - 200000  # >48 hours ago
    with patch.object(app, "DB_PATH", db_file):
        app.init_db()
        with sqlite3.connect(db_file) as conn:
            conn.execute(
                "INSERT INTO torrents (id, title, is_new, created_at) VALUES (?, ?, ?, ?)",
                ("old_id", "Old Movie", True, old_time)
            )
        app.clear_expired_new_flags()
        with sqlite3.connect(db_file) as conn:
            row = conn.execute("SELECT is_new FROM torrents WHERE id='old_id'").fetchone()
            assert row[0] == 0


def test_api_mark_all_read_endpoint(client):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT INTO torrents (id, title, is_new) VALUES (?, ?, ?)", ("1001", "Movie 1", True))
        conn.execute("INSERT INTO torrents (id, title, is_new) VALUES (?, ?, ?)", ("1002", "Movie 2", True))

    resp = client.post("/api/mark_all_read")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["count"] == 2

    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("SELECT is_new FROM torrents WHERE is_new = 1").fetchall()
        assert len(rows) == 0


def test_api_sync_status_endpoint(client):
    resp = client.get("/api/sync_status?category=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "last_synced" in data
    assert "new_items" in data
    assert "unread_count" in data


def test_api_torrents_with_new_only(client):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT INTO torrents (id, title, is_new, added_date) VALUES (?, ?, ?, ?)", ("2001", "Old Film", False, "2026-09-01"))
        conn.execute("INSERT INTO torrents (id, title, is_new, added_date) VALUES (?, ?, ?, ?)", ("2002", "Fresh Film", True, "2026-09-02"))

    resp_all = client.get("/api/torrents?new_only=false")
    assert resp_all.status_code == 200
    all_data = resp_all.get_json()
    assert len(all_data["torrents"]) == 2
    assert all_data["unread_count"] == 1

    resp_new = client.get("/api/torrents?new_only=true")
    assert resp_new.status_code == 200
    new_data = resp_new.get_json()
    assert len(new_data["torrents"]) == 1
    assert new_data["torrents"][0]["id"] == "2002"


def test_api_config_endpoint(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "app_version" in data
    assert "skt_username" in data
    assert "can_use_nas" in data
    assert "current_user" in data


def test_index_endpoint_serves_spa(client):
    with patch.object(app, "ensure_login", return_value=True):
        resp = client.get("/")
        assert resp.status_code == 200
        # Check that it serves either the built SPA HTML or the template fallback
        assert b"SkT Proxy" in resp.data


def test_static_dist_assets_served(client):
    # Verify that Flask serves the built JS/CSS bundle
    dist_dir = os.path.join(app.app.root_path, "static", "dist", "assets")
    if os.path.exists(dist_dir):
        assets = os.listdir(dist_dir)
        for asset in assets:
            resp = client.get(f"/static/dist/assets/{asset}")
            assert resp.status_code == 200


def test_api_cover_cached_hit(client, tmp_path):
    covers_dir = app.COVERS_DIR
    os.makedirs(covers_dir, exist_ok=True)
    cover_file = os.path.join(covers_dir, "5555.jpg")
    with open(cover_file, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0testimage")

    resp = client.get("/api/cover/5555")
    assert resp.status_code == 200
    assert resp.data == b"\xff\xd8\xff\xe0testimage"
    assert "immutable" in resp.headers.get("Cache-Control", "")


def test_api_cover_on_demand_fetch(client):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, image_url) VALUES (?, ?, ?)",
            ("7777", "Test Movie", "https://sktorrent.eu/torrent/images/cover7777.jpg")
        )

    with patch.object(app.skt_session, "get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, content=b"downloaded_cover_bytes")
        resp = client.get("/api/cover/7777")
        assert resp.status_code == 200
        assert resp.data == b"downloaded_cover_bytes"

        # Verify cached to disk
        cover_path = os.path.join(app.COVERS_DIR, "7777.jpg")
        assert os.path.exists(cover_path)

        # Verify DB updated
        with sqlite3.connect(db_file) as conn:
            local_img = conn.execute("SELECT local_image FROM torrents WHERE id='7777'").fetchone()[0]
            assert local_img == "/static/covers/7777.jpg"


def test_api_cover_not_found(client):
    with patch.object(app.skt_session, "get") as mock_get:
        mock_get.return_value = MagicMock(status_code=404, text="")
        resp = client.get("/api/cover/999999")
        assert resp.status_code == 404


def test_api_torrent_details_proxies_poster(client):
    mock_details_html = """
    <html>
    <body>
        <table class="main">
            <tr><td class="heading">Názov</td><td class="rowhead">Avatar 2009</td></tr>
            <tr><td class="heading">Popis</td><td class="rowhead">
                <img src="https://cdn.sktorrent.eu/obrazky/123456.jpg" />
                <p>Pandora story.</p>
                <a href="details.php?id=abcdef1234567890abcdef1234567890abcdef12">Avatar 1080p</a>
            </td></tr>
        </table>
    </body>
    </html>
    """
    with patch.object(app, "ensure_login", return_value=True), \
         patch.object(app.skt_session, "get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text=mock_details_html)
        resp = client.get("/api/torrent_details?id=123456")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["poster_url"] == "/api/cover/123456"
        assert len(data["related_torrents"]) == 1
        rel = data["related_torrents"][0]
        assert "download_link" in rel
        assert "/proxy_download?id=" in rel["download_link"]
        assert "sktorrent_url" in rel


def test_api_torrent_details_detects_speech_and_category(client):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, category) VALUES (?, ?, ?)",
            ("8888", "Karel Capek - RUR", "Hovorené slovo")
        )

    mock_speech_html = """
    <html>
    <body>
        <table class="main">
            <tr><td class="heading">Názov</td><td class="rowhead">Karel Capek - RUR (rozhlasova hra)</td></tr>
            <tr><td class="heading">Popis</td><td class="rowhead"><p>Vyborna rozhlasova hra.</p></td></tr>
        </table>
    </body>
    </html>
    """
    with patch.object(app, "ensure_login", return_value=True), \
         patch.object(app.skt_session, "get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text=mock_speech_html)
        resp = client.get("/api/torrent_details?id=8888")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["content_type"] == "book"
        assert data["category"] == "Hovorené slovo"


def test_api_torrent_details_persists_databazeknih_url(client):
    db_file = app.DB_PATH
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, category) VALUES (?, ?, ?)",
            ("9991", "Stoparuv pruvodce po Galaxii", "E-knihy")
        )

    mock_book_html = """
    <html>
    <body>
        <meta itemprop="name" content="Stopařův průvodce po Galaxii">
        <meta itemprop="description" content="Kultovní sci-fi komedie od Douglase Adamse.">
        <table class="main">
            <tr><td class="heading">Názov</td><td class="rowhead">Douglas Adams - Stopařův průvodce</td></tr>
            <tr><td class="heading">Popis</td><td class="rowhead">
                <a href="https://www.databazeknih.cz/knihy/stoparuv-pruvodce-po-galaxii-217">Databáze knih</a>
                <p>Nezapomeňte si ručník!</p>
            </td></tr>
        </table>
    </body>
    </html>
    """
    with patch.object(app, "ensure_login", return_value=True), \
         patch.object(app.skt_session, "get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text=mock_book_html)
        resp = client.get("/api/torrent_details?id=9991")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["databazeknih_url"] == "https://www.databazeknih.cz/knihy/stoparuv-pruvodce-po-galaxii-217"

        # Verify persisted into SQLite
        with sqlite3.connect(db_file) as conn:
            row = conn.execute("SELECT databazeknih_url FROM torrents WHERE id='9991'").fetchone()
            assert row[0] == "https://www.databazeknih.cz/knihy/stoparuv-pruvodce-po-galaxii-217"

        # Verify returned in /api/torrents
        torrents_resp = client.get("/api/torrents")
        assert torrents_resp.status_code == 200
        t_list = torrents_resp.get_json()["torrents"]
        matching = [t for t in t_list if t["id"] == "9991"]
        assert len(matching) == 1
        assert matching[0]["databazeknih_url"] == "https://www.databazeknih.cz/knihy/stoparuv-pruvodce-po-galaxii-217"
