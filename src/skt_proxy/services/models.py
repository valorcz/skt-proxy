from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass(slots=True)
class TorrentDTO:
    id: str
    title: str
    category: str
    category_id: str
    size: str
    added_date: str
    seeders: int
    leechers: int
    download_link: str
    is_new: bool
    display_image: str
    image_url: str = ""
    local_image: Optional[str] = None
    csfd_id: Optional[str] = None
    csfd_score: Optional[str] = None
    content_type: str = "movie"
    created_at: Optional[float] = None
    genres: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class TorrentDetailsDTO:
    id: str
    title: str
    sktorrent_url: str = ""
    poster_url: str = ""
    csfd_url: str = ""
    csfd_id: str = ""
    csfd_score: Optional[str] = None
    databazeknih_url: str = ""
    imdb_url: str = ""
    content_type: str = "movie"
    season_episode: str = ""
    synopsis: str = ""
    mediainfo_text: str = ""
    trailer_url: str = ""
    infohash: str = ""
    size: str = ""
    uploader: str = ""
    added_date: str = ""
    languages: List[str] = field(default_factory=list)
    quality_tags: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    related_torrents: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
