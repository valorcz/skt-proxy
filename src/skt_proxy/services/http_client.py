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


def get_http_session() -> requests.Session:
    """Returns a thread-safe HTTP Session instance for the current thread."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = create_resilient_session()
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
            success = "pass" in cookies or "uid" in cookies

            if success:
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
