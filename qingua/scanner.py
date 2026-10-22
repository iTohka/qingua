from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import SKIP_DIRS
from .parser import (
    ParsedFile,
    _season_from_text,
    _title_from_brackets,
    clean_title,
    explicit_season_number,
    is_video,
    parse_file,
    season_number_from_folder,
    season_title,
)
from .series import series_base

SAMPLE_RE = re.compile(
    r"(?i)(?:^|[\s._\-])(?:sample|trailer|teaser|featurette|extras?|behind[ ._-]?the[ ._-]?scenes|"
    r"预告|花絮|片花|特辑|彩蛋)(?:$|[\s._\-])"
)
PART_RE = re.compile(r"(?i)\.(?:part|cd|disc)[ ._-]?\d{1,2}\.(?:mkv|mp4|avi|mov|m4v|ts)$")
BARE_EPISODE_RE = re.compile(r"^\s*(\d{1,3})\s*$")
TRAILING_EPISODE_RE = re.compile(
    r"(?i)^(?P<title>.+?)[\s._-]+(?:EP?|#)?\s*(?P<episode>\d{1,3})(?:v\d)?"
    r"(?:\s*(?:END|FIN|完|完结))?$"
)


@dataclass
class ScanGroup:
    kind: str
    root_path: Path
    title: str
    year: int | None
    season_count: int
    episode_count: int
    confidence: int
    files: list[ParsedFile] = field(default_factory=list)


def _skip_dir(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in SKIP_DIRS
        or lowered.startswith(".")
        or lowered.startswith("~")
        or bool(EXTRA_DIR_RE.match(name.strip()))
    )


def _skip_file(path: Path, min_bytes: int) -> bool:
    name = path.name
    if name.startswith(".") or name.startswith("~$"):
        return True
    if SAMPLE_RE.search(name):
        return True
    if PART_RE.search(name):
        return True
    if EXTRA_FILE_RE.search(path.stem):
        return True
    if RANGE_FILE_RE.search(path.stem):
        return True
    try:
        if path.stat().st_size < min_bytes:
            return True
    except OSError:
        return True
    return False


NUMERIC_TOKEN_RE = re.compile(r"(?<![\dA-Za-z])(\d{1,3})(?:v\d)?(?![\dA-Za-z])")
RESOLUTION_NUMBERS = {360, 480, 540, 576, 720, 1080, 1440, 2160, 4320}
SP_FOLDER_RE = re.compile(
    r"(?i)^(?:sp|sps|specials?|extras?|menu|menus|ncop|nced|pv|cm|oad|ova|"
    r"剧场版|映像特典|特典|预告|花絮)$"
)
PART_PREFIX_RE = re.compile(r"(?i)(?:^|[\s._\-])(?:part|cd|disc|vol(?:ume)?)[\s._\-]*$")
CONTAINER_NAME_RE = re.compile(
    r"(?i)^(?:番剧|动漫|动画|电影|影视|资源|下载|媒体库|library|anime|animation|cartoons?|tv|shows|series|"
    r"media|downloads?|videos|movies|numeric-check)$"
)
EXTRA_DIR_RE = re.compile(
    r"(?i)^(?:sp|sps|specials?|extras?|menus?|ncop|nced|pv|cm|bonus|scans?|oads?|"
    r"特典|映像特典|预告|花絮)$"
)
EXTRA_FILE_RE = re.compile(
    r"(?i)(?:^|[\s._\[\(])(?:NCED|NCOP|PV|CM|Menu|Preview|Trailer|OAD|OVA|SP|Special|"
    r"Extra|Bonus|Chimichara|Commentary)\d*(?:[^A-Za-z]|$)"
)
RANGE_FILE_RE = re.compile(r"[\[\(]\s*\d{1,3}\s*[-~]\s*\d{1,3}\s*(?:[\]\)]|[\(\[]|$)")


def _is_container_folder(path: Path) -> bool:
    return bool(CONTAINER_NAME_RE.match(clean_title(path.name).strip()))


def _is_extra_folder(path: Path) -> bool:
    return any(SP_FOLDER_RE.match(part.strip()) for part in path.parts[-3:] if part)


def _numeric_variants(stem: str, extra: bool) -> list[tuple[str, int, int, int]]:
    """Return `(signature, number, start, end)` for plausible episode numbers."""
    variants: list[tuple[str, int, int, int]] = []
    for match in NUMERIC_TOKEN_RE.finditer(stem):
        number = int(match.group(1))
        if number < 1 or number > 999:
            continue
        if 1900 <= number <= 2099:
            continue
        if number in RESOLUTION_NUMBERS:
            continue
        prefix = stem[: match.start()]
        if PART_PREFIX_RE.search(prefix):
            continue
        if re.search(r"(?i)(?:^|[\s._\-\[\(])menu(?:$|[\s._\-\[\]\(\)])", stem):
            continue
        # In a normal folder a bare number in brackets is a strong episode
        # signal; in an SPs/EXTRA folder it is usually a menu or credit clip.
        if extra and not re.search(r"(?:^|[\s._\-])\d{1,3}(?:v\d)?(?:$|[\s._\-])", stem):
            continue
        signature = f"{stem[:match.start()]}{{ep}}{stem[match.end():]}"
        variants.append((signature, number, match.start(), match.end()))
    return variants


def _promote_numeric_sequences(parsed: list[ParsedFile]) -> list[ParsedFile]:
    """Group sibling movies whose names differ only by one number.

    This is the general fallback behind explicit patterns: `Title 01`,
    `[Group] Title [01]`, `Title - 01 [tags]`, etc. It deliberately ignores
    years, resolutions, codecs and Part/CD/Disc numbers.
    """
    by_dir: dict[Path, list[ParsedFile]] = {}
    for item in parsed:
        if item.kind == "movie" and not _is_extra_folder(item.path.parent):
            by_dir.setdefault(item.path.parent, []).append(item)

    for directory, items in by_dir.items():
        groups: dict[str, list[tuple[ParsedFile, int, int, int]]] = {}
        for item in items:
            for variant in _numeric_variants(item.path.stem, extra=False):
                groups.setdefault(variant[0], []).append((item, *variant[1:]))
        candidates = []
        for signature, rows in groups.items():
            if len(rows) < 2:
                continue
            numbers = {row[1] for row in rows}
            if len(numbers) < 2:
                continue
            bracket_bonus = 8 if re.search(r"[\[【]\{ep\}(\]】)", signature) else 0
            dash_bonus = 6 if re.search(r"[\s._-]-[\s._-]*\{ep\}", signature) else 0
            zero_bonus = 4 if any(
                re.search(r"(?:^|[^\d])0\d+(?:[^\d]|$)", item.path.stem) for item, *_ in rows
            ) else 0
            coverage = len(rows) / max(1, len(items))
            candidates.append((len(rows), bracket_bonus + dash_bonus + zero_bonus, coverage, signature, rows))
        if not candidates:
            continue
        _, _, _, signature, rows = max(candidates, key=lambda row: (row[0], row[1], row[2]))
        first_item, first_number, start, _ = rows[0]
        prefix = first_item.path.stem[:start]
        title = clean_title(prefix) or _title_from_brackets(prefix)
        if not title:
            title = clean_title(directory.name)
        if not title:
            continue
        season = _season_from_text(directory.name) or season_number_from_folder(directory.name) or 1
        root = directory.parent if season_number_from_folder(directory.name) is not None else directory
        for item, number, *_ in rows:
            item.kind = "tv"
            item.title = title
            item.season = season
            item.episode = number
            item.root_path = root
            item.confidence = 80
            item.tags = ["numeric_sequence"]
    return parsed


def _promote_bare_numbers(parsed: list[ParsedFile]) -> list[ParsedFile]:
    """Treat two or more `01.mkv`, `02.mkv` siblings as a TV season."""
    by_dir: dict[Path, list[ParsedFile]] = {}
    for item in parsed:
        if item.kind == "movie" and BARE_EPISODE_RE.match(item.path.stem):
            by_dir.setdefault(item.path.parent, []).append(item)

    result: list[ParsedFile] = []
    promoted_paths: set[Path] = set()
    for directory, items in by_dir.items():
        if len(items) < 2:
            continue
        season = season_number_from_folder(directory.name)
        root = directory.parent if season is not None else directory
        title = clean_title(root.name) or clean_title(directory.name)
        season = season if season is not None else 1
        for item in items:
            match = BARE_EPISODE_RE.match(item.path.stem)
            if not match:
                continue
            item.kind = "tv"
            item.title = title
            item.season = season
            item.episode = int(match.group(1))
            item.root_path = root
            item.confidence = 58
            item.tags = ["bare_sequence"]
            promoted_paths.add(item.path)

    for item in parsed:
        if item.path in promoted_paths:
            result.append(item)
        else:
            result.append(item)
    return result


def _episode_from_name(stem: str) -> tuple[str, int] | None:
    """Recognize `Title - 01`, `Title 01`, `Title EP01` and similar.

    This is deliberately conservative and only used when several sibling files
    form a sequence; it must not turn `Movie 2020` into episode 2020.
    """
    cleaned = clean_title(stem)
    match = TRAILING_EPISODE_RE.match(cleaned)
    if not match:
        return None
    episode = int(match.group("episode"))
    if episode < 1 or episode > 999:
        return None
    title = clean_title(match.group("title"))
    if not title:
        return None
    # A bare four-digit year can never be an episode, but this also protects
    # against titles such as "Cyberpunk 2077".
    if re.fullmatch(r"(?:19|20)\d{2}", match.group("episode")):
        return None
    return title, episode


def _promote_sequential_episodes(parsed: list[ParsedFile]) -> list[ParsedFile]:
    """Group `Title - 01`, `Title - 02`, ... siblings into one TV show."""
    by_dir: dict[Path, list[ParsedFile]] = {}
    for item in parsed:
        if item.kind == "movie":
            by_dir.setdefault(item.path.parent, []).append(item)

    promoted_any = False
    for directory, items in by_dir.items():
        by_title: dict[str, list[tuple[ParsedFile, str, int]]] = {}
        for item in items:
            guess = _episode_from_name(item.path.stem)
            if not guess:
                continue
            title, episode = guess
            if PART_PREFIX_RE.search(title):
                continue
            by_title.setdefault(_title_group_key(title), []).append((item, title, episode))

        for guesses in by_title.values():
            if len(guesses) < 2:
                continue
            if len({episode for _, _, episode in guesses}) < 2:
                continue
            title = guesses[0][1]
            season = _season_from_text(directory.name) or season_number_from_folder(directory.name) or 1
            root = directory.parent if season_number_from_folder(directory.name) is not None else directory
            for item, _, episode in guesses:
                item.kind = "tv"
                item.title = title
                item.season = season
                item.episode = episode
                item.root_path = root
                item.confidence = 82
                item.tags = ["sequential_filename"]
                promoted_any = True

    if not promoted_any:
        return parsed
    return parsed


def _group_title_key(item: ParsedFile) -> str:
    return _title_group_key(item.title) or _title_group_key(item.path.parent.name)


def _title_group_key(value: str) -> str:
    text = clean_title(value).casefold()
    text = re.sub(r"(?i)(?:第\s*[一二三四五六七八九十\d]{1,3}\s*季|season\s*\d{1,2}|s\d{1,2})\s*$", "", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _is_grouping_folder(name: str) -> bool:
    return bool(
        re.fullmatch(r"(?:19|20)\d{2}", name.strip())
        or CONTAINER_NAME_RE.match(clean_title(name).strip())
        or re.fullmatch(r"(?i)movies|tv|shows|series|anime|animation|番剧|动漫|动画|影视|资源", name.strip())
    )


def _is_series_label(name: str) -> bool:
    return bool(
        season_number_from_folder(name) is not None
        or _season_from_text(name) is not None
        or series_base(name) != name
        or re.search(r"(?i)旧|新|tv|剧场版|movie|ova|oad|series|系列|篇|季|part|collection|合集|特别|final", name)
    )


def _apply_folder_context(items: list[ParsedFile], root: Path) -> None:
    """Resolve the show folder and split real season subfolders.

    `Show/S1`, `Show/S2` -> two season works.
    `Show/旧TV+剧场版` -> one show work named `Show` (the subfolder is a label).
    """
    if not items:
        return
    root = Path(root)
    rels = [item.path.relative_to(root) for item in items]
    has_direct = any(len(rel.parts) == 1 for rel in rels)
    child_names = {rel.parts[0] for rel in rels if len(rel.parts) > 1}

    root_is_show = False
    drive_root = re.fullmatch(r"(?i)[a-z]:", root.name.strip()) is not None
    if not drive_root and not _is_container_folder(root) and not _is_grouping_folder(root.name):
        season_children = {
            name
            for name in child_names
            if season_number_from_folder(name) is not None or _season_from_text(name) is not None
        }
        if not has_direct:
            if child_names and all(_is_grouping_folder(name) for name in child_names):
                root_is_show = False
            elif len(child_names) == 1:
                root_is_show = _is_series_label(next(iter(child_names)))
            elif season_children:
                root_is_show = True
            elif len(child_names) <= 2 and all(_is_series_label(name) for name in child_names):
                root_is_show = True
        elif not child_names:
            root_is_show = True

    def show_root_for(item: ParsedFile, rel: Path) -> Path:
        dirs = list(rel.parts[:-1])
        if root_is_show:
            return root
        index = 0
        while index < len(dirs) and _is_grouping_folder(dirs[index]):
            index += 1
        if index < len(dirs):
            return root.joinpath(*dirs[: index + 1])
        return item.path.parent

    show_roots: dict[Path, Path] = {}
    subfolders: dict[Path, set[str]] = {}
    for item, rel in zip(items, rels):
        if item.kind != "tv":
            continue
        show_root = show_root_for(item, rel)
        show_roots[item.path] = show_root
        dirs = list(rel.parts[:-1])
        relative = rel.parts[len(show_root.relative_to(root).parts) : -1] if show_root != root else dirs
        if relative:
            subfolders.setdefault(show_root, set()).add(relative[0])

    # If a show has multiple real season subfolders, every one of them becomes
    # a season work; the parent folder itself is not a work.  Example:
    # Shingeki/Season 2, Season 3, The Final Season, and the unlabeled S1 folder.
    season_assignments: dict[tuple[Path, str], int] = {}
    for show_root, subs in subfolders.items():
        if len(subs) < 2:
            continue
        explicit = {sub: explicit_season_number(sub) for sub in subs}
        if not any(value is not None for value in explicit.values()):
            continue
        used = {value for value in explicit.values() if value is not None}
        base_name = clean_title(show_root.name).casefold()
        unlabeled = sorted(
            (sub for sub in subs if explicit[sub] is None),
            key=lambda sub: (clean_title(sub).casefold() != base_name, sub.casefold()),
        )
        next_number = 1
        for sub in unlabeled:
            while next_number in used:
                next_number += 1
            season_assignments[(show_root, sub)] = next_number
            used.add(next_number)
        for sub, number in explicit.items():
            if number is not None:
                season_assignments[(show_root, sub)] = number

    for item, rel in zip(items, rels):
        if item.kind != "tv":
            continue
        show_root = show_roots[item.path]
        dirs = list(rel.parts[:-1])
        relative = rel.parts[len(show_root.relative_to(root).parts) : -1] if show_root != root else dirs
        first_sub = relative[0] if relative else ""
        assigned = season_assignments.get((show_root, first_sub))
        if assigned is not None:
            show_title = clean_title(show_root.name) or item.title
            item.root_path = show_root / first_sub
            item.season = assigned
            item.title = season_title(show_title, assigned)
            item.confidence = max(82, item.confidence)
            item.tags = [*item.tags, "season_folder"]
            continue

        item.root_path = show_root
        if not (show_root == item.path.parent and _is_container_folder(show_root)):
            title = clean_title(show_root.name)
            if title:
                item.title = title

        for folder_name in relative:
            season = season_number_from_folder(folder_name) or _season_from_text(folder_name)
            if season is not None:
                item.season = season
                break


def scan_tree(root: Path, min_file_mb: float = 0) -> list[ScanGroup]:
    root = Path(root)
    min_bytes = max(0, int(min_file_mb * 1024 * 1024))
    parsed: list[ParsedFile] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        for filename in filenames:
            path = Path(dirpath) / filename
            if not is_video(path) or _skip_file(path, min_bytes):
                continue
            if path.is_symlink():
                continue
            parsed.append(parse_file(path))

    parsed = _promote_numeric_sequences(parsed)
    parsed = _promote_bare_numbers(parsed)
    parsed = _promote_sequential_episodes(parsed)
    _apply_folder_context(parsed, root)

    grouped: dict[tuple[str, str, str], list[ParsedFile]] = {}
    for item in parsed:
        root_text = str(item.root_path.resolve(strict=False))
        if item.kind == "movie":
            # Movies stay one-per-file; only TV episodes are clustered by title.
            title_key = str(item.path.resolve(strict=False))
        elif _is_container_folder(item.root_path):
            title_key = _group_title_key(item)
        else:
            # A show folder is normally authoritative even if different release
            # groups use different romanized/Chinese titles for the same show.
            title_key = ""
        grouped.setdefault((item.kind, root_text, title_key), []).append(item)

    groups: list[ScanGroup] = []
    for (kind, root_text, _title_key), items in grouped.items():
        items.sort(key=lambda f: (f.season or 0, f.episode or 0, f.path.name.lower()))
        if kind == "movie":
            item = items[0]
            groups.append(
                ScanGroup(
                    kind="movie",
                    root_path=Path(root_text),
                    title=item.title,
                    year=item.year,
                    season_count=1,
                    episode_count=1,
                    confidence=item.confidence,
                    files=[item],
                )
            )
            continue

        # TV: prefer a filename-derived title, fall back to the show folder.
        candidates = [i for i in items if i.title and not BARE_EPISODE_RE.match(i.path.stem)]
        candidates.sort(key=lambda i: i.confidence, reverse=True)
        title = candidates[0].title if candidates else ""
        if not title:
            title = clean_title(Path(root_text).name)
        if not title:
            title = clean_title(items[0].path.parent.name)

        years = [i.year for i in items if i.year]
        year = years[0] if years else None
        seasons = {i.season for i in items if i.season is not None}
        episodes = {(i.season, i.episode) for i in items if i.episode is not None}
        groups.append(
            ScanGroup(
                kind="tv",
                root_path=Path(root_text),
                title=title,
                year=year,
                season_count=max(1, len(seasons)),
                episode_count=max(len(episodes), len(items)),
                confidence=max(i.confidence for i in items),
                files=items,
            )
        )

    groups.sort(key=lambda g: (g.title.lower(), g.year or 0))
    return groups
