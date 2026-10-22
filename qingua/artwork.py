from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .config import image_cache_dir
from .providers import DESKTOP_UA, DOUBAN_MOBILE_UA

MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ArtworkError(RuntimeError):
    pass


def safe_stem(value: str) -> str:
    text = INVALID_FILENAME.sub("_", value).strip(" .")
    return text[:120] or "artwork"


def _extension(data: bytes, content_type: str = "") -> str:
    for magic, suffix in MAGIC:
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    lowered = content_type.lower()
    if "png" in lowered:
        return ".png"
    if "webp" in lowered:
        return ".webp"
    return ".jpg"


def _headers_for(provider: str) -> dict[str, str]:
    if provider == "douban":
        return {"User-Agent": DOUBAN_MOBILE_UA, "Referer": "https://m.douban.com/"}
    if provider == "bilibili":
        return {
            "User-Agent": DESKTOP_UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
    if provider == "fankuhub":
        return {"User-Agent": "Mozilla/5.0", "Referer": "https://fankuhub.com/"}
    return {"User-Agent": DESKTOP_UA}


def fetch_image(url: str, provider: str = "", timeout: int = 30, proxy: str = "") -> bytes:
    if not url:
        raise ArtworkError("没有图片地址")
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    for suffix in (".jpg", ".png", ".webp", ".gif"):
        cached = image_cache_dir() / f"{key}{suffix}"
        if cached.is_file() and cached.stat().st_size > 1024:
            return cached.read_bytes()

    request = urllib.request.Request(url, headers=_headers_for(provider))
    opener = urllib.request.build_opener()
    if proxy.strip():
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy.strip(), "https": proxy.strip()})
        )
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type") or ""
    except Exception as exc:  # noqa: BLE001 - network boundary
        raise ArtworkError(f"图片下载失败：{str(exc)[:120]}") from exc
    if len(data) < 1024:
        raise ArtworkError("图片内容过小，可能已失效")
    suffix = _extension(data, content_type)
    cache_file = image_cache_dir() / f"{key}{suffix}"
    temp = cache_file.with_suffix(cache_file.suffix + ".part")
    temp.write_bytes(data)
    temp.replace(cache_file)
    return data


def save_image(
    url: str,
    target_dir: Path,
    stem: str,
    *,
    provider: str = "",
    overwrite: bool = False,
    timeout: int = 30,
    proxy: str = "",
) -> Path:
    source = (url or "").strip()
    if not source:
        raise ArtworkError("没有图片地址")
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_stem(stem)
    for suffix in (".jpg", ".png", ".webp", ".gif"):
        existing = target_dir / f"{safe}{suffix}"
        if existing.is_file() and existing.stat().st_size > 1024 and not overwrite:
            return existing

    if source.startswith(("http://", "https://")):
        data = fetch_image(source, provider, timeout, proxy=proxy)
    else:
        local = Path(source).expanduser()
        if not local.is_file():
            raise ArtworkError("本地图片不存在")
        try:
            data = local.read_bytes()
        except OSError as exc:
            raise ArtworkError(f"读取本地图片失败：{exc}") from exc
        if len(data) < 1024:
            raise ArtworkError("本地图片内容过小")

    suffix = _extension(data)
    target = target_dir / f"{safe}{suffix}"
    temp = target.with_suffix(target.suffix + ".part")
    temp.write_bytes(data)
    temp.replace(target)
    return target


def image_content_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "image/jpeg")


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ArtworkError("图片地址必须以 http(s) 开头")
    return url
