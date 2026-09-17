import re
import urllib.parse
import hashlib
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_csfd_score(*texts):
    for text in texts:
        if not text:
            continue
        m = re.search(r"(?:csfd|čsfd)[\s:=_\-]*(\d{1,3})\s*%", text, re.I)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 100:
                return f"{val}%"
        m2 = re.search(r"(\d{1,3})\s*%\s*(?:csfd|čsfd)", text, re.I)
        if m2:
            val = int(m2.group(1))
            if 0 <= val <= 100:
                return f"{val}%"
    return None


def detect_content_type(category, title):
    cat_lower = (category or "").lower()
    title_lower = (title or "").lower()

    if any(k in cat_lower for k in ["knihy", "časopis", "slovo", "book", "audiobook", "literatúra"]):
        return "book"
    if any(k in cat_lower for k in ["hudba", "music", "album", "discography"]):
        return "music"
    if any(k in cat_lower for k in ["hry", "game", "aplikáci", "software", "program"]):
        return "software"
    if any(k in cat_lower for k in ["seriál", "series", "tv"]) or re.search(r"\bS\d{1,2}E\d{1,2}\b|\b\d{1,2}x\d{1,2}\b", title_lower, re.I):
        return "tv"
    return "movie"


def parse_languages(text):
    if not text:
        return []
    found = set()
    patterns = [
        (r"\b(cz|czech|česky|čeština)\b", "CZ"),
        (r"\b(sk|slovak|slovensky|slovenčina)\b", "SK"),
        (r"\b(en|eng|english|anglicky)\b", "EN"),
        (r"\b(de|ger|german|nemecky)\b", "DE"),
        (r"\b(pl|pol|polish|poľsky)\b", "PL"),
        (r"\b(fr|french|francúzsky)\b", "FR"),
        (r"\b(ru|russian|rusky)\b", "RU"),
    ]
    for pattern, code in patterns:
        if re.search(pattern, text, re.I):
            found.add(code)
    return sorted(list(found))


def torrent_bytes_to_magnet(file_bytes):
    """
    Extracts infohash, title, and all tracker URLs from raw .torrent file bytes and builds a magnet URI.
    """
    try:
        info_start = file_bytes.find(b"4:info")
        if info_start == -1:
            return None
        dict_start = info_start + 6
        if file_bytes[dict_start:dict_start + 1] != b"d":
            return None

        depth = 0
        i = dict_start
        while i < len(file_bytes):
            char = file_bytes[i:i + 1]
            if char in (b"d", b"l"):
                depth += 1
                i += 1
            elif char == b"e":
                depth -= 1
                i += 1
                if depth == 0:
                    break
            elif char.isdigit():
                colon = file_bytes.find(b":", i)
                if colon == -1:
                    break
                length = int(file_bytes[i:colon])
                i = colon + 1 + length
            elif char == b"i":
                e_pos = file_bytes.find(b"e", i)
                if e_pos == -1:
                    break
                i = e_pos + 1
            else:
                i += 1

        info_bytes = file_bytes[dict_start:i]
        infohash = hashlib.sha1(info_bytes).hexdigest()

        name_match = re.search(rb"4:name(\d+):", info_bytes)
        name_str = ""
        if name_match:
            try:
                nl = int(name_match.group(1))
                ns = name_match.end()
                name_str = info_bytes[ns:ns + nl].decode("utf-8", errors="ignore")
            except Exception:
                pass

        trackers = []
        for m in re.finditer(rb'(https?://[^\s\"\'<>]+|udp://[^\s\"\'<>]+)', file_bytes):
            start = m.start(1)
            if start > 0 and file_bytes[start - 1:start] == b':':
                digits_end = start - 1
                digits_start = digits_end
                while digits_start > 0 and file_bytes[digits_start - 1:digits_start].isdigit():
                    digits_start -= 1
                if digits_start < digits_end:
                    try:
                        length = int(file_bytes[digits_start:digits_end])
                        tr_url = file_bytes[start:start + length].decode("utf-8", errors="ignore")
                        if tr_url.startswith(("http://", "https://", "udp://")) and tr_url not in trackers:
                            trackers.append(tr_url)
                    except Exception:
                        pass

        magnet = f"magnet:?xt=urn:btih:{infohash}"
        if name_str:
            magnet += f"&dn={urllib.parse.quote(name_str)}"
        for tr in trackers:
            magnet += f"&tr={urllib.parse.quote(tr, safe='')}"

        return magnet
    except Exception as e:
        logger.warning(f"Failed to extract magnet from torrent bytes: {e}")
        return None


def parse_torrents(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    torrents = []

    for a_tag in soup.find_all(
        "a", href=lambda href: href and "details.php?name=" in href
    ):
        parent_td = a_tag.find_parent("td")
        if not parent_td:
            continue

        href = a_tag["href"]
        parsed_url = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed_url.query)
        torrent_id = qs.get("id", [""])[0]
        if not torrent_id:
            continue

        title = str(a_tag.contents[-1]).strip() if a_tag.contents else "Unknown Title"
        img_tag = a_tag.find("img")
        image_url = img_tag.get("data-src") or img_tag.get("src", "") if img_tag else ""
        if image_url:
            image_url = urllib.parse.urljoin("https://sktorrent.eu/torrent/", image_url)

        cat_tag = parent_td.find("a", href=lambda h: h and "category=" in h)
        category = cat_tag.text.strip() if cat_tag else "Unknown"
        category_id = (
            urllib.parse.parse_qs(urllib.parse.urlparse(cat_tag["href"]).query).get(
                "category", ["0"]
            )[0]
            if cat_tag and "href" in cat_tag.attrs
            else "0"
        )

        genre_tags = parent_td.find_all("a", href=lambda h: h and "zaner=" in h)
        genres = [g.text.strip() for g in genre_tags]

        formatted_title = title.replace(" ", "_") + ".torrent"
        download_link = (
            f"/proxy_download?id={torrent_id}&f={urllib.parse.quote(formatted_title)}"
        )

        td_text = parent_td.get_text(separator=" ")
        size_match = re.search(r"Velkost\s+(.*?)\s+\|", td_text)
        size = size_match.group(1).strip() if size_match else "N/A"

        date_match = re.search(r"Pridany\s+([\d/]+)", td_text)
        added_date = "N/A"
        if date_match:
            parts = date_match.group(1).strip().split("/")
            if len(parts) == 3:
                added_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
            else:
                added_date = date_match.group(1).strip()

        seed_match = re.search(r"Odosielaju\s*:\s*(\d+)", td_text)
        seeders = int(seed_match.group(1)) if seed_match else 0

        leech_match = re.search(r"Stahuju\s*:\s*(\d+)", td_text)
        leechers = int(leech_match.group(1)) if leech_match else 0

        csfd_score = extract_csfd_score(title, td_text)
        content_type = detect_content_type(category, title)
        languages = parse_languages(title)

        torrents.append(
            {
                "id": torrent_id,
                "title": title,
                "csfd_score": csfd_score,
                "content_type": content_type,
                "languages": languages,
                "image_url": image_url,
                "category": category,
                "category_id": category_id,
                "genres": genres,
                "size": size,
                "added_date": added_date,
                "seeders": seeders,
                "leechers": leechers,
                "download_link": download_link,
            }
        )
    return torrents


def extract_quality_tags(title: str) -> list[str]:
    """Extracts clean, high-level resolution, source, and codec badges from release title."""
    tags = []
    t_lower = (title or "").lower()

    # Resolution
    if re.search(r"\b(2160p|4k|uhd)\b", t_lower):
        tags.append("4K UHD")
    elif re.search(r"\b(1080p|1080i|fhd)\b", t_lower):
        tags.append("1080p")
    elif re.search(r"\b(720p|hd)\b", t_lower):
        tags.append("720p")
    elif re.search(r"\b(576p|480p|sd)\b", t_lower):
        tags.append("SD")

    # Source
    if re.search(r"\bremux\b", t_lower):
        tags.append("Remux")
    elif re.search(r"\b(bluray|bdrip|brrip)\b", t_lower):
        tags.append("BluRay")
    elif re.search(r"\b(web-?dl|webrip)\b", t_lower):
        tags.append("WEB")
    elif re.search(r"\b(dvdrip|dvd)\b", t_lower):
        tags.append("DVD")
    elif re.search(r"\bhdtv\b", t_lower):
        tags.append("HDTV")

    # Video Codec
    if re.search(r"\b(x265|hevc|h\.?265)\b", t_lower):
        tags.append("HEVC/x265")
    elif re.search(r"\b(x264|avc|h\.?264)\b", t_lower):
        tags.append("AVC/x264")
    elif re.search(r"\bav1\b", t_lower):
        tags.append("AV1")

    # Dynamic Range
    if re.search(r"\b(hdr10\+|hdr10|hdr)\b", t_lower):
        tags.append("HDR")
    if re.search(r"\b(dolby[\s.]*vision|dovi|\bdv\b)", t_lower):
        tags.append("DV")

    return tags


def is_technical_encoding_line(line: str) -> bool:
    """Returns True if a line consists of technical MediaInfo, stream, or encoding parameters."""
    l = line.strip()
    if not l:
        return True

    # Tracker noise / boilerplate
    if any(k in l for k in [
        "Rolovatelne Media Info", "Podakuj za torrent", "Pridaj vlastnu verziu",
        "Text bude automaticky centrovany", "Darovat seedbodov", "http://", "https://"
    ]):
        return True

    # MediaInfo Section Headers
    if re.match(r"^(?:General|Hlavné|Hlavní|Video|Obraz|Audio|Zvuk|Text|Titulky|Menu|Chapters|Kapitoly)(?:\s*#\d+)?\s*$", l, re.I):
        return True

    # Standard MediaInfo Key: Value patterns
    if re.match(
        r"^(?:Format|Formát|Format/Info|Format_Profile|Format_Settings|Codec|Codec\s*ID|Kodek|"
        r"Bit\s*rate|Bitrate|Dátový\s*tok|Datovy\s*tok|Celkový\s*dátový\s*tok|Overall\s*bit\s*rate|"
        r"Width|Height|Resolution|Rozlíšenie|Rozlišení|Display\s*aspect\s*ratio|Pomer\s*strán|"
        r"Frame\s*rate|Snímková\s*frekvencia|Framerate|FPS|Scan\s*type|"
        r"Color\s*space|Chroma\s*subsampling|Bit\s*depth|"
        r"Stream\s*size|Veľkosť\s*streamu|Velikost\s*streamu|"
        r"Writing\s*(?:library|application)|Použitá\s*knižnica|Knihovna|"
        r"Encoding\s*settings|Nastavenia\s*kódovania|Nastavení\s*enkódování|"
        r"Channel\(s\)|Kanál\(y\)|Sampling\s*rate|Vzorkovacia\s*frekvencia|Compression\s*mode|"
        r"Unique\s*ID|Complete\s*name|File\s*size|Duration|Trvanie|Délka)[\s:]+",
        l,
        re.I,
    ):
        return True

    # Encoder settings dumps (e.g. cabac=1 / ref=4 / deblock=1:0:0...)
    if re.search(r"\b(?:cabac=\d|ref=\d|deblock=|analyse=|me_hex|subme=\d|rc=2pass|aq=\d)\b", l, re.I):
        return True

    # Inline stream parameter dumps (e.g. "1920x1080 23.976fps x264 5000kbps AC3 5.1")
    if re.search(r"\b(?:\d{3,4}x\d{3,4}|\d{3,4}p)\b", l, re.I) and any(
        k in l.lower() for k in ["fps", "kbps", "mbps", "x264", "x265", "hevc", "avc", "ac3", "dts", "aac"]
    ):
        return True

    # Metadata headers that aren't synopsis
    if re.match(r"^(?:Jazyk|Language|Audio|Video|Titulky|Subtitles|Veľkosť|Velkost|Size|Hash|Info_Hash|Seed|Leech)[\s:]", l, re.I):
        return True

    return False


def find_main_content_td(soup):
    # 1. Search for table row with Popis / Description / Obsah
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            heading = tds[0].get_text(strip=True).lower()
            if "popis" in heading or "description" in heading or "obsah" in heading:
                return tds[1]

    # 2. Fallback to candidate td elements
    for td in soup.find_all("td", class_="lista"):
        text = td.get_text(separator=" ", strip=True)
        if len(text) > 100 and any(k in text for k in ["Obsah", "Popis", "CSFD", "csfd", "databazeknih", "Dej", "Film"]):
            return td
    return None


def extract_clean_synopsis(soup):
    td = find_main_content_td(soup)
    if not td:
        return ""

    # Replace breaks and paragraph boundaries with newlines
    for br in td.find_all(["br", "hr"]):
        br.replace_with("\n")
    for block in td.find_all(["p", "div"]):
        block.append("\n")

    raw_text = td.get_text()

    # Search for an explicit Obsah / Popis block if marked
    obsah_match = re.search(
        r"(?:Obsah|Popis|Dej|Summary|Plot|Popis\s+filmu)[\s:]+(.*?)(?=(?:\n\s*){2,}(?:Media\s*info|Video|Audio|CSFD|ČSFD|https?://|$))",
        raw_text,
        re.I | re.DOTALL,
    )
    candidate_text = obsah_match.group(1) if obsah_match else raw_text

    clean_lines = []
    for raw_line in candidate_text.split("\n"):
        line = raw_line.strip()
        if not line:
            if clean_lines and clean_lines[-1] != "":
                clean_lines.append("")
            continue

        # Strip heading label from start of line if present
        line = re.sub(r"^(?:Obsah|Popis|Dej|Summary|Plot|Popis\s+filmu)[\s:]+", "", line, flags=re.I).strip()
        if not line:
            continue

        if is_technical_encoding_line(line):
            continue

        clean_lines.append(line)

    result = "\n".join(clean_lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)

    # Require at least 20 characters of genuine narrative text
    if len(result) >= 20 and not is_technical_encoding_line(result):
        return result

    return ""


def extract_related_torrents(soup, current_tid=""):
    related = []
    seen_ids = {current_tid} if current_tid else set()
    for a in soup.find_all("a", href=re.compile(r"details\.php\?.*id=")):
        href = a.get("href", "")
        m = re.search(r"id=([a-f0-9]{40})", href, re.I)
        if m:
            rel_id = m.group(1)
            rel_title = a.text.strip()
            if rel_id not in seen_ids and rel_title and len(rel_title) > 3:
                seen_ids.add(rel_id)
                related.append({
                    "id": rel_id,
                    "title": rel_title,
                    "sktorrent_url": f"https://sktorrent.eu/torrent/details.php?id={rel_id}",
                })
    return related


def parse_skt_details_html(html_text, category="", tid=""):
    soup = BeautifulSoup(html_text, "html.parser")

    details = {
        "id": tid,
        "title": "",
        "sktorrent_url": f"https://sktorrent.eu/torrent/details.php?id={tid}" if tid else "",
        "poster_url": "",
        "csfd_url": "",
        "csfd_id": "",
        "databazeknih_url": "",
        "imdb_url": "",
        "content_type": "movie",
        "languages": [],
        "quality_tags": [],
        "season_episode": "",
        "synopsis": "",
        "mediainfo_text": "",
        "trailer_url": "",
        "infohash": "",
        "size": "",
        "uploader": "",
        "added_date": "",
        "files": [],
        "related_torrents": [],
    }

    meta_name = soup.find("meta", {"itemprop": "name"})
    if meta_name and meta_name.get("content"):
        details["title"] = meta_name["content"].strip()

    # External Links
    csfd_link = soup.find("a", href=re.compile(r"csfd\.cz/film/"))
    if csfd_link and csfd_link.get("href"):
        details["csfd_url"] = csfd_link["href"]
        m = re.search(r"csfd\.cz/film/(\d+)", csfd_link["href"])
        if m:
            details["csfd_id"] = m.group(1)

    book_link = soup.find("a", href=re.compile(r"(databazeknih\.cz|cbdb\.cz|goodreads\.com)"))
    if book_link and book_link.get("href"):
        details["databazeknih_url"] = book_link["href"]

    imdb_link = soup.find("a", href=re.compile(r"imdb\.com/title/"))
    if imdb_link and imdb_link.get("href"):
        details["imdb_url"] = imdb_link["href"]

    poster_img = soup.find("img", src=re.compile(r"cdn\.sktorrent\.eu/obrazky/"))
    if poster_img and poster_img.get("src"):
        src = poster_img["src"]
        if src.startswith("//"):
            src = "https:" + src
        details["poster_url"] = src

    mediainfo_div = (
        soup.find("div", {"id": re.compile(r"filesMediainfo", re.I)}) or
        soup.find("div", {"name": re.compile(r"filesMediainfo", re.I)})
    )
    if mediainfo_div:
        raw_mi = mediainfo_div.get_text(separator="\n", strip=True)
        clean_mi_lines = [
            line for line in raw_mi.split("\n")
            if not any(skip in line for skip in ["Rolovatelne Media Info", "Podakuj za torrent", "Pridaj vlastnu verziu", "Text bude automaticky centrovany", "Darovat seedbodov"])
        ]
        details["mediainfo_text"] = "\n".join(clean_mi_lines).strip()

    iframe = soup.find("iframe", src=re.compile(r"youtube\.com"))
    if iframe and iframe.get("src"):
        details["trailer_url"] = iframe["src"]

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            k = tds[0].get_text(strip=True).lower()
            v = tds[1].get_text(strip=True)
            if "názov" in k or "nazov" in k or "title" in k:
                if not details["title"]:
                    details["title"] = v
            elif "info_hash" in k or "hash" in k:
                details["infohash"] = v
            elif "veľkosť" in k or "velkost" in k or "size" in k:
                details["size"] = v
            elif "pridal" in k or "uploader" in k:
                details["uploader"] = v
            elif "súbory" in k or "subory" in k or "files" in k:
                files_text = tds[1].get_text(separator="\n", strip=True)
                details["files"] = [f.strip() for f in files_text.split("\n") if f.strip()]

    details["synopsis"] = extract_clean_synopsis(soup)
    details["quality_tags"] = extract_quality_tags(details["title"])

    details["csfd_score"] = extract_csfd_score(details["title"], details["synopsis"], html_text)
    details["content_type"] = detect_content_type(category, details["title"])
    details["languages"] = parse_languages(f"{details['title']} {details['synopsis']}")

    se_match = re.search(r"\b(S\d{1,2}E\d{1,2}|\d{1,2}x\d{1,2})\b", details["title"], re.I)
    if se_match:
        details["season_episode"] = se_match.group(1).upper()

    details["related_torrents"] = extract_related_torrents(soup, tid)

    return details
