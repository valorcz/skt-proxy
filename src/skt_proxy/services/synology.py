import sys
import logging
import requests
import skt_proxy.config as config
from skt_proxy.services.torrent_parser import torrent_bytes_to_magnet
from skt_proxy.services.logger import setup_logger, log_siem_event

logger = setup_logger("skt-proxy.synology")


def get_synology_config():
    app_module = sys.modules.get("skt_proxy.app") or sys.modules.get("app")
    url = getattr(app_module, "SYNOLOGY_URL", config.SYNOLOGY_URL)
    user = getattr(app_module, "SYNOLOGY_USER", config.SYNOLOGY_USER)
    password = getattr(app_module, "SYNOLOGY_PASSWORD", config.SYNOLOGY_PASSWORD)
    destination = getattr(app_module, "SYNOLOGY_DESTINATION", config.SYNOLOGY_DESTINATION)
    verify_ssl = getattr(app_module, "SYNOLOGY_VERIFY_SSL", config.SYNOLOGY_VERIFY_SSL)
    return url, user, password, destination, verify_ssl


def push_to_synology(file_bytes, filename):
    """
    Pushes a torrent to Synology DownloadStation by converting its bencoded contents
    to a Magnet URI (with infohash, title, and all tracker URLs) and submitting it
    via SYNO.DownloadStation.Task (V1 API).
    """
    syn_url, syn_user, syn_pass, syn_dest, verify_ssl = get_synology_config()

    if not syn_url or not syn_user or not syn_pass:
        log_siem_event(
            logger,
            logging.WARNING,
            "Synology NAS configuration missing",
            event="nas_push_failed",
            reason="missing_config",
        )
        return False, "Synology NAS configuration (SYNOLOGY_URL, SYNOLOGY_USER, or SYNOLOGY_PASSWORD) is missing."

    magnet_uri = torrent_bytes_to_magnet(file_bytes)
    if not magnet_uri:
        log_siem_event(
            logger,
            logging.WARNING,
            f"Magnet conversion failed for {filename}",
            event="nas_push_failed",
            filename=filename,
            reason="bencode_error",
        )
        return False, "Failed to parse magnet link from torrent file."

    base_url = syn_url
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    base_url = base_url.rstrip("/")

    session = requests.Session()

    # 1. Login to Synology DSM (session=DownloadStation)
    login_url = f"{base_url}/webapi/auth.cgi"
    login_params = {
        "api": "SYNO.API.Auth",
        "version": "3",
        "method": "login",
        "account": syn_user,
        "passwd": syn_pass,
        "session": "DownloadStation",
        "format": "cookie",
    }
    try:
        login_resp = session.get(login_url, params=login_params, timeout=10, verify=verify_ssl)
        login_data = login_resp.json()
        if not login_data.get("success"):
            error_code = login_data.get("error", {}).get("code", "Unknown")
            log_siem_event(
                logger,
                logging.ERROR,
                f"Synology DSM login failed with code {error_code}",
                event="nas_login_failed",
                error_code=error_code,
            )
            return False, f"Synology DSM login failed (Code: {error_code})"

        sid = login_data.get("data", {}).get("sid", "")
        synotoken = login_data.get("data", {}).get("synotoken", "")
    except Exception as exc:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Synology DSM login exception: {exc}",
            event="nas_login_error",
            error=str(exc),
        )
        return False, f"Synology login error: {exc}"

    # 2. Post Magnet URI to SYNO.DownloadStation.Task (V1 API)
    task_url = f"{base_url}/webapi/DownloadStation/task.cgi"
    task_data = {
        "api": "SYNO.DownloadStation.Task",
        "method": "create",
        "version": "1",
        "uri": magnet_uri,
        "_sid": sid,
    }
    if syn_dest:
        task_data["destination"] = syn_dest

    headers = {}
    if synotoken:
        headers["X-SYNO-TOKEN"] = synotoken

    try:
        resp = session.post(task_url, data=task_data, headers=headers, timeout=15, verify=verify_ssl)
        result = resp.json()
        if result and result.get("success"):
            log_siem_event(
                logger,
                logging.INFO,
                f"Pushed magnet task to Synology NAS: {filename}",
                event="nas_push_success",
                filename=filename,
                method="POST",
            )
            return True, "Task added successfully"

        # Fallback to GET request
        resp_get = session.get(task_url, params=task_data, headers=headers, timeout=15, verify=verify_ssl)
        result_get = resp_get.json()
        if result_get and result_get.get("success"):
            log_siem_event(
                logger,
                logging.INFO,
                f"Pushed magnet task to Synology NAS via GET fallback: {filename}",
                event="nas_push_success",
                filename=filename,
                method="GET",
            )
            return True, "Task added successfully"

        code = result.get("error", {}).get("code", "Unknown")
        log_siem_event(
            logger,
            logging.ERROR,
            f"DownloadStation task creation rejected code {code}",
            event="nas_push_rejected",
            filename=filename,
            code=code,
        )
        return False, f"Failed to add task to DownloadStation (Code: {code})"
    except Exception as exc:
        log_siem_event(
            logger,
            logging.ERROR,
            f"Synology task creation exception: {exc}",
            event="nas_push_error",
            filename=filename,
            error=str(exc),
        )
        return False, f"Synology task creation error: {exc}"
