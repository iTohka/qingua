from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


def title_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[\s\-_.·:：,，;；!！?？'\"“”‘’()（）\[\]【】]", "", text)
    text = re.sub(r"(?:第[一二三四五六七八九十\d]+季|season\d+)", "", text)
    return text.strip()


CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def season_of_title(value: str) -> int | None:
    text = unicodedata.normalize("NFKC", value or "")
    match = re.search(r"第\s*([一二三四五六七八九十\d]{1,3})\s*季", text)
    if not match:
        match = re.search(r"(?i)\bseason\s*(\d{1,2})\b", text)
    if not match:
        match = re.search(r"(?i)(?:^|[\s:：_-])S(\d{1,2})\s*$", text)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        return CN_NUMBERS.get(left, 0) * 10 + CN_NUMBERS.get(right, 0)
    return CN_NUMBERS.get(raw)


def _similarity(a: str, b: str) -> float:
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if ka.startswith(kb) or kb.startswith(ka):
        return 0.92
    if ka in kb or kb in ka:
        return 0.8
    ratio = SequenceMatcher(None, ka, kb).ratio()
    # Token overlap helps with Chinese/English mixed titles.
    tokens_a = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", ka))
    tokens_b = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", kb))
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b) / max(1, min(len(tokens_a), len(tokens_b)))
        ratio = max(ratio, overlap * 0.9)
    return ratio


def score_candidate(
    candidate: dict[str, Any],
    title: str,
    year: int | None,
    kind: str | None,
    season: int | None = None,
    episode_count: int | None = None,
) -> int:
    names = [candidate.get("title") or "", candidate.get("original_title") or ""]
    names.extend(candidate.get("aliases") or [])
    names = [n for n in names if n]
    best_sim = max((_similarity(title, n) for n in names), default=0.0)

    if best_sim >= 0.999:
        score = 72.0
    elif best_sim >= 0.9:
        score = 62.0
    elif best_sim >= 0.75:
        score = 52.0
    else:
        score = best_sim * 55.0

    candidate_year = candidate.get("year")
    if year and candidate_year:
        delta = abs(int(year) - int(candidate_year))
        if delta == 0:
            score += 20
        elif delta == 1:
            score += 8
        else:
            score -= 15
    elif year and not candidate_year:
        score -= 2

    candidate_kind = candidate.get("kind")
    if kind and candidate_kind:
        if kind == candidate_kind:
            score += 10
        else:
            score -= 25

    if season:
        candidate_season = next(
            (parsed for parsed in (season_of_title(name) for name in names) if parsed is not None),
            None,
        )
        if candidate_season is None and (candidate.get("kind") or kind) == "tv":
            # 番库 sometimes titles a sequel as "Show 2" rather than "Show 第二季".
            for name in names:
                match = re.search(r"\s([2-9]|1\d)\s*$", name)
                if match:
                    candidate_season = int(match.group(1))
                    break
        if candidate_season is not None:
            if candidate_season == season:
                score += 14
            else:
                score -= 35

    if episode_count:
        candidate_count = candidate.get("episode_count")
        try:
            candidate_count = int(candidate_count) if candidate_count else 0
        except (TypeError, ValueError):
            candidate_count = 0
        if candidate_count:
            difference = abs(candidate_count - episode_count)
            if difference == 0:
                score += 12
            elif difference <= 2:
                score += 4
            else:
                score -= 25

    rating = candidate.get("rating")
    if rating:
        score += min(2.0, float(rating) / 5.0)
    if candidate.get("episode_count") and kind == "tv":
        score += 1

    return max(0, min(100, int(round(score))))


def rank_candidates(
    candidates: list[dict[str, Any]],
    title: str,
    year: int | None,
    kind: str | None,
    season: int | None = None,
    episode_count: int | None = None,
) -> list[dict[str, Any]]:
    for candidate in candidates:
        candidate["score"] = score_candidate(
            candidate, title, year, kind, season=season, episode_count=episode_count
        )
    return sorted(
        candidates,
        key=lambda c: (c.get("score", 0), c.get("votes") or 0, c.get("rating") or 0),
        reverse=True,
    )


def pick_best(candidates: list[dict[str, Any]], threshold: int = 82) -> tuple[dict | None, bool]:
    if not candidates:
        return None, False
    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    confident = int(best.get("score", 0)) >= threshold
    if second is not None and int(best.get("score", 0)) - int(second.get("score", 0)) < 4:
        confident = False
    return best, confident
