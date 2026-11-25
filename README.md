# skt-proxy

A lightweight, modern web interface and caching proxy for **SkTorrent.eu**, designed for fast browsing, mobile readability, ČSFD/DatabázeKnih rating badges, and one-click pushing to Synology DownloadStation.

---

## 🌟 Key Features

- **📱 Mobile-First UI**: Dark/Light mode, monocolored vector SVG icons, responsive streaming grid, and live title search.
- **🎬 Content-Type Intelligence**: Automatically detects Movies, TV Series, Books, Audiobooks, Music, and Software to present tailored metadata.
- **⭐ Smart Rating Badges**: Color-coded ČSFD rating badges (Green $\ge 70\%$, Amber $50\text{--}69\%$, Red $< 50\%$) extracted directly from descriptions and titles.
- **📚 Book & Database Integrations**: Automatic detection of **ČSFD.cz**, **DatabázeKnih.cz**, and **IMDb** links with direct link action buttons.
- **🚀 Synology NAS Integration**: Converts torrent files into multi-tracker Magnet URIs and pushes them directly to Synology DSM DownloadStation.
- **📖 Clean Info Modal Reader**: Parses details pages into a clean plot summary (removing junk MediaInfo lines), scrollable code blocks, YouTube trailers, file lists, and related release versions.
- **🔄 Always-Fresh Background Sync**: Periodically syncs new releases from SkTorrent in the background (`BACKGROUND_SYNC_INTERVAL`), even when no browser tabs are open.
- **👁️ Read Tracking**: Mark individual torrents read on click/download, bulk-clear via **Mark all read** next to the **New Only** filter, or let unread flags expire automatically after 48 hours (`NEW_FLAG_EXPIRY_SECONDS`).

---

## ⚙️ Configuration & Settings

`skt-proxy` is configured via environment variables (or a `.env` file in the root directory).

| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `SKT_USERNAME` | **Yes** | — | SkTorrent tracker username / User ID. |
| `SKT_PASSWORD` | **Yes** | — | SkTorrent tracker password. |
| `SYNOLOGY_URL` | Optional | `""` | Base URL or IP of your Synology NAS (e.g. `http://192.168.1.100:5000`). |
| `SYNOLOGY_USER` | Optional | `""` | Synology DSM account username. |
| `SYNOLOGY_PASSWORD` | Optional | `""` | Synology DSM account password. |
| `SYNOLOGY_DESTINATION` | Optional | `""` | Target download folder path on NAS (e.g. `downloads/movies`). |
| `SYNOLOGY_VERIFY_SSL` | Optional | `false` | Enable/disable SSL certificate validation for NAS HTTPS calls (`true`/`false`). |
| `NAS_ALLOWED_EMAILS` | Optional | `""` | Comma-separated list of authorized email addresses permitted to use NAS push actions. |
| `CACHE_EXPIRY` | Optional | `300` | On-demand cache freshness window in seconds. |
| `ENABLE_BACKGROUND_SYNC` | Optional | `true` | Enable/disable periodic background tracker sync when idle (`true`/`false`). |
| `BACKGROUND_SYNC_INTERVAL` | Optional | `CACHE_EXPIRY` | Interval in seconds between periodic background sync runs. |
| `PAGE_SIZE` | Optional | `40` | Number of torrents returned per API page. |
| `NEW_FLAG_EXPIRY_SECONDS` | Optional | `172800` | Seconds before unread `New` flags expire automatically (48h). |
| `DATA_DIR` | Optional | `data` | Directory for SQLite cache and cached cover images. |
| `LOG_FORMAT` | Optional | `kv` | Log output format (`kv` or `json`). |
| `APP_VERSION` | Optional | `v1.4.0-<sha>` | Override displayed build version badge. |

---

## 🚀 Quickstart

### Running with Docker Compose (Recommended)

```bash
docker compose up -d --build
```

Access the interface at `http://localhost:5002`.

### Running Locally with `uv`

```bash
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run gunicorn --workers=1 --threads=4 --bind=127.0.0.1:5000 skt_proxy.app:app
```

The `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` variable is for `gunicorn` to run well on macOS.

#### Alternative (CLI & Flask Development Server)

```bash
# Run via uv project CLI entrypoint
uv run skt-proxy

# Or run via module entrypoint
uv run python -m skt_proxy
```

### Running Tests

```bash
uv run pytest
```

---

## 🔒 Authentication & Security

For reverse-proxy setups using Forward Authentication (e.g., Traefik, Authelia, OAuth2 Proxy, Cloudflare Access), `skt-proxy` reads identity headers (`X-Forwarded-Email`) to authorize NAS push permissions.

See the dedicated **[OAuth2 & Forward Auth Documentation](docs/oauth2.md)** for detailed integration guides.

---

## 📄 License

MIT License.
