#!/usr/bin/env bash

set -e

# Load .env file if present
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    echo "[i] Sourcing environment variables from $ENV_FILE"
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

SYNOLOGY_URL="${SYNOLOGY_URL:-}"
SYNOLOGY_USER="${SYNOLOGY_USER:-}"
SYNOLOGY_PASSWORD="${SYNOLOGY_PASSWORD:-}"
DESTINATION="${1:-${SYNOLOGY_DESTINATION:-}}"
TORRENT_FILE="${2:-}"

if [ -z "$SYNOLOGY_URL" ] || [ -z "$SYNOLOGY_USER" ] || [ -z "$SYNOLOGY_PASSWORD" ]; then
    echo "[!] Error: SYNOLOGY_URL, SYNOLOGY_USER, and SYNOLOGY_PASSWORD must be set in .env or environment."
    exit 1
fi

if [[ ! "$SYNOLOGY_URL" =~ ^https?:// ]]; then
    SYNOLOGY_URL="http://${SYNOLOGY_URL}"
fi
SYNOLOGY_URL="${SYNOLOGY_URL%/}"

COOKIE_FILE=$(mktemp)
trap "rm -f $COOKIE_FILE" EXIT

echo "--------------------------------------------------------"
echo " Synology Magnet (All Trackers) Upload Tester"
echo " Target NAS:   $SYNOLOGY_URL"
echo " User:         $SYNOLOGY_USER"
echo " Destination:  '${DESTINATION}'"
echo " Torrent File: '${TORRENT_FILE}'"
echo "--------------------------------------------------------"

# 1. Login to DSM WebAPI
echo "[1/3] Logging into Synology DSM WebAPI..."
LOGIN_URL="${SYNOLOGY_URL}/webapi/auth.cgi"
LOGIN_RESP=$(curl -s -k -c "$COOKIE_FILE" -b "$COOKIE_FILE" \
    --data-urlencode "api=SYNO.API.Auth" \
    --data-urlencode "version=3" \
    --data-urlencode "method=login" \
    --data-urlencode "account=${SYNOLOGY_USER}" \
    --data-urlencode "passwd=${SYNOLOGY_PASSWORD}" \
    --data-urlencode "session=DownloadStation" \
    --data-urlencode "format=cookie" \
    "$LOGIN_URL")

echo "Login Response: $LOGIN_RESP"

SUCCESS=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('success', False))" "$LOGIN_RESP" 2>/dev/null || echo "False")

if [ "$SUCCESS" != "True" ]; then
    echo "[!] Login failed. Check your NAS credentials or URL."
    exit 1
fi

SID=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('data', {}).get('sid', ''))" "$LOGIN_RESP" 2>/dev/null || echo "")
echo "[+] Logged in. SID: ${SID:0:8}..."

# 2. Convert .torrent file to magnet link extracting ALL trackers
if [ -z "$TORRENT_FILE" ] || [ ! -f "$TORRENT_FILE" ]; then
    TORRENT_FILE=$(mktemp --suffix=.torrent)
    echo -n "d8:announce31:https://sktorrent.eu/announce13:announce-listll31:https://sktorrent.eu/announceel28:udp://tracker.open.org:6969/ee4:infod4:name12:test_file.txt6:lengthi100e12:piece lengthi16384e6:pieces20:12345678901234567890ee" > "$TORRENT_FILE"
    echo "[+] Generated sample .torrent file at $TORRENT_FILE"
fi

echo "[2/3] Extracting Infohash & ALL Tracker URLs..."
MAGNET_URI=$(uv run python -c "
import sys
from skt_proxy.services.torrent_parser import torrent_bytes_to_magnet

with open(sys.argv[1], 'rb') as f:
    print(torrent_bytes_to_magnet(f.read()) or '')
" "$TORRENT_FILE")

echo "[+] Converted Magnet URI with ALL Trackers: $MAGNET_URI"

if [ -z "$MAGNET_URI" ]; then
    echo "[!] Failed to parse infohash from torrent file."
    exit 1
fi

# 3. Test sending Magnet URI to Synology DownloadStation
echo "[3/3] Sending Magnet URI to Synology DownloadStation..."

TASK_URL="${SYNOLOGY_URL}/webapi/DownloadStation/task.cgi"

echo "--- Sending Magnet Task via HTTP POST ---"
CURL_POST_ARGS=(
    -s -k
    -b "$COOKIE_FILE"
    -c "$COOKIE_FILE"
    --data-urlencode "api=SYNO.DownloadStation.Task"
    --data-urlencode "method=create"
    --data-urlencode "version=1"
    --data-urlencode "uri=${MAGNET_URI}"
    --data-urlencode "_sid=${SID}"
)
if [ -n "$DESTINATION" ]; then
    CURL_POST_ARGS+=(--data-urlencode "destination=${DESTINATION}")
fi

POST_RESP=$(curl "${CURL_POST_ARGS[@]}" "$TASK_URL")
echo "POST Response: $POST_RESP"

POST_SUCCESS=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('success', False))" "$POST_RESP" 2>/dev/null || echo "False")

if [ "$POST_SUCCESS" == "True" ]; then
    echo "[+] SUCCESS! Magnet task added to DownloadStation with all trackers."
    exit 0
else
    echo "[!] Magnet task creation failed."
    exit 1
fi
