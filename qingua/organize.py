from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class OrganizeError(RuntimeError):
    pass


def _safe(value: str) -> str:
    text = INVALID.sub("_", value or "").strip(" .")
    text = re.sub(r"\s+", " ", text)
    return text[:120] or "未命名"


def _title(work: dict[str, Any], meta: dict[str, Any] | None) -> tuple[str, int | None]:
    meta = meta or {}
    title = (meta.get("title") or work.get("title") or work.get("parsed_title") or "未命名").strip()
    year = meta.get("year") or work.get("year") or work.get("parsed_year")
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    return _safe(title), year


def _sidecars_for_video(video: Path) -> list[Path]:
    result = []
    for suffix in (".nfo",):
        candidate = video.with_suffix(suffix)
        if candidate.exists():
            result.append(candidate)
    for pattern in (f"{video.stem}-poster.*", f"{video.stem}-fanart.*", f"{video.stem}-thumb.*"):
        result.extend(sorted(video.parent.glob(pattern)))
    return result


def plan_organize(work: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    base = Path(work["folder_path"])
    title, year = _title(work, meta)
    year_part = f" ({year})" if year else ""
    moves: list[dict[str, str]] = []
    seen_dest: set[str] = set()

    def add(src: Path, dest: Path, role: str) -> None:
        if not src.exists():
            return
        if src.resolve(strict=False) == dest.resolve(strict=False):
            return
        candidate = dest
        counter = 2
        while str(candidate).lower() in seen_dest or (
            candidate.exists() and candidate.resolve(strict=False) != src.resolve(strict=False)
        ):
            candidate = dest.with_name(f"{dest.stem} ({counter}){dest.suffix}")
            counter += 1
        seen_dest.add(str(candidate).lower())
        moves.append({"src": str(src), "dest": str(candidate), "role": role})

    if work["kind"] == "movie":
        folder = base / f"{title}{year_part}"
        video = Path((work.get("files") or [{}])[0].get("path") or work["root_path"])
        add(video, folder / f"{title}{year_part}{video.suffix}", "video")
        for sidecar in _sidecars_for_video(video):
            add(sidecar, folder / sidecar.name, "sidecar")
        for name in ("poster.jpg", "poster.png", "fanart.jpg", "fanart.png"):
            add(video.parent / name, folder / name, "artwork")
    else:
        folder = base / f"{title}{year_part}"
        for file_row in work.get("files") or []:
            video = Path(file_row["path"])
            season = int(file_row.get("season") or 1)
            episode = int(file_row.get("episode") or 1)
            season_folder = folder / f"Season {season:02d}"
            dest = season_folder / f"{title} - S{season:02d}E{episode:02d}{video.suffix}"
            add(video, dest, "video")
            for sidecar in _sidecars_for_video(video):
                add(sidecar, season_folder / sidecar.name, "sidecar")
        root = Path(work["root_path"])
        for name in ("tvshow.nfo", "poster.jpg", "poster.png", "fanart.jpg", "fanart.png"):
            add(root / name, folder / name, "show")
        for pattern in ("season*.jpg", "season*.png"):
            for art in sorted(root.glob(pattern)):
                add(art, folder / art.name, "show")

    return {
        "work_id": work["id"],
        "title": title,
        "year": year,
        "target_root": str(folder),
        "moves": moves,
        "move_count": len(moves),
        "dry_run": True,
    }


def execute_organize(work: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if not plan.get("moves"):
        return {"moved": [], "work_root": work["root_path"], "error": None}
    moved: list[dict[str, str]] = []
    errors: list[str] = []
    for item in plan["moves"]:
        src = Path(item["src"])
        dest = Path(item["dest"])
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise OrganizeError(f"目标已存在：{dest}")
            shutil.move(str(src), str(dest))
            moved.append({"src": str(src), "dest": str(dest), "role": item.get("role", "")})
        except Exception as exc:  # noqa: BLE001 - report per-file
            errors.append(f"{src.name}: {exc}")

    return {
        "moved": moved,
        "errors": errors,
        "work_root": plan.get("target_root"),
        "at": time.time(),
    }
