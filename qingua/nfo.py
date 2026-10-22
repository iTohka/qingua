from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .artwork import safe_stem
from .parser import explicit_season_number


def _add(parent: ET.Element, tag: str, text: Any = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items() if v is not None})
    if text is not None and str(text) != "":
        element.text = str(text)
    return element


def _add_many(parent: ET.Element, tag: str, values: list[Any] | tuple[Any, ...] | None) -> None:
    for value in values or []:
        if value:
            _add(parent, tag, value)


def _rating(parent: ET.Element, meta: dict[str, Any]) -> None:
    value = meta.get("rating")
    if value is None:
        return
    ratings = _add(parent, "ratings")
    source = meta.get("provider") or "tmdb"
    name = "douban" if source in ("douban", "fankuhub", "bilibili") else "tmdb"
    rating = _add(ratings, "rating", name=name, max="10", default="true")
    _add(rating, "value", value)
    if meta.get("votes"):
        _add(rating, "votes", meta["votes"])


def _uniqueids(parent: ET.Element, meta: dict[str, Any]) -> None:
    ids = [
        ("tmdb", meta.get("tmdb_id")),
        ("douban", meta.get("douban_id")),
        ("imdb", meta.get("imdb_id")),
        ("fankuhub", meta.get("provider_id") if meta.get("provider") == "fankuhub" else None),
        ("bilibili", meta.get("provider_id") if meta.get("provider") == "bilibili" else None),
    ]
    first = True
    for kind, value in ids:
        if value:
            _add(parent, "uniqueid", value, type=kind, default="true" if first else "false")
            first = False


def _actors(parent: ET.Element, meta: dict[str, Any]) -> None:
    for index, actor in enumerate((meta.get("actors") or [])[:24]):
        if isinstance(actor, str):
            actor = {"name": actor}
        if not actor.get("name"):
            continue
        node = _add(parent, "actor")
        _add(node, "name", actor["name"])
        _add(node, "role", actor.get("role") or "")
        _add(node, "order", actor.get("order", index))
        if actor.get("thumb"):
            _add(node, "thumb", actor["thumb"])


def _fileinfo(parent: ET.Element, probe: dict[str, Any] | None) -> None:
    if not probe:
        return
    info = _add(parent, "fileinfo")
    details = _add(info, "streamdetails")
    video = probe.get("video") or {}
    if video:
        v = _add(details, "video")
        _add(v, "codec", video.get("codec") or "")
        if video.get("aspect"):
            _add(v, "aspect", f"{float(video['aspect']):.3f}")
        _add(v, "width", video.get("width") or "")
        _add(v, "height", video.get("height") or "")
        if probe.get("duration"):
            _add(v, "durationinseconds", int(float(probe["duration"])))
        _add(v, "stereomode", "")
    for audio in (probe.get("audios") or [])[:4]:
        a = _add(details, "audio")
        _add(a, "codec", audio.get("codec") or "")
        _add(a, "language", audio.get("language") or "")
        _add(a, "channels", audio.get("channels") or "")
    for subtitle in (probe.get("subtitles") or [])[:8]:
        s = _add(details, "subtitle")
        _add(s, "language", subtitle.get("language") or "")


def _finish(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n"


def build_movie_nfo(
    meta: dict[str, Any],
    poster_name: str = "",
    fanart_name: str = "",
    probe: dict[str, Any] | None = None,
) -> str:
    root = ET.Element("movie")
    _add(root, "title", meta.get("title"))
    _add(root, "originaltitle", meta.get("original_title"))
    _add(root, "sorttitle", meta.get("sort_title") or meta.get("title"))
    _rating(root, meta)
    _add(root, "plot", meta.get("overview"))
    _add(root, "outline", (meta.get("overview") or "")[:260])
    _add(root, "tagline", meta.get("tagline"))
    if meta.get("runtime"):
        _add(root, "runtime", meta["runtime"])
    _add(root, "year", meta.get("year"))
    _add(root, "premiered", meta.get("release_date"))
    _add(root, "releasedate", meta.get("release_date"))
    _add(root, "playcount", 0)
    _uniqueids(root, meta)
    _add_many(root, "genre", meta.get("genres"))
    _add_many(root, "country", meta.get("countries"))
    _add_many(root, "studio", meta.get("studios"))
    _add_many(root, "director", meta.get("directors"))
    _add_many(root, "credits", meta.get("writers"))
    _actors(root, meta)
    if poster_name:
        _add(root, "thumb", poster_name, aspect="poster")
    if fanart_name:
        fanart = _add(root, "fanart")
        _add(fanart, "thumb", fanart_name)
    _fileinfo(root, probe)
    return _finish(root)


def build_tvshow_nfo(meta: dict[str, Any], poster_name: str = "", fanart_name: str = "") -> str:
    root = ET.Element("tvshow")
    _add(root, "title", meta.get("title"))
    _add(root, "originaltitle", meta.get("original_title"))
    _add(root, "sorttitle", meta.get("sort_title") or meta.get("title"))
    _rating(root, meta)
    _add(root, "plot", meta.get("overview"))
    _add(root, "outline", (meta.get("overview") or "")[:260])
    _add(root, "tagline", meta.get("tagline"))
    _add(root, "year", meta.get("year"))
    _add(root, "premiered", meta.get("release_date"))
    _add(root, "releasedate", meta.get("release_date"))
    _add(root, "status", "")
    _add(root, "playcount", 0)
    _uniqueids(root, meta)
    _add_many(root, "genre", meta.get("genres"))
    _add_many(root, "country", meta.get("countries"))
    _add_many(root, "studio", meta.get("studios"))
    _add_many(root, "director", meta.get("directors"))
    _add_many(root, "credits", meta.get("writers"))
    _actors(root, meta)
    if poster_name:
        _add(root, "thumb", poster_name, aspect="poster")
    if fanart_name:
        fanart = _add(root, "fanart")
        _add(fanart, "thumb", fanart_name)
    return _finish(root)


def build_season_nfo(meta: dict[str, Any], season: int, poster_name: str = "") -> str:
    root = ET.Element("season")
    _add(root, "title", meta.get("season_title") or meta.get("title"))
    _add(root, "seasonnumber", season)
    _rating(root, meta)
    _add(root, "plot", meta.get("overview"))
    _add(root, "outline", (meta.get("overview") or "")[:260])
    _add(root, "year", meta.get("year"))
    _add(root, "premiered", meta.get("release_date"))
    _add(root, "playcount", 0)
    _uniqueids(root, meta)
    _add_many(root, "genre", meta.get("genres"))
    if poster_name:
        _add(root, "thumb", poster_name)
        _add(root, "fanart", poster_name)
    return _finish(root)


def build_episode_nfo(
    meta: dict[str, Any],
    season: int,
    episode: int,
    episode_title: str = "",
    probe: dict[str, Any] | None = None,
) -> str:
    root = ET.Element("episodedetails")
    _add(root, "title", episode_title or f"第 {episode} 集")
    _add(root, "showtitle", meta.get("title"))
    _add(root, "season", season)
    _add(root, "episode", episode)
    _add(root, "plot", meta.get("overview"))
    _add(root, "year", meta.get("year"))
    _add(root, "premiered", meta.get("release_date"))
    _add(root, "aired", meta.get("release_date"))
    _add(root, "playcount", 0)
    _uniqueids(root, meta)
    _add_many(root, "genre", meta.get("genres"))
    _add_many(root, "director", meta.get("directors"))
    _add_many(root, "credits", meta.get("writers"))
    _actors(root, meta)
    _fileinfo(root, probe)
    return _finish(root)


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def _write(path: Path, text: str, overwrite: bool) -> tuple[Path, bool]:
    if path.exists() and not overwrite:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)
    return path, True


def write_work_nfo(
    work: dict[str, Any],
    meta: dict[str, Any],
    *,
    poster_path: Path | None = None,
    fanart_path: Path | None = None,
    overwrite: bool = False,
    probes: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root_path = Path(work["root_path"])
    files = work.get("files") or []
    probes = probes or {}
    warnings: list[str] = []
    written: list[str] = []
    episodes: dict[int, str] = {}
    main_path: Path | None = None

    if work["kind"] == "movie":
        video = Path(files[0]["path"]) if files else root_path
        nfo_dir = video.parent
        poster_name = _relative(poster_path, nfo_dir) if poster_path else ""
        fanart_name = _relative(fanart_path, nfo_dir) if fanart_path else ""
        probe = probes.get(files[0]["id"]) if files else None
        text = build_movie_nfo(meta, poster_name, fanart_name, probe)
        target = video.with_suffix(".nfo")
        main_path, was_written = _write(target, text, overwrite)
        if not was_written:
            warnings.append(f"{target.name} 已存在，已跳过")
        else:
            written.append(str(target))
    else:
        nfo_dir = root_path
        poster_name = _relative(poster_path, nfo_dir) if poster_path else ""
        fanart_name = _relative(fanart_path, nfo_dir) if fanart_path else ""
        season_number = explicit_season_number(nfo_dir.name)
        if season_number:
            target = nfo_dir / "season.nfo"
            main_path, was_written = _write(
                target, build_season_nfo(meta, season_number, poster_name), overwrite
            )
            if not was_written:
                warnings.append("season.nfo 已存在，已跳过")
            else:
                written.append(str(target))
        else:
            target = nfo_dir / "tvshow.nfo"
            main_path, was_written = _write(target, build_tvshow_nfo(meta, poster_name, fanart_name), overwrite)
            if not was_written:
                warnings.append("tvshow.nfo 已存在，已跳过")
            else:
                written.append(str(target))

        for index, file_row in enumerate(files):
            video = Path(file_row["path"])
            season = int(file_row.get("season") or 1)
            episode = int(file_row.get("episode") or index + 1)
            key = f"S{season:02d}E{episode:02d}"
            episode_title = (meta.get("episode_titles") or {}).get(key, "")
            text = build_episode_nfo(meta, season, episode, episode_title, probes.get(file_row["id"]))
            ep_path, was_written = _write(video.with_suffix(".nfo"), text, overwrite)
            episodes[int(file_row["id"])] = str(ep_path)
            if not was_written:
                warnings.append(f"{ep_path.name} 已存在，已跳过")
            else:
                written.append(str(ep_path))

    return {
        "main_path": str(main_path) if main_path else None,
        "episode_paths": episodes,
        "written": written,
        "warnings": warnings,
    }


def safe_nfo_stem(path: Path) -> str:
    return safe_stem(path.stem)
