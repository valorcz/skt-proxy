import pytest
from unittest.mock import patch
import config
import app


@pytest.fixture
def client():
    app.app.config["TESTING"] = True
    with app.app.test_client() as client:
        yield client


def test_nas_access_allowed_when_no_restriction():
    with patch.object(config, "NAS_ALLOWED_EMAILS", []):
        with app.app.test_request_context():
            assert app.can_use_nas() is True


def test_nas_access_blocked_when_email_header_missing():
    with patch.object(config, "NAS_ALLOWED_EMAILS", ["admin@example.com"]):
        with app.app.test_request_context():
            assert app.can_use_nas() is False


def test_nas_access_blocked_when_unauthorized_email():
    with patch.object(config, "NAS_ALLOWED_EMAILS", ["admin@example.com"]):
        with app.app.test_request_context(headers={"X-Forwarded-Email": "hacker@evil.com"}):
            assert app.can_use_nas() is False


def test_nas_access_granted_when_authorized_email():
    with patch.object(config, "NAS_ALLOWED_EMAILS", ["admin@example.com"]):
        with app.app.test_request_context(headers={"X-Forwarded-Email": "admin@example.com"}):
            assert app.can_use_nas() is True


def test_api_send_to_nas_returns_403_when_unauthorized(client):
    with patch.object(config, "NAS_ALLOWED_EMAILS", ["admin@example.com"]):
        resp = client.post("/api/send_to_nas", json={"id": "123", "f": "test.torrent"})
        assert resp.status_code == 403
        assert "Forbidden" in resp.json["error"]
