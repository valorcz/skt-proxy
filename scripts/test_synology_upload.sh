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
TORRENT_URL="${2:-https://releases.ubuntu.com/22.04/ubuntu-22.04.4-live-server-amd64.iso.torrent}"

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
echo " Synology DownloadStation Task Tester"
echo " Target NAS:   $SYNOLOGY_URL"
echo " User:         $SYNOLOGY_USER"
echo " Destination:  '${DESTINATION}'"
echo " Test URL:     $TORRENT_URL"
echo "--------------------------------------------------------"

# 1. Login to DSM WebAPI
echo "[1/4] Logging into Synology DSM WebAPI..."
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
TOKEN=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('data', {}).get('synotoken', ''))" "$LOGIN_RESP" 2>/dev/null || echo "")

echo "[+] Logged in. SID: ${SID:0:8}... Token: ${TOKEN:0:8}..."

# Method 1: SYNO.DownloadStation.Task (V1 URL Task)
echo "--- Test 1: V1 URL Task (SYNO.DownloadStation.Task with uri parameter) ---"
V1_URL="${SYNOLOGY_URL}/webapi/DownloadStation/task.cgi"

CURL_V1_ARGS=(
    -s -k
    -b "$COOKIE_FILE"
    -c "$COOKIE_FILE"
    --data-urlencode "api=SYNO.DownloadStation.Task"
    --data-urlencode "method=create"
    --data-urlencode "version=1"
    --data-urlencode "uri=${TORRENT_URL}"
    --data-urlencode "_sid=${SID}"
)

if [ -n "$DESTINATION" ]; then
    CURL_V1_ARGS+=(--data-urlencode "destination=${DESTINATION}")
fi

V1_RESP=$(curl "${CURL_V1_ARGS[@]}" "$V1_URL")
echo "V1 URL Response: $V1_RESP"

V1_SUCCESS=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('success', False))" "$V1_RESP" 2>/dev/null || echo "False")

if [ "$V1_SUCCESS" == "True" ]; then
    echo "[+] SUCCESS! Task added via V1 URL API."
    exit 0
fi

# Method 2: SYNO.DownloadStation2.Task (V2 URL Task)
echo "--- Test 2: V2 URL Task (SYNO.DownloadStation2.Task with url parameter) ---"
V2_URL="${SYNOLOGY_URL}/webapi/entry.cgi"

CURL_V2_ARGS=(
    -s -k
    -b "$COOKIE_FILE"
    -c "$COOKIE_FILE"
    --data-urlencode "api=SYNO.DownloadStation2.Task"
    --data-urlencode "method=create"
    --data-urlencode "version=2"
    --data-urlencode 'type="url"'
    --data-urlencode "url=[\"${TORRENT_URL}\"]"
    --data-urlencode "create_list=false"
    --data-urlencode "_sid=${SID}"
)

if [ -n "$TOKEN" ]; then
    CURL_V2_ARGS+=(-H "X-SYNO-TOKEN: ${TOKEN}")
fi

if [ -n "$DESTINATION" ]; then
    CURL_V2_ARGS+=(--data-urlencode "destination=\"${DESTINATION}\"")
fi

V2_RESP=$(curl "${CURL_V2_ARGS[@]}" "$V2_URL")
echo "V2 URL Response: $V2_RESP"

V2_SUCCESS=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('success', False))" "$V2_RESP" 2>/dev/null || echo "False")

if [ "$V2_SUCCESS" == "True" ]; then
    echo "[+] SUCCESS! Task added via V2 URL API."
    exit 0
fi

# Method 3: SYNO.DownloadStation2.Task (V2 Direct POST File Upload)
echo "--- Test 3: V2 Direct File Upload (SYNO.DownloadStation2.Task) ---"
TMP_TORRENT=$(mktemp --suffix=.torrent)
echo -n "d8:announce27:http://tracker.example/announce4:info4:name12:test_file.txt6:lengthi100e12:piece lengthi16384e6:pieces20:12345678901234567890e" > "$TMP_TORRENT"
FILE_SIZE=$(wc -c < "$TMP_TORRENT" | tr -d ' ')
MTIME=$(python3 -c "import time; print(int(time.time()*1000))")

PYTHON_V2_RESP=$(python3 -c "
import requests, json, sys, os

url = '${SYNOLOGY_URL}/webapi/entry.cgi'
cookies = requests.utils.cookiejar_from_dict({'id': '${SID}'})
headers = {'X-SYNO-TOKEN': '${TOKEN}'} if '${TOKEN}' else {}

data = {
    'api': 'SYNO.DownloadStation2.Task',
    'method': 'create',
    'version': '2',
    'type': '\"file\"',
    'file': '[\"torrent\"]',
    'create_list': 'false',
    'size': '$FILE_SIZE',
    'mtime': '$MTIME'
}
if '${DESTINATION}':
    data['destination'] = '\"${DESTINATION}\"'

with open('$TMP_TORRENT', 'rb') as f:
    files = {'torrent': (os.path.basename('$TMP_TORRENT'), f.read(), 'application/x-bittorrent')}

resp = requests.post(url, cookies=cookies, data=data, files=files, headers=headers, verify=False)
print(resp.text)
")
echo "V2 File Upload Response: $PYTHON_V2_RESP"
rm -f "$TMP_TORRENT"

PYTHON_V2_SUCCESS=$(python3 -c "import sys, json; print(json.loads(sys.argv[1]).get('success', False))" "$PYTHON_V2_RESP" 2>/dev/null || echo "False")

if [ "$PYTHON_V2_SUCCESS" == "True" ]; then
    echo "[+] SUCCESS! Task added via V2 File Upload."
    exit 0
else
    echo "[!] All 3 task creation methods failed."
    exit 1
fi
