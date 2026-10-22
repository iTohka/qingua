from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "轻刮 QingGua"
APP_SLUG = "qingua"
VERSION = "0.1.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
WEB_DIR = PROJECT_ROOT / "web"

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".wmv",
    ".m4v",
    ".ts",
    ".m2ts",
    ".webm",
    ".flv",
    ".rmvb",
    ".rm",
    ".mpg",
    ".mpeg",
    ".vob",
    ".3gp",
    ".ogv",
}

SKIP_DIRS = {
    "@eadir",
    "#recycle",
    "$recycle.bin",
    "system volume information",
    "node_modules",
    ".git",
    ".svn",
    "lost+found",
    "temp",
    "tmp",
}

DEFAULT_SETTINGS = {
    "source": "auto",              # auto | douban | fankuhub | bilibili | tmdb
    "douban_enabled": True,
    "fankuhub_enabled": True,
    "bilibili_enabled": True,
    "tmdb_enabled": False,
    "tmdb_api_key": "",
    "tmdb_base": "https://api.themoviedb.org/3",
    "tmdb_image_base": "https://image.tmdb.org/t/p",
    "image_proxy": "",
    "http_proxy": "",
    "language": "zh-CN",
    "auto_threshold": 82,
    "min_file_mb": 0,
    "write_nfo": True,
    "download_images": True,
    "overwrite_nfo": False,
    "overwrite_artwork": True,
    "probe_files": True,
    "organize": False,
    "ffprobe_path": "",
    "request_timeout": 20,
    "theme": "dark",
}

SECRET_KEYS = {"tmdb_api_key"}


def data_dir() -> Path:
    override = os.environ.get("QINGUA_DATA")
    path = Path(override).expanduser() if override else DEFAULT_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "qingua.db"


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_cache_dir() -> Path:
    path = cache_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path
