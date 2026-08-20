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
- **👁️ Automatic Read Tracking**: New torrent badges automatically fade out when scrolled into view (via `IntersectionObserver`) or after 48 hours.

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

---

## 🚀 Quickstart

### Running with Docker Compose (Recommended)

```bash
docker compose up -d --build
```

Access the interface at `http://localhost:5001`.

### Running Locally with `uv`

```bash
# Install dependencies & run Gunicorn server
uv run gunicorn --workers=1 --threads=4 --bind=127.0.0.1:5000 app:app

# Or run Flask development server
uv run flask --app app run --port 5001 --debug
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
