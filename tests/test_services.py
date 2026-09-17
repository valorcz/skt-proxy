import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from skt_proxy import config, database, app
from skt_proxy.services.torrent_parser import (
    extract_csfd_score,
    torrent_bytes_to_magnet,
    parse_torrents,
    parse_skt_details_html,
    extract_quality_tags,
    is_technical_encoding_line,
    extract_clean_synopsis,
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

    # Test unread count & mark_all_read
    with database.get_db_connection(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, is_new) VALUES (?, ?, ?)",
            ("102", "Unread 1", True),
        )
        conn.execute(
            "INSERT INTO torrents (id, title, is_new) VALUES (?, ?, ?)",
            ("103", "Unread 2", True),
        )
    assert database.get_unread_count(db_file) == 2
    marked = database.mark_all_read(db_file)
    assert marked == 2
    assert database.get_unread_count(db_file) == 0

    # Test metadata and sync_state
    assert database.is_system_initialized(db_file) is False
    database.set_system_initialized(db_file)
    assert database.is_system_initialized(db_file) is True

    database.update_sync_state("0", 12345.0, 5, db_file)
    state = database.get_sync_state("0", db_file)
    assert state["last_synced"] == 12345.0
    assert state["new_items"] == 5


def test_synology_push_missing_config():
    with patch.object(app, "SYNOLOGY_URL", ""):
        success, msg = push_to_synology(b"fake", "fake.torrent")
        assert success is False
        assert "missing" in msg.lower()


def test_watermark_sync_stops_on_known_records(tmp_path):
    from skt_proxy.services import scraper

    db_file = str(tmp_path / "watermark_test.db")
    database.init_db(db_file)
    database.set_system_initialized(db_file)

    # Insert an already known torrent
    with database.get_db_connection(db_file) as conn:
        conn.execute(
            "INSERT INTO torrents (id, title, is_new) VALUES (?, ?, ?)",
            ("known_99", "Existing Title", False),
        )

    # Mock tracker HTML returning one known torrent and one new torrent
    mock_html = """
    <html>
    <body>
        <td>
            <a href="details.php?name=Existing+Title&id=known_99">Existing Title</a>
            Velkost 1 GB | Pridany 01/09/2026 Odosielaju : 1 Stahuju : 1
        </td>
        <td>
            <a href="details.php?name=Brand+New&id=new_100">Brand New</a>
            Velkost 2 GB | Pridany 02/09/2026 Odosielaju : 5 Stahuju : 2
        </td>
    </body>
    </html>
    """
    mock_session = MagicMock()
    mock_session.get.return_value = MagicMock(status_code=200, text=mock_html)

    with patch("skt_proxy.services.scraper.get_http_session", return_value=mock_session), \
         patch("skt_proxy.services.scraper.ensure_login", return_value=True):
        new_count = scraper.sync_tracker_feed(category="0", db_path=db_file, max_pages=3)
        # Because page 0 had a known record ('known_99'), watermark triggered and broke pagination
        feed_calls = [c for c in mock_session.get.call_args_list if "torrents_v2.php" in c[0][0]]
        assert len(feed_calls) == 1


def test_extract_quality_tags():
    tags1 = extract_quality_tags("Vlny.2024.1080p.WEBRip.x264.HDR.CZ.mkv")
    assert "1080p" in tags1
    assert "WEB" in tags1
    assert "AVC/x264" in tags1
    assert "HDR" in tags1

    tags2 = extract_quality_tags("Dune.2.2024.2160p.4K.Remux.HEVC.DV")
    assert "4K UHD" in tags2
    assert "Remux" in tags2
    assert "HEVC/x265" in tags2
    assert "DV" in tags2


def test_is_technical_encoding_line():
    assert is_technical_encoding_line("Format : Matroska") is True
    assert is_technical_encoding_line("Codec ID : V_MPEG4/ISO/AVC") is True
    assert is_technical_encoding_line("Bit rate : 5 000 kb/s") is True
    assert is_technical_encoding_line("Rozlíšenie : 1920x1080") is True
    assert is_technical_encoding_line("Writing library : x264 core 157 r2935") is True
    assert is_technical_encoding_line("cabac=1 / ref=4 / deblock=1:0:0") is True
    assert is_technical_encoding_line("1920x1080 23.976fps x264 5000kbps AC3 5.1") is True

    # Narrative text should NOT be marked as technical
    assert is_technical_encoding_line("Detektiv Nick Burkhardt zjišťuje, že je potomkem lovců.") is False
    assert is_technical_encoding_line("Film zachycuje atmosféru v redakci Československého rozhlasu v 60. letech.") is False


def test_extract_clean_synopsis_filters_out_encoding_specs():
    from bs4 import BeautifulSoup

    html_mixed = """
    <html>
    <body>
        <table class="main">
            <tr>
                <td class="heading">Popis</td>
                <td class="rowhead">
                    <b>Obsah:</b><br>
                    Napínavý příběh o odvaze novinářů v srpnu 1968.<br>
                    Mladý technik Tomáš stojí před těžkou životní volbou.<br>
                    <br>
                    <b>Video:</b><br>
                    Rozlíšenie : 1920x1080<br>
                    Codec ID : V_MPEG4/ISO/AVC<br>
                    Writing library : x264 core 157<br>
                    <br>
                    <b>Audio:</b><br>
                    Format : AC-3<br>
                    Channels : 6 channels<br>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    soup_mixed = BeautifulSoup(html_mixed, "html.parser")
    synopsis = extract_clean_synopsis(soup_mixed)

    assert "Napínavý příběh o odvaze novinářů v srpnu 1968." in synopsis
    assert "Mladý technik Tomáš stojí před těžkou životní volbou." in synopsis
    # Technical lines MUST be completely filtered out
    assert "Rozlíšenie" not in synopsis
    assert "Codec ID" not in synopsis
    assert "Writing library" not in synopsis
    assert "x264" not in synopsis
    assert "AC-3" not in synopsis


def test_extract_clean_synopsis_returns_empty_when_only_encoding_specs():
    from bs4 import BeautifulSoup

    html_pure_specs = """
    <html>
    <body>
        <table class="main">
            <tr>
                <td class="heading">Popis</td>
                <td class="rowhead">
                    Format : Matroska<br>
                    File size : 2.15 GiB<br>
                    Video : 1920x1080 AVC High@L4.1 5000 kbps<br>
                    Audio : AC-3 6 channels 640 kbps czech<br>
                    Writing library : x264 core 157<br>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    soup_pure = BeautifulSoup(html_pure_specs, "html.parser")
    synopsis = extract_clean_synopsis(soup_pure)
    assert synopsis == ""


