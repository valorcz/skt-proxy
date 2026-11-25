import threading
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

import skt_proxy.config as config
from skt_proxy.services.logger import setup_logger, log_siem_event

logger = setup_logger("skt-proxy.http")

_thread_local = threading.local()
login_lock = threading.Lock()
_shared_cookies: dict[str, str] = {}


def create_resilient_session() -> requests.Session:
    """Creates a requests Session with HTTP retry adapter and backoff policy."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    retries = Retry(
        total=3,
        backoff_factor=1.0,  # Wait 1s, 2s, 4s
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Fallback shared session for backwards-compatibility / test monkeypatching
default_session = create_resilient_session()


def _has_auth_cookies(cookies: dict) -> bool:
    return "pass" in cookies or "uid" in cookies


def get_http_session() -> requests.Session:
    """Returns a thread-safe HTTP Session instance for the current thread."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = create_resilient_session()
    with login_lock:
        if _shared_cookies and not _has_auth_cookies(_thread_local.session.cookies.get_dict()):
            _thread_local.session.cookies.update(_shared_cookies)
    return _thread_local.session


def invalidate_session(session=None) -> None:
    """Clears cached auth cookies globally and on the given session."""
    with login_lock:
        _shared_cookies.clear()
        s = session if session is not None else default_session
        for cookie in list(s.cookies):
            if cookie.name in ("pass", "uid"):
                try:
                    s.cookies.clear(cookie.domain, cookie.path, cookie.name)
                except KeyError:
                    pass


def is_login_response(resp) -> bool:
    """Detects whether a tracker response indicates an expired/unauthenticated session."""
    if getattr(resp, "status_code", None) == 401:
        return True
    resp_url = getattr(resp, "url", None)
    if isinstance(resp_url, str) and "login.php" in resp_url:
        return True
    resp_text = getattr(resp, "text", None)
    if isinstance(resp_text, str) and "login.php" in resp_text and 'name="pwd"' in resp_text:
        return True
    return False


def ensure_login(session=None) -> bool:
    """Ensures the HTTP session is authenticated with SkTorrent."""
    s = session if session is not None else default_session
    cookies = s.cookies.get_dict()

    if _has_auth_cookies(cookies):
        return True

    with login_lock:
        cookies = s.cookies.get_dict()
        if _has_auth_cookies(cookies):
            return True

        if _has_auth_cookies(_shared_cookies):
            s.cookies.update(_shared_cookies)
            return True

        if not config.SKT_USERNAME or not config.SKT_PASSWORD:
            log_siem_event(
                logger,
                logging.ERROR,
                "SKT credentials missing",
                event="login_failed",
                reason="missing_credentials",
            )
            return False

        try:
            resp = s.post(
                config.LOGIN_URL,
                data={"uid": config.SKT_USERNAME, "pwd": config.SKT_PASSWORD},
                timeout=10,
            )
            cookies = s.cookies.get_dict()
            success = _has_auth_cookies(cookies)

            if success:
                _shared_cookies.clear()
                _shared_cookies.update({str(k): str(v) for k, v in cookies.items()})
                log_siem_event(
                    logger,
                    logging.INFO,
                    "Authenticated with SkTorrent tracker",
                    event="login_success",
                    user_id=config.SKT_USERNAME,
                )
            else:
                log_siem_event(
                    logger,
                    logging.WARNING,
                    "Tracker authentication rejected credentials",
                    event="login_rejected",
                    status_code=resp.status_code,
                )
            return success
        except requests.RequestException as e:
            log_siem_event(
                logger,
                logging.ERROR,
                f"Login network error: {e}",
                event="login_network_error",
                error=str(e),
            )
            return False


def skt_get(url: str, session=None, **kwargs):
    """Performs an authenticated GET request and retries once if tracker session expired."""
    s = session if session is not None else get_http_session()
    resp = s.get(url, **kwargs)
    if is_login_response(resp):
        invalidate_session(s)
        if ensure_login(s):
            resp = s.get(url, **kwargs)
    return resp
