from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def find_ffprobe(configured: str = "") -> str | None:
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ffprobe")
    if found:
        return found
    for candidate in (
        Path("C:/ffmpeg/bin/ffprobe.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffprobe.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def probe_file(path: Path, ffprobe: str | None) -> dict[str, Any] | None:
    if not ffprobe:
        return None
    command = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=flags,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subtitles = [s for s in streams if s.get("codec_type") == "subtitle"]

    duration = None
    try:
        duration = float(fmt.get("duration") or (video or {}).get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None

    width = _int((video or {}).get("width"))
    height = _int((video or {}).get("height"))
    aspect = None
    if width and height:
        aspect = round(width / height, 3)

    return {
        "duration": duration,
        "video": {
            "codec": (video or {}).get("codec_name") or "",
            "width": width,
            "height": height,
            "aspect": aspect,
        }
        if video
        else None,
        "audios": [
            {
                "codec": s.get("codec_name") or "",
                "channels": _int(s.get("channels")) or 0,
                "language": s.get("tags", {}).get("language") or "",
            }
            for s in audios
        ],
        "subtitles": [
            {"language": s.get("tags", {}).get("language") or ""} for s in subtitles
        ],
    }


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
