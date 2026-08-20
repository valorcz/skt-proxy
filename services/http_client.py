import threading
import logging
import requests
import config

logger = logging.getLogger(__name__)

_thread_local = threading.local()
login_lock = threading.Lock()

# Fallback shared session for backwards-compatibility / test monkeypatching
default_session = requests.Session()
default_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})


def get_http_session() -> requests.Session:
    """Returns a thread-safe HTTP Session instance for the current thread."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        _thread_local.session = session
    return _thread_local.session


def ensure_login(session=None) -> bool:
    """Ensures the HTTP session is authenticated with SkTorrent."""
    s = session if session is not None else default_session
    cookies = s.cookies.get_dict()

    if "pass" in cookies or "uid" in cookies:
        return True

    with login_lock:
        cookies = s.cookies.get_dict()
        if "pass" in cookies or "uid" in cookies:
            return True

        if not config.SKT_USERNAME or not config.SKT_PASSWORD:
            logger.error("SKT_USERNAME or SKT_PASSWORD environment variables are not configured.")
            return False

        try:
            s.post(
                config.LOGIN_URL,
                data={"uid": config.SKT_USERNAME, "pwd": config.SKT_PASSWORD},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.error(f"Login failed due to network error: {e}")
            return False

        cookies = s.cookies.get_dict()
        return "pass" in cookies or "uid" in cookies
