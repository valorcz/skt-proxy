import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

# --- BUILD VERSION RESOLUTION ---
def get_app_version() -> str:
    env_ver = os.environ.get("APP_VERSION", "").strip()
    if env_ver:
        return env_ver
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        if git_hash:
            return f"v1.4.0-{git_hash}"
    except Exception:
        pass
    return "v1.4.0"


APP_VERSION = get_app_version()

# --- TRACKER CONFIGURATION ---
SKT_USERNAME = os.environ.get("SKT_USERNAME", "")
SKT_PASSWORD = os.environ.get("SKT_PASSWORD", "")
LOGIN_URL = "https://sktorrent.eu/torrent/login.php?returnto=index.php"

# --- SYNOLOGY NAS CONFIGURATION ---
SYNOLOGY_URL = os.environ.get("SYNOLOGY_URL", "").rstrip("/")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER", "")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD", "")
SYNOLOGY_DSM_VERSION = int(os.environ.get("SYNOLOGY_DSM_VERSION", "7"))
SYNOLOGY_DESTINATION = os.environ.get("SYNOLOGY_DESTINATION", "")
SYNOLOGY_VERIFY_SSL = os.environ.get("SYNOLOGY_VERIFY_SSL", "false").lower() in ("true", "1", "yes")

# --- ACCESS CONTROL ---
NAS_ALLOWED_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("NAS_ALLOWED_EMAILS", "").split(",")
    if e.strip()
]

# --- APP & DATABASE CONSTANTS ---
CACHE_EXPIRY = 300  # seconds (5 min)
DB_PATH = "cache.db"
COVERS_DIR = os.path.join("static", "covers")

os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs("downloads", exist_ok=True)
