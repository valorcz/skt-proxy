import os
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


def test_extract_quality_tags_from_mediainfo():
    # Title without codec or resolution
    title = "Kralik.Jojo.2019.SK.CZ.DABING"
    mediainfo = """
    General
    Format : Matroska
    File size : 4.50 GiB
    Duration : 1 h 48 min
    Overall bit rate : 5 950 kb/s

    Video #1
    Format : HEVC
    Format/Info : High Efficiency Video Coding
    Width : 1 920 pixels
    Height : 1 080 pixels
    Writing library : x265
    HDR format : SMPTE ST 2086, HDR10 compatible

    Audio #1
    Format : E-AC-3
    Channel(s) : 6 channels
    """
    tags = extract_quality_tags(title, mediainfo)
    assert "1080p" in tags
    assert "HEVC/x265" in tags
    assert "HDR" in tags
    assert "5.1" in tags


def test_detect_content_type_speech_music_book_tv():
    from skt_proxy.services.torrent_parser import detect_content_type

    # Speech / Spoken word
    assert detect_content_type("Hovorené slovo", "Karel Capek - RUR") == "book"
    assert detect_content_type("Mluvené slovo", "Sherlock Holmes 01") == "book"
    assert detect_content_type("", "Agatha Christie (Audiokniha CZ)") == "book"

    # Books
    assert detect_content_type("Knihy", "Dune Frank Herbert PDF") == "book"
    assert detect_content_type("E-knihy", "Zaklinac EPUB") == "book"
    assert detect_content_type("Časopisy", "National Geographic 2026") == "book"

    # Music
    assert detect_content_type("Hudba", "Pink Floyd - The Wall FLAC") == "music"
    assert detect_content_type("", "Hans Zimmer - Interstellar Soundtrack 320kbps") == "music"
    pure_audio_mi = "General\nFormat : FLAC\nAudio #1\nFormat : FLAC"
    assert detect_content_type("", "Artist - Album", mediainfo=pure_audio_mi) == "music"

    # TV
    assert detect_content_type("Seriály", "Breaking Bad") == "tv"
    assert detect_content_type("", "Dark Matter S01E05 1080p") == "tv"

    # Movies
    assert detect_content_type("Filmy", "Inception 2010 1080p") == "movie"


def test_extract_tracklist_and_anthrax_fixture():
    fixture_path = "/home/valor/.gemini/antigravity-cli/brain/cf89c166-f781-40b5-9c71-60da142b2ecb/scratch/anthrax_details.html"
    if os.path.exists(fixture_path):
        with open(fixture_path, "r", encoding="utf-8") as f:
            html = f.read()
        res = parse_skt_details_html(html, category="Hudba", tid="d052cec135c49179905e2515570ab60bea837186")
        assert res["content_type"] == "music"
        assert len(res["tracklist"]) == 11
        assert res["tracklist"][0] == "01. Persistence of Memory"
        assert res["tracklist"][10] == "11. My Victory"
        assert res["music_meta"].get("year") == "2026"
        assert res["music_meta"].get("codec") == "MP3"
        assert res["music_meta"].get("bitrate") == "320 kbps"
        assert "320 kbps" in res["quality_tags"]
        assert "MP3" in res["quality_tags"]


def test_extract_databazeknih_url():
    from bs4 import BeautifulSoup
    from skt_proxy.services.torrent_parser import extract_databazeknih_url

    # HTML with anchor
    html_a = """
    <div>
        Popis knihy: skvělý román.
        <a href="https://www.databazeknih.cz/knihy/stoparuv-pruvodce-po-galaxii-217">Profil na Databázi knih</a>
    </div>
    """
    soup_a = BeautifulSoup(html_a, "html.parser")
    assert extract_databazeknih_url(soup_a, html_a) == "https://www.databazeknih.cz/knihy/stoparuv-pruvodce-po-galaxii-217"

    # Plain text URL
    html_plain = """
    <div>
        Více info zde: https://databazeknih.cz/dalsi-vydani/zaklinac-posledni-prani-23941
    </div>
    """
    soup_plain = BeautifulSoup(html_plain, "html.parser")
    assert extract_databazeknih_url(soup_plain, html_plain) == "https://databazeknih.cz/dalsi-vydani/zaklinac-posledni-prani-23941"


def test_sync_preserves_sktorrent_order_across_pages_and_updates(tmp_path):
    from skt_proxy.services import scraper

    db_file = str(tmp_path / "order_test.db")
    covers_dir = str(tmp_path / "covers")
    database.init_db(db_file)

    page0_html = """
    <html><body>
        <td>
            <a href="details.php?name=Newest+Item&id=tid_1">Newest Item</a>
            Velkost 1 GB | Pridany 25/11/2026 Odosielaju : 10 Stahuju : 1
        </td>
        <td>
            <a href="details.php?name=Second+Item&id=tid_2">Second Item</a>
            Velkost 2 GB | Pridany 25/11/2026 Odosielaju : 8 Stahuju : 2
        </td>
    </body></html>
    """
    page1_html = """
    <html><body>
        <td>
            <a href="details.php?name=Third+Item&id=tid_3">Third Item</a>
            Velkost 3 GB | Pridany 25/11/2026 Odosielaju : 5 Stahuju : 0
        </td>
        <td>
            <a href="details.php?name=Older+Day&id=tid_4">Older Day</a>
            Velkost 4 GB | Pridany 24/11/2026 Odosielaju : 2 Stahuju : 0
        </td>
    </body></html>
    """

    mock_session = MagicMock()
    mock_session.get.side_effect = [
        MagicMock(status_code=200, text=page0_html),
        MagicMock(status_code=200, text=page1_html),
    ]

    with patch("skt_proxy.services.scraper.get_http_session", return_value=mock_session), \
         patch("skt_proxy.services.scraper.ensure_login", return_value=True), \
         patch("skt_proxy.services.scraper.get_covers_dir", return_value=covers_dir), \
         patch.object(scraper.background_worker, "submit"):
        scraper.sync_tracker_feed(category="0", db_path=db_file, max_pages=2)

    with patch("skt_proxy.services.scraper.get_covers_dir", return_value=covers_dir):
        results = scraper.get_torrents(page=0, db_path=db_file)
    assert [r["id"] for r in results] == ["tid_1", "tid_2", "tid_3", "tid_4"]

    # Simulate pre-existing enriched metadata on tid_1 and an incremental sync with tid_0 at the top
    with database.get_db_connection(db_file) as conn:
        conn.execute(
            "UPDATE torrents SET local_image = ?, csfd_id = ? WHERE id = ?",
            ("/static/covers/tid_1.jpg", "12345", "tid_1"),
        )

    incremental_html = """
    <html><body>
        <td>
            <a href="details.php?name=Brand+New+Top&id=tid_0">Brand New Top</a>
            Velkost 1.5 GB | Pridany 25/11/2026 Odosielaju : 20 Stahuju : 5
        </td>
        <td>
            <a href="details.php?name=Newest+Item&id=tid_1">Newest Item</a>
            Velkost 1 GB | Pridany 25/11/2026 Odosielaju : 15 Stahuju : 1
        </td>
    </body></html>
    """
    mock_session2 = MagicMock()
    mock_session2.get.return_value = MagicMock(status_code=200, text=incremental_html)

    with patch("skt_proxy.services.scraper.get_http_session", return_value=mock_session2), \
         patch("skt_proxy.services.scraper.ensure_login", return_value=True), \
         patch("skt_proxy.services.scraper.get_covers_dir", return_value=covers_dir), \
         patch.object(scraper.background_worker, "submit"):
        new_count = scraper.sync_tracker_feed(category="0", db_path=db_file, max_pages=2)

    assert new_count == 1
    with patch("skt_proxy.services.scraper.get_covers_dir", return_value=covers_dir):
        updated_results = scraper.get_torrents(page=0, db_path=db_file)
    assert [r["id"] for r in updated_results] == ["tid_0", "tid_1", "tid_2", "tid_3", "tid_4"]

    # Verify tid_1 retained its enriched columns & got updated seeders via ON CONFLICT DO UPDATE
    tid_1 = next(r for r in updated_results if r["id"] == "tid_1")
    assert tid_1["seeders"] == 15
    assert tid_1["local_image"] == "/static/covers/tid_1.jpg"
    assert tid_1["csfd_id"] == "12345"
    assert tid_1["is_new"] == 0


def test_parse_skt_details_sanitizes_urls():
    html = """
    <html><body>
        <a href="javascript:alert(1)//csfd.cz/film/123">Bad CSFD</a>
        <a href="https://www.imdb.com/title/tt1234567/">IMDb</a>
        <iframe src="https://evil.example/embed?youtube.com"></iframe>
    </body></html>
    """
    details = parse_skt_details_html(html, tid="123")
    assert details["csfd_url"] == ""
    assert details["imdb_url"] == "https://www.imdb.com/title/tt1234567/"
    assert details["trailer_url"] == ""

    html_yt = """
    <html><body>
        <iframe src="http://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
    </body></html>
    """
    details_yt = parse_skt_details_html(html_yt, tid="124")
    assert details_yt["trailer_url"] == "https://www.youtube.com/embed/dQw4w9WgXcQ"


def test_http_client_shared_cookies_and_reauth():
    import requests
    from skt_proxy.services import http_client

    http_client._shared_cookies.clear()

    s1 = requests.Session()
    s2 = requests.Session()

    login_calls = 0

    def fake_post(url, data=None, timeout=10):
        nonlocal login_calls
        login_calls += 1
        s1.cookies.set("uid", "42")
        s1.cookies.set("pass", "secret")
        return MagicMock(status_code=200)

    with patch.object(http_client.config, "SKT_USERNAME", "user"), \
         patch.object(http_client.config, "SKT_PASSWORD", "pwd"), \
         patch.object(s1, "post", side_effect=fake_post):
        assert http_client.ensure_login(s1) is True
        # Second session reuses shared cookies without another login POST
        assert http_client.ensure_login(s2) is True
        assert login_calls == 1
        assert s2.cookies.get_dict().get("uid") == "42"

    # Simulate expired session response (login.php redirect) triggering one re-auth retry
    get_calls = 0

    def fake_s2_get(url, **kwargs):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            return MagicMock(status_code=200, url="https://sktorrent.eu/torrent/login.php", text="")
        return MagicMock(status_code=200, url=url, text="ok")

    def fake_s2_post(url, data=None, timeout=10):
        s2.cookies.set("uid", "42")
        s2.cookies.set("pass", "refreshed")
        return MagicMock(status_code=200)

    with patch.object(http_client.config, "SKT_USERNAME", "user"), \
         patch.object(http_client.config, "SKT_PASSWORD", "pwd"), \
         patch.object(s2, "get", side_effect=fake_s2_get), \
         patch.object(s2, "post", side_effect=fake_s2_post):
        resp = http_client.skt_get("https://sktorrent.eu/torrent/torrents_v2.php", session=s2)
        assert resp.text == "ok"
        assert get_calls == 2
        assert s2.cookies.get_dict().get("pass") == "refreshed"

    http_client._shared_cookies.clear()


def test_torrent_bytes_to_magnet_ignores_pieces_and_reads_announce_list():
    # Binary pieces contains a deceptive "https://fake.invalid" string that must NOT be parsed as a tracker
    pieces_bytes = b"https://fake.invalid"
    info = (
        f"4:infod4:name4:test6:lengthi10e12:piece lengthi16384e6:pieces{len(pieces_bytes)}:".encode("utf-8")
        + pieces_bytes
        + b"e"
    )
    tr1 = b"https://tr1.example/announce"
    tr2 = b"udp://tr2.example:6969"
    torrent_bytes = (
        b"d"
        + f"8:announce{len(tr1)}:".encode("utf-8") + tr1
        + b"13:announce-listll"
        + f"{len(tr1)}:".encode("utf-8") + tr1
        + b"el"
        + f"{len(tr2)}:".encode("utf-8") + tr2
        + b"ee"
        + info
        + b"e"
    )
    magnet = torrent_bytes_to_magnet(torrent_bytes)
    assert magnet is not None
    assert "tr1.example" in magnet
    assert "tr2.example" in magnet
    assert "fake.invalid" not in magnet


def test_background_sync_loop_runs_and_stops_cleanly(tmp_path):
    import threading
    import time
    from skt_proxy.services import scraper

    db_file = str(tmp_path / "bg_loop.db")
    database.init_db(db_file)

    called = threading.Event()
    calls = []

    def fake_sync(category="0", db_path=None, max_pages=3):
        calls.append((category, db_path))
        database.update_sync_state(category, time.time(), 0, db_path)
        called.set()
        return 0

    with patch("skt_proxy.services.scraper.sync_tracker_feed", side_effect=fake_sync):
        thread, stop_event = scraper.start_background_sync_loop(
            db_path=db_file,
            interval_seconds=60,
            initial_delay_seconds=0,
        )
        assert called.wait(timeout=2.0) is True
        stop_event.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    assert calls == [("0", db_file)]

    # If last_synced is already fresh, loop should skip calling sync_tracker_feed
    with patch("skt_proxy.services.scraper.sync_tracker_feed") as mock_sync:
        thread2, stop_event2 = scraper.start_background_sync_loop(
            db_path=db_file,
            interval_seconds=60,
            initial_delay_seconds=0,
        )
        # Wait briefly via join after immediate stop after a tiny yield
        time.sleep(0.05)
        stop_event2.set()
        thread2.join(timeout=2.0)
        mock_sync.assert_not_called()
