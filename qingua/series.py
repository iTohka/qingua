from __future__ import annotations

import re
import unicodedata
from typing import Any


SEASON_SUFFIX_RE = re.compile(
    r"(?i)\s*(?:第\s*[一二三四五六七八九十\d]{1,3}\s*季|season\s*\d{1,2}|"
    r"\d{1,2}(?:st|nd|rd|th)\s+season|s\d{1,2})\s*$"
)
ROMAN_SUFFIX_RE = re.compile(r"(?i)\s+(?:II|III|IV|V|VI)\s*$")
PART_SUFFIX_RE = re.compile(r"(?i)\s*(?:part|chapter)\s*\d{1,2}\s*$")
TRAILING_NUMBER_RE = re.compile(r"^(?P<base>.+?)[\s._\-]*(?<![.\d])(?P<number>\d{1,2})(?:\s*[:：].*)?$")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def series_base(title: str) -> str:
    """Return a collection name for sequels such as 钢铁侠1/2/3."""
    text = _normalize(title)
    text = SEASON_SUFFIX_RE.sub("", text)
    text = PART_SUFFIX_RE.sub("", text)
    text = ROMAN_SUFFIX_RE.sub("", text)
    for _ in range(2):
        match = TRAILING_NUMBER_RE.match(text)
        if not match:
            break
        base = match.group("base").strip(" ._-—–:：")
        if len(base) < 2:
            break
        text = base
    return text.strip(" ._-—–:：").strip()


def series_key(title: str) -> str:
    base = series_base(title)
    key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", base.casefold())
    return key if len(key) >= 2 else ""


def series_info(works: list[dict[str, Any]]) -> tuple[dict[int, dict[str, str]], list[dict[str, Any]]]:
    """Return per-work series metadata and groups containing at least two works."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    bases: dict[str, str] = {}
    for work in works:
        base = series_base(str(work.get("title") or ""))
        key = series_key(base)
        if not key:
            continue
        buckets.setdefault(key, []).append(work)
        if key not in bases or len(base) < len(bases[key]):
            bases[key] = base

    per_work: dict[int, dict[str, str]] = {}
    groups: list[dict[str, Any]] = []
    for key, items in buckets.items():
        if len(items) < 2:
            continue
        # Different encodes of the same title are duplicates, not a series.
        if len({str(work.get("title") or "").strip().casefold() for work in items}) < 2:
            continue
        name = bases[key]
        for work in items:
            per_work[int(work["id"])] = {"series_key": key, "series_name": name}
        groups.append(
            {
                "key": key,
                "name": name,
                "count": len(items),
                "work_ids": [int(work["id"]) for work in items],
            }
        )
    groups.sort(key=lambda item: (-item["count"], item["name"].casefold()))
    return per_work, groups
