import pytest
from unittest.mock import MagicMock, patch
import app

def test_push_to_synology_missing_config():
    with patch.object(app, "SYNOLOGY_URL", ""), patch.object(app, "SYNOLOGY_USER", ""), patch.object(app, "SYNOLOGY_PASSWORD", ""):
        success, msg = app.push_to_synology(b"fake torrent data", "test.torrent")
        assert success is False
        assert "missing" in msg.lower() or "not configured" in msg.lower()

def test_push_to_synology_success():
    url = "https://sktorrent.eu/announce.php"
    sample = f"d8:announce{len(url)}:{url}4:infod4:name12:sample_movie6:lengthi100e12:piece lengthi16384e6:pieces20:12345678901234567890ee".encode("utf-8")

    with patch.object(app, "SYNOLOGY_URL", "http://192.168.1.100:5000"), \
         patch.object(app, "SYNOLOGY_USER", "user"), \
         patch.object(app, "SYNOLOGY_PASSWORD", "pass"), \
         patch("requests.Session") as mock_session_cls:

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # Login response
        mock_session.get.side_effect = [
            MagicMock(json=lambda: {"success": True, "data": {"sid": "test_sid"}}),
            MagicMock(json=lambda: {"success": True}),
        ]
        mock_session.post.return_value.json.return_value = {"success": True}

        success, msg = app.push_to_synology(sample, "sample.torrent")
        assert success is True
        assert msg == "Task added successfully"

        # Verify POST data contained magnet URI and sid
        mock_session.post.assert_called_once()
        _, kwargs = mock_session.post.call_args
        data = kwargs.get("data", {})
        assert data.get("api") == "SYNO.DownloadStation.Task"
        assert data.get("method") == "create"
        assert data.get("version") == "1"
        assert data.get("_sid") == "test_sid"
        assert "magnet:?xt=urn:btih:" in data.get("uri", "")

def test_push_to_synology_login_failure():
    url = "https://sktorrent.eu/announce.php"
    sample = f"d8:announce{len(url)}:{url}4:infod4:name12:sample_movie6:lengthi100e12:piece lengthi16384e6:pieces20:12345678901234567890ee".encode("utf-8")

    with patch.object(app, "SYNOLOGY_URL", "http://nas:5000"), \
         patch.object(app, "SYNOLOGY_USER", "user"), \
         patch.object(app, "SYNOLOGY_PASSWORD", "pass"), \
         patch("requests.Session") as mock_session_cls:

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.get.return_value.json.return_value = {"success": False, "error": {"code": 400}}

        success, msg = app.push_to_synology(sample, "test.torrent")
        assert success is False
        assert "Code: 400" in msg

def test_torrent_bytes_to_magnet_extraction():
    url = "https://sktorrent.eu/announce.php"
    sample = f"d8:announce{len(url)}:{url}4:infod4:name12:sample_movie6:lengthi100e12:piece lengthi16384e6:pieces20:12345678901234567890ee".encode("utf-8")
    magnet = app.torrent_bytes_to_magnet(sample)
    assert magnet is not None
    assert magnet.startswith("magnet:?xt=urn:btih:")
    assert "&dn=sample_movie" in magnet
    assert "&tr=https%3A%2F%2Fsktorrent.eu%2Fannounce.php" in magnet
