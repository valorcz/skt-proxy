import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
import app

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
    torrents = app.parse_torrents(sample_html)
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


