import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from skt_proxy import config, database, app
from skt_proxy.services.torrent_parser import (
    extract_csfd_score,
    torrent_bytes_to_magnet,
    parse_torrents,
    parse_skt_details_html,
)
from skt_proxy.services.synology import push_to_synology


def test_extract_csfd_score():
    assert extract_csfd_score("Movie 85% CSFD") == "85%"
    assert extract_csfd_score("CSFD 50% title") == "50%"
    assert extract_csfd_score("No rating") is None


def test_torrent_bytes_to_magnet():
    url = "https://sktorrent.eu/announce.php"
    sample = f"d8:announce{len(url)}:{url}4:infod4:name12:sample_movie6:lengthi100e12:piece lengthi16384e6:pieces20:12345678901234567890ee".encode("utf-8")
    magnet = torrent_bytes_to_magnet(sample)
    assert magnet is not None
    assert magnet.startswith("magnet:?xt=urn:btih:")
    assert "dn=sample_movie" in magnet
    assert "tr=https%3A%2F%2Fsktorrent.eu%2Fannounce.php" in magnet


def test_database_operations(tmp_path):
    db_file = str(tmp_path / "service_test.db")
    database.init_db(db_file)

    assert database.is_db_empty(db_file) is True

    with database.get_db_connection(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, category_id, category, genres, is_new) VALUES (?, ?, ?, ?, ?, ?)",
            ("101", "Action Movie", "5", "Filmy", '["Action"]', True),
        )

    assert database.is_db_empty(db_file) is False
    cats = database.get_categories(db_file)
    assert len(cats) == 1
    assert cats[0] == {"id": "5", "name": "Filmy"}

    genres = database.get_genres(db_file)
    assert genres == ["Action"]

    database.mark_read(["101"], db_file)
    with database.get_db_connection(db_file) as conn:
        row = conn.execute("SELECT is_new FROM torrents WHERE id='101'").fetchone()
        assert row[0] == 0


def test_synology_push_missing_config():
    with patch.object(app, "SYNOLOGY_URL", ""):
        success, msg = push_to_synology(b"fake", "fake.torrent")
        assert success is False
        assert "missing" in msg.lower()
