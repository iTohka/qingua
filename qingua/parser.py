from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .config import VIDEO_EXTENSIONS

# Tokens that almost never belong to a title. Once one is found, everything from
# that point on is release noise (resolution, codec, audio, subtitles, group...).
QUALITY_TOKENS = (
    r"2160p|1080p|1080i|720p|576p|480p|4k|8k|uhd|hdr10\+?|hdr|dolby[ ._-]?vision|dv",
    r"blu[ ._-]?ray|bdrip|brrip|remux|web[ ._-]?dl|webrip|hdtv|dvdrip|dvdscr|"
    r"hdrip|camrip|telesync|tc|ts|hdts|hdcam|vhsrip|bd25|bd50",
    r"x264|x265|h[ ._-]?264|h[ ._-]?265|hevc|avc|av1|vp9|xvid|divx",
    r"aac|ac3|eac3|dts(?:[ ._-]?hd)?|truehd|atmos|ddp?[ ._-]?[257][ ._-]?1|"
    r"flac|opus|mp3|commentary",
    r"10[ ._-]?bit|8[ ._-]?bit|60fps|50fps|hfr",
    r"chs|cht|chinese|english|eng|jpn|japanese|kor|korean|"
    r"中字|中英|简中|繁中|简繁|字幕|国语|粤语|双语|内封|内嵌|外挂|"
    r"国配|台配|港配|无删减|未删减|完整版|高清|超清|蓝光|原盘",
)

QUALITY_RE = re.compile(r"(?i)(?:^|[\s._\-\[\(（【])(?:" + "|".join(QUALITY_TOKENS) + r")(?:$|[\s._\-\]）\)】])")
URL_RE = re.compile(r"(?i)(?:https?://\S+|www\.\S+|\S+\.(?:com|net|org|cn|cc|tv|me|xyz|top|vip|club|info|io)\b)")
BRACKET_RE = re.compile(r"[\[【][^\]】]{0,60}[\]】]")
PAREN_TAG_RE = re.compile(
    r"[\(（]\s*(?:"
    r"2160p|1080p|720p|4k|uhd|hdr|bluray|web[ ._-]?dl|国语|粤语|中字|双语|"
    r"简繁|内封|内嵌|完整版|未删减|高清|超清|官方|特效|字幕"
    r")\s*[\)）]",
    re.I,
)

TV_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(?:^|[\s._\-\[\(])(?:S(\d{1,2})[ ._-]?E(\d{1,3}))(?:$|[\s._\-\]\)])"), "sxxexx"),
    (re.compile(r"(?i)(?:^|[\s._\-\[\(])(\d{1,2})[xX](\d{1,3})(?:$|[\s._\-\]\)])"), "1x02"),
    (re.compile(r"第\s*(\d{1,2})\s*季[\s._\-]*第\s*(\d{1,3})\s*[集话話]"), "cn_season_ep"),
    (re.compile(r"\[(\d{1,3})(?:v\d)?\]"), "bracket_ep"),
    (re.compile(r"(?i)(?:^|[\s._])-[\s._]?(\d{1,3})(?:v\d)?(?:$|[\s._\-])"), "dash_ep"),
    (re.compile(r"(?i)(?:^|[\s._\-])EP[ ._-]?(\d{1,3})(?:$|[\s._\-])"), "ep"),
    (re.compile(r"(?i)(?:^|[\s._\-])E[ ._-]?(\d{1,3})(?:$|[\s._\-])"), "e"),
    (re.compile(r"第\s*(\d{1,3})\s*[集话話]"), "cn_ep"),
    (re.compile(r"(?<!\d)(\d{1,3})\s*[集话話]"), "bare_cn_ep"),
    (re.compile(r"(?i)(?:^|[\s._\-])(?:EP|E)?[ ._-]?(\d{1,3})\s*(?:v\d)?[\s._\-](?:1080p|720p|2160p|4k|web|hdtv|bluray)"), "ep_quality"),
]

SEASON_DIR_RE = re.compile(r"(?i)^(?:season|s)[ ._-]*(\d{1,2})$|^第\s*([一二三四五六七八九十\d]{1,3})\s*季$|^(\d{1,2})$")
EXPLICIT_SEASON_RE = re.compile(
    r"(?i)(?:^|[\s._\-\[\(])(?:S(\d{1,2})|Season\s*(\d{1,2})|第\s*([一二三四五六七八九十\d]{1,3})\s*季)(?:$|[\s._\-\]\)])"
)
CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
EPISODE_SUFFIX_RE = re.compile(r"(?i)(?:^|[\s._\-])(?:S\d{1,2}E\d{1,3}|E\d{1,3}|EP\d{1,3}|第\s*\d{1,3}\s*[集话話]).*$")
BRACKET_CONTENT_RE = re.compile(r"[\[【]([^\]】]{1,100})[\]】]")
RESOLUTION_NUMBERS = {360, 480, 540, 576, 720, 1080, 1440, 2160, 4320}
GROUP_HINTS = re.compile(
    r"(?i)字幕组|字幕社|sub(?:s)?\b|raws?\b|vcb|loli\s*house|nekomoe|kissaten|"
    r"airota|sakurato|comicat|kiss\s*sub|beansub|dmg|emd|fsh|ani\b|ncel|sweetsub|"
    r"lilith|moozzi|reinforce|uha|jsum|beatrice|ohys|erai|horriblesubs"
)


@dataclass
class ParsedFile:
    path: Path
    kind: str
    title: str
    year: int | None
    season: int | None
    episode: int | None
    root_path: Path
    confidence: int
    tags: list[str] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return self.path.name


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def season_number_from_folder(name: str) -> int | None:
    match = SEASON_DIR_RE.match(name.strip())
    if not match:
        return None
    for group in match.groups():
        if not group:
            continue
        if group.isdigit():
            return int(group)
        if group == "十":
            return 10
        if group.startswith("十"):
            return 10 + CN_NUMBERS.get(group[1:], 0)
        if group.endswith("十"):
            return CN_NUMBERS.get(group[:-1], 0) * 10
        if "十" in group:
            left, _, right = group.partition("十")
            return CN_NUMBERS.get(left, 0) * 10 + CN_NUMBERS.get(right, 0)
        return CN_NUMBERS.get(group)
    return None


def season_title(title: str, season: int) -> str:
    base = (title or "").strip()
    suffix = f"第{season}季"
    if base and re.search(r"[\u4e00-\u9fff]$", base):
        return f"{base}{suffix}"
    return f"{base} {suffix}".strip()


def explicit_season_number(name: str) -> int | None:
    """Recognize season labels anywhere in a folder name, e.g. `Show S1`.

    This is deliberately narrower than `season_number_from_folder`: plain
    `S1`/`Season 1`/`第1季` only, not arbitrary trailing numbers.
    """
    match = EXPLICIT_SEASON_RE.search(name.strip())
    if not match:
        return _season_from_text(name)
    raw = next((group for group in match.groups() if group), "")
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        return CN_NUMBERS.get(left, 0) * 10 + CN_NUMBERS.get(right, 0)
    return CN_NUMBERS.get(raw)


def extract_year(text: str) -> int | None:
    years = [int(m.group(1)) for m in YEAR_RE.finditer(text)]
    if not years:
        return None
    # A release year is normally the last plausible year before quality tags.
    return years[-1]


def _strip_quality(text: str) -> tuple[str, list[str]]:
    tags: list[str] = []
    match = QUALITY_RE.search(text)
    if match:
        start = match.start()
        # If the quality token was preceded by a separator, cut the separator too.
        while start > 0 and text[start - 1] in " ._-[]()（）【】":
            start -= 1
        tags = [t for t in re.split(r"[\s._\-\[\]()（）【】]+", text[start:]) if t]
        text = text[:start]
    return text.strip(" ._-[]()（）【】"), tags


def clean_title(value: str) -> str:
    if not value:
        return ""
    text = normalize_text(value)
    text = URL_RE.sub(" ", text)
    text = BRACKET_RE.sub(" ", text)
    text = PAREN_TAG_RE.sub(" ", text)
    text, _ = _strip_quality(text)
    text = text.replace("_", " ").replace(".", " ")
    text = re.sub(r"[\[\]【】()（）{}<>]", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ._-—–·:：,，;；!！?？'\"")
    text = re.sub(r"\s*[-–—]\s*$", "", text)
    return text.strip()


def _title_before_match(raw: str, start: int) -> str:
    head = raw[:start]
    head = re.sub(r"(?i)[\s._\-]*\b(?:S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3})\b.*$", "", head)
    head = re.sub(r"第\s*\d{1,3}\s*[集话話].*$", "", head)
    title = clean_title(head)
    if title:
        return title
    return _title_from_brackets(head)


def _title_from_brackets(head: str) -> str:
    """Recover a title when every useful part is inside brackets.

    Example: `[Airota][Majo no Tabitabi][01][BDRip ...]` -> `Majo no Tabitabi`.
    Group names are excluded by a small vocabulary, and the candidate with the
    most words wins (titles usually have more words than release-group tags).
    """
    candidates: list[str] = []
    for match in BRACKET_CONTENT_RE.finditer(head):
        value = clean_title(match.group(1))
        if not value or value.isdigit():
            continue
        if value.isupper() and len(value) <= 4:
            continue
        if GROUP_HINTS.search(value) or "&" in value:
            continue
        if QUALITY_RE.search(value):
            continue
        candidates.append(value)
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (item.count(" "), len(item)))


def _season_from_text(text: str) -> int | None:
    match = re.search(r"第\s*([一二三四五六七八九十\d]{1,3})\s*季", text)
    if match:
        raw = match.group(1)
        if raw.isdigit():
            return int(raw)
        if raw == "十":
            return 10
        if "十" in raw:
            left, _, right = raw.partition("十")
            return CN_NUMBERS.get(left, 0) * 10 + CN_NUMBERS.get(right, 0)
        return CN_NUMBERS.get(raw)
    match = re.search(r"(?i)\bseason\s*(\d{1,2})\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(?i)\b(II|III|IV|V|VI)\b", text)
    if match:
        return {"II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}[match.group(1).upper()]
    match = re.search(r"(?i)(?:^|[\s._\-])S(\d{1,2})(?:$|[\s._\-])", text)
    return int(match.group(1)) if match else None


def _match_tv(raw: str) -> tuple[str, int | None, int | None, int, str] | None:
    for pattern, kind in TV_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        groups = [g for g in match.groups() if g is not None]
        if kind in ("sxxexx", "1x02", "cn_season_ep"):
            season, episode = int(groups[0]), int(groups[1])
        elif kind in ("ep", "e", "cn_ep", "bare_cn_ep", "ep_quality", "dash_ep"):
            season, episode = None, int(groups[0])
        elif kind == "bracket_ep":
            episode = int(groups[0])
            if episode in RESOLUTION_NUMBERS:
                continue
            season = None
        else:
            continue
        title = _title_before_match(raw, match.start())
        confidence = {
            "sxxexx": 95,
            "1x02": 90,
            "cn_season_ep": 92,
            "bracket_ep": 88,
            "dash_ep": 84,
            "ep": 78,
            "e": 76,
            "cn_ep": 82,
            "bare_cn_ep": 74,
            "ep_quality": 68,
        }.get(kind, 60)
        return title, season, episode, confidence, kind
    return None


def _root_for_tv(path: Path) -> Path:
    parent = path.parent
    if season_number_from_folder(parent.name) is not None:
        return parent.parent
    return parent


def _root_title(path: Path) -> str:
    root = _root_for_tv(path)
    if root.name and root.parent != root:
        return clean_title(root.name)
    return ""


def parse_file(path: Path) -> ParsedFile:
    path = Path(path)
    raw = normalize_text(path.stem)
    tv = _match_tv(raw)

    # A file named just "01.mkv" is only treated as TV when its folder says so,
    # or when the caller (scanner) detects a sibling sequence.
    if tv is None:
        bare = re.fullmatch(r"\s*(\d{1,3})\s*", raw)
        if bare and season_number_from_folder(path.parent.name) is not None:
            tv = ("", None, int(bare.group(1)), 55, "bare_number")

    if tv is not None:
        title, season, episode, confidence, kind = tv
        root = _root_for_tv(path)
        folder_season = season_number_from_folder(path.parent.name) or _season_from_text(path.parent.name)
        if season is None:
            season = _season_from_text(raw) or folder_season or 1
        if not title:
            title = clean_title(root.name)
        if not title:
            title = clean_title(path.parent.name)
        year = extract_year(raw) or extract_year(root.name) or extract_year(path.parent.name)
        return ParsedFile(
            path=path,
            kind="tv",
            title=title,
            year=year,
            season=season,
            episode=episode,
            root_path=root,
            confidence=confidence,
            tags=[kind],
        )

    year = extract_year(raw)
    title = raw
    if year is not None:
        # Cut at the last year occurrence; keep a leading title that may itself be a year.
        matches = list(YEAR_RE.finditer(title))
        if matches:
            title = title[: matches[-1].start()]
    title = clean_title(title)
    if not title:
        title = clean_title(path.parent.name)
    return ParsedFile(
        path=path,
        kind="movie",
        title=title,
        year=year,
        season=None,
        episode=None,
        root_path=path,
        confidence=88 if year else 66,
        tags=[],
    )


def guess_from_folder(path: Path) -> tuple[str, int | None]:
    name = clean_title(path.name)
    return name, extract_year(path.name)
