from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from . import __version__
from .artwork import ArtworkError, image_content_type, save_image
from .config import data_dir
from .db import Database
from .matching import pick_best, rank_candidates
from .nfo import build_movie_nfo, write_work_nfo
from .organize import execute_organize, plan_organize
from .parser import explicit_season_number, season_title
from .probe import find_ffprobe, probe_file
from .providers import ProviderBundle, ProviderError
from .scanner import scan_tree
from .series import series_info


class AppError(RuntimeError):
    pass


class App:
    def __init__(self, db: Database | None = None, data_root: str | Path | None = None):
        self.data_root = Path(data_root) if data_root else data_dir()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(self.data_root / "qingua.db")
        self._jobs: dict[str, threading.Thread] = {}
        self._job_lock = threading.Lock()

    # ------------------------------------------------------------------ folders
    def add_folder(self, path: str) -> dict[str, Any]:
        raw = str(path or "").strip().strip('"')
        if not raw:
            raise AppError("请输入文件夹路径")
        folder = Path(raw).expanduser()
        try:
            folder = folder.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AppError(f"文件夹不存在：{raw}") from exc
        if not folder.is_dir():
            raise AppError(f"不是文件夹：{folder}")
        row = self.db.add_folder(str(folder))
        return row

    def remove_folder(self, folder_id: int) -> None:
        self.db.remove_folder(folder_id)

    def scan(self, folder_id: int | None = None) -> dict[str, Any]:
        folders = self.db.folders()
        if folder_id is not None:
            folders = [f for f in folders if f["id"] == folder_id]
        if not folders:
            raise AppError("还没有添加媒体文件夹")
        min_mb = float(self.db.settings().get("min_file_mb", 0) or 0)
        total_groups = 0
        total_files = 0
        errors: list[str] = []
        for folder in folders:
            root = Path(folder["path"])
            if not root.is_dir():
                errors.append(f"目录不存在，已跳过：{root}")
                continue
            try:
                groups = scan_tree(root, min_mb)
            except OSError as exc:
                errors.append(f"{root.name}: {exc}")
                continue
            seen_paths: set[str] = set()
            for group in groups:
                total_groups += 1
                files = []
                for item in group.files:
                    try:
                        stat = item.path.stat()
                        size, mtime = stat.st_size, stat.st_mtime
                    except OSError:
                        size, mtime = 0, 0
                    path_text = str(item.path)
                    seen_paths.add(path_text)
                    files.append(
                        {
                            "path": path_text,
                            "season": item.season,
                            "episode": item.episode,
                            "size": size,
                            "mtime": mtime,
                        }
                    )
                total_files += len(files)
                existing = self.db.work_exists(folder["id"], group.kind, str(group.root_path))
                if existing:
                    self.db.update_work_scan(
                        existing["id"],
                        group.title,
                        group.year,
                        group.season_count,
                        group.episode_count,
                        group.confidence,
                    )
                    if re.search(r"第\s*\d+\s*季\s*$", group.title):
                        self.db.set_work_title(existing["id"], group.title)
                    work_id = existing["id"]
                else:
                    work_id = self.db.create_work(
                        folder["id"],
                        group.kind,
                        str(group.root_path),
                        group.title,
                        group.year,
                        group.season_count,
                        group.episode_count,
                        group.confidence,
                    )
                self.db.replace_files(work_id, files)
            self.db.prune_folder_files(folder["id"], seen_paths)
            self.db.touch_folder(folder["id"])
        self.db.delete_orphan_works()
        self._normalize_tv_titles()
        return {
            "folders": len(folders),
            "works": total_groups,
            "files": total_files,
            "errors": errors,
            "stats": self.db.stats(),
        }

    # ------------------------------------------------------------------ works
    def _normalize_tv_titles(self) -> None:
        """Remove a stale "第一季/Season 1" suffix from show titles.

        Source sites often return the season name as the show title. Files
        already carry the real season (for example S02), so the show title
        should be the base title and the episode NFO keeps its own season.
        """
        for row in self.db.all_works():
            if row["kind"] != "tv":
                continue
            work = self.work(row["id"])
            if explicit_season_number(Path(work["root_path"]).name):
                continue
            meta = work.get("meta")
            current = str((meta or {}).get("title") or work.get("title") or "")
            if re.search(r"第\s*\d+\s*季\s*$", current):
                continue
            seasons = work.get("seasons") or [1]
            season = seasons[0] if len(seasons) == 1 else None
            normalized = _strip_tv_title(current, season)
            if not normalized or normalized == current:
                continue
            if meta:
                meta["title"] = normalized
                meta["sort_title"] = normalized
                if meta.get("original_title"):
                    meta["original_title"] = _strip_tv_title(str(meta["original_title"]), season)
                self.db.set_work_match(
                    work["id"],
                    title=normalized,
                    year=work.get("year"),
                    provider=work.get("provider"),
                    provider_id=work.get("provider_id"),
                    meta=meta,
                    status=work.get("status") or "matched",
                    error=work.get("error"),
                    confidence=work.get("confidence"),
                )
            else:
                self.db.set_work_title(work["id"], normalized)

    def works(
        self,
        *,
        folder_id: int | None = None,
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        series: str | None = None,
    ) -> dict[str, Any]:
        items = self.db.list_works(folder_id=folder_id, status=status, kind=kind, query=query)
        per_series, series_groups = series_info(items)
        for item in items:
            item["poster"] = bool(item.get("poster_path") and Path(item["poster_path"]).is_file())
            try:
                meta = json.loads(item.get("meta_json") or "{}") if item.get("meta_json") else {}
            except (TypeError, ValueError):
                meta = {}
            item["rating"] = meta.get("rating")
            item["provider_label"] = meta.get("source_label") or _provider_label(item.get("provider"))
            item["genres"] = (meta.get("genres") or [])[:3]
            item.update(per_series.get(int(item["id"]), {}))
            item.pop("meta_json", None)
        if series:
            items = [item for item in items if item.get("series_key") == series]
        return {
            "items": items,
            "stats": self.db.stats(),
            "folders": self.db.folders(),
            "series": series_groups,
        }

    def series(self, folder_id: int | None = None) -> list[dict[str, Any]]:
        items = self.db.list_works(folder_id=folder_id)
        return series_info(items)[1]


    def work(self, work_id: int) -> dict[str, Any]:
        work = self.db.get_work(work_id)
        if not work:
            raise AppError("条目不存在")
        seasons = _work_seasons(work)
        work["seasons"] = seasons
        work["season"] = seasons[0] if len(seasons) == 1 else None
        counts: dict[int, int] = {}
        for file in work.get("files") or []:
            season = file.get("season")
            if season is not None:
                counts[int(season)] = counts.get(int(season), 0) + 1
        work["season_counts"] = counts
        return work

    def delete_work(self, work_id: int) -> None:
        self.db.delete_work(work_id)

    def set_manual(self, work_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        work = self.work(work_id)
        meta = dict(work.get("meta") or {})
        for key in (
            "title",
            "original_title",
            "year",
            "overview",
            "rating",
            "genres",
            "countries",
            "runtime",
            "release_date",
            "poster_url",
            "fanart_url",
            "episode_titles",
        ):
            if key in patch and patch[key] not in (None, ""):
                meta[key] = patch[key]
        meta.setdefault("provider", "manual")
        meta.setdefault("provider_id", str(work_id))
        title = str(meta.get("title") or work["title"] or work["parsed_title"] or "未命名")
        meta["sort_title"] = str(meta.get("sort_title") or title)
        if patch.get("title"):
            meta["title"] = str(patch["title"])
            meta["sort_title"] = str(patch["title"])
            title = str(patch["title"])
        year = _int(meta.get("year")) or work.get("year")
        self.db.set_work_match(
            work_id,
            title=title,
            year=year,
            provider=meta.get("provider"),
            provider_id=meta.get("provider_id"),
            meta=meta,
            status="matched",
        )
        return self.work(work_id)

    # ------------------------------------------------------------------ search and apply
    def search(
        self,
        work_id: int,
        *,
        query: str | None = None,
        year: int | None = None,
        kind: str | None = None,
        provider: str | None = None,
        season: int | None = None,
    ) -> dict[str, Any]:
        work = self.work(work_id)
        base_title = (query or work.get("parsed_title") or work.get("title") or "").strip()
        if not base_title:
            raise AppError("标题为空，无法搜索")
        seasons = work.get("seasons") or [1]
        search_season = season if season in seasons else (seasons[0] if len(seasons) == 1 else None)
        season_counts = work.get("season_counts") or {}
        expected_count = int(season_counts.get(search_season, 0)) if search_season else 0
        title = _season_query(base_title, search_season)
        search_year = year if year is not None else (work.get("parsed_year") or work.get("year"))
        search_kind = kind or work["kind"]
        bundle = ProviderBundle(self.db, self.db.settings())
        candidates: list[dict[str, Any]] = []
        errors: list[str] = []
        attempts = [title] + ([base_title] if title != base_title else [])
        for attempt in attempts:
            candidates, attempt_errors = bundle.search(attempt, search_kind, search_year, provider)
            errors.extend(attempt_errors)
            if candidates:
                break
        if expected_count and search_kind == "tv":
            matching = [
                candidate
                for candidate in candidates
                if candidate.get("episode_count")
                and abs(int(candidate["episode_count"]) - expected_count) <= 1
            ]
            if matching:
                candidates = matching
        candidates = rank_candidates(
            candidates,
            title,
            search_year,
            search_kind,
            season=search_season,
            episode_count=expected_count,
        )
        return {
            "query": title,
            "year": search_year,
            "kind": search_kind,
            "season": search_season,
            "seasons": seasons,
            "season_counts": season_counts,
            "episode_count": expected_count,
            "candidates": candidates,
            "errors": errors,
        }

    def resolve(self, work_id: int, provider: str, item_id: str) -> dict[str, Any]:
        work = self.work(work_id)
        bundle = ProviderBundle(self.db, self.db.settings())
        meta = bundle.detail(provider, item_id, work["kind"])
        return meta

    def apply(
        self,
        work_id: int,
        *,
        provider: str | None = None,
        item_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        write_nfo: bool | None = None,
        download_images: bool | None = None,
    ) -> dict[str, Any]:
        work = self.work(work_id)
        meta: dict[str, Any]
        if provider and item_id:
            bundle = ProviderBundle(self.db, self.db.settings())
            meta = bundle.detail(provider, item_id, work["kind"])
        elif work.get("meta"):
            meta = dict(work["meta"])
        else:
            raise AppError("请先选择一个匹配结果")

        if overrides:
            for key, value in overrides.items():
                if key in {
                    "title",
                    "original_title",
                    "sort_title",
                    "year",
                    "overview",
                    "tagline",
                    "rating",
                    "votes",
                    "runtime",
                    "release_date",
                    "genres",
                    "countries",
                    "languages",
                    "directors",
                    "writers",
                    "studios",
                    "aliases",
                    "poster_url",
                    "fanart_url",
                    "episode_titles",
                } and value not in (None, ""):
                    meta[key] = value

        # Any explicit apply is an edit: overwrite the existing NFO even when
        # the global "overwrite" option is off. Batch jobs keep the global policy.
        return self._apply_meta(
            work,
            meta,
            write_nfo=write_nfo,
            download_images=download_images,
            force_nfo=True,
        )

    def _apply_meta(
        self,
        work: dict[str, Any],
        meta: dict[str, Any],
        *,
        write_nfo: bool | None = None,
        download_images: bool | None = None,
        force_nfo: bool = False,
    ) -> dict[str, Any]:
        settings = self.db.settings()
        write_nfo = settings.get("write_nfo", True) if write_nfo is None else write_nfo
        download_images = settings.get("download_images", True) if download_images is None else download_images
        meta = dict(meta)
        if work["kind"] == "tv":
            seasons = _work_seasons(work)
            season = seasons[0] if len(seasons) == 1 else None
            meta["title"] = _strip_tv_title(str(meta.get("title") or ""), season)
            meta["sort_title"] = _strip_tv_title(
                str(meta.get("sort_title") or meta.get("title") or ""), season
            )
            if meta.get("original_title"):
                meta["original_title"] = _strip_tv_title(str(meta["original_title"]), season)
        files = work.get("files") or []
        if not files:
            raise AppError("条目没有关联的视频文件")
        provider = str(meta.get("provider") or "manual")
        warnings: list[str] = []
        poster_path: Path | None = None
        fanart_path: Path | None = None
        for key in ("poster_path", "fanart_path"):
            existing = work.get(key)
            if existing and Path(existing).is_file():
                if key == "poster_path":
                    poster_path = Path(existing)
                else:
                    fanart_path = Path(existing)

        video = Path(files[0]["path"])
        if work["kind"] == "movie":
            art_dir = video.parent
            poster_stem = f"{video.stem}-poster"
            fanart_stem = f"{video.stem}-fanart"
        else:
            art_dir = Path(work["root_path"])
            poster_stem = "poster"
            fanart_stem = "fanart"

        if download_images:
            for key, stem in (("poster_url", poster_stem), ("fanart_url", fanart_stem)):
                url = str(meta.get(key) or "").strip()
                if not url:
                    continue
                try:
                    saved = save_image(
                        url,
                        art_dir,
                        stem,
                        provider=provider,
                        overwrite=bool(settings.get("overwrite_artwork", True)),
                        timeout=int(settings.get("request_timeout", 20)),
                        proxy=str(settings.get("http_proxy", "") or ""),
                    )
                    if key == "poster_url":
                        poster_path = saved
                    else:
                        fanart_path = saved
                except ArtworkError as exc:
                    warnings.append(f"{'海报' if key == 'poster_url' else '背景图'}：{exc}")

        probes: dict[int, dict[str, Any]] = {}
        if settings.get("probe_files", True):
            ffprobe = find_ffprobe(str(settings.get("ffprobe_path", "") or ""))
            if ffprobe:
                for file_row in files:
                    probe = probe_file(Path(file_row["path"]), ffprobe)
                    if probe:
                        probes[int(file_row["id"])] = probe
            else:
                warnings.append("未找到 ffprobe，跳过媒体信息读取")

        nfo_report = None
        if write_nfo:
            nfo_report = write_work_nfo(
                work,
                meta,
                poster_path=poster_path,
                fanart_path=fanart_path,
                overwrite=bool(settings.get("overwrite_nfo", False)) or force_nfo,
                probes=probes,
            )
            warnings.extend(nfo_report.get("warnings") or [])
            if nfo_report.get("episode_paths"):
                for file_id, nfo_path in nfo_report["episode_paths"].items():
                    self.db.connect().execute("UPDATE files SET nfo_path=? WHERE id=?", (nfo_path, file_id))

        title = str(meta.get("title") or work["title"] or work["parsed_title"] or "未命名")
        season_number = explicit_season_number(Path(work["root_path"]).name)
        if season_number:
            meta["season"] = season_number
            meta["season_title"] = season_title(title, season_number)
            title = meta["season_title"]
        year = _int(meta.get("year")) or work.get("year")
        status = "applied" if (write_nfo or download_images) else "matched"
        self.db.set_work_match(
            work_id=work["id"],
            title=title,
            year=year,
            provider=str(meta.get("provider") or ""),
            provider_id=str(meta.get("provider_id") or ""),
            meta=meta,
            status=status,
        )
        self.db.set_work_artwork(
            work["id"],
            str(poster_path) if poster_path else work.get("poster_path"),
            str(fanart_path) if fanart_path else work.get("fanart_path"),
            (nfo_report or {}).get("main_path") or work.get("nfo_path"),
        )
        self.db.add_history(
            "apply",
            {
                "work_id": work["id"],
                "title": title,
                "provider": provider,
                "poster": str(poster_path) if poster_path else None,
                "nfo": (nfo_report or {}).get("main_path"),
            },
        )
        updated = self.work(work["id"])
        return {
            "work": updated,
            "poster": str(poster_path) if poster_path else None,
            "fanart": str(fanart_path) if fanart_path else None,
            "nfo": nfo_report,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------ batch job
    def start_auto_job(self, *, folder_id: int | None = None, status: str = "new") -> dict[str, Any]:
        statuses = ["new", "review", "error"] if status in ("attention", "all") else [status]
        works = []
        for item_status in statuses:
            works.extend(
                self.db.list_works(folder_id=folder_id, status=item_status, limit=1000)
            )
        # de-duplicate and keep stable order
        seen = set()
        queue = []
        for item in works:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            queue.append(item)

        job_id = self.db.create_job("auto", len(queue))
        thread = threading.Thread(target=self._run_auto_job, args=(job_id, queue), daemon=True)
        with self._job_lock:
            self._jobs[job_id] = thread
        thread.start()
        return self.db.get_job(job_id) or {"id": job_id}

    def _run_auto_job(self, job_id: str, queue: list[dict[str, Any]]) -> None:
        self.db.update_job(job_id, status="running", message="正在自动匹配…")
        settings = self.db.settings()
        threshold = int(settings.get("auto_threshold", 82) or 82)
        counts = {"applied": 0, "review": 0, "failed": 0}
        errors: list[dict[str, str]] = []

        for index, item in enumerate(queue):
            if self.db.job_cancelled(job_id):
                self.db.update_job(job_id, status="cancelled", message="已取消")
                return
            try:
                work = self.work(item["id"])
                title = (work.get("parsed_title") or work.get("title") or "").strip()
                year = work.get("parsed_year") or work.get("year")
                seasons = work.get("seasons") or _work_seasons(work)
                if len(seasons) > 1:
                    season_counts = work.get("season_counts") or {}
                    detail = " / ".join(
                        f"S{value:02d} {season_counts.get(value, 0)}集" for value in seasons
                    )
                    counts["review"] += 1
                    self.db.set_work_status(
                        work["id"],
                        "review",
                        f"检测到多季作品（{detail}），请按集数选择季并确认",
                    )
                    self.db.update_job(
                        job_id,
                        done=index + 1,
                        message=f"已处理 {index + 1}/{len(queue)}（多季需确认）",
                    )
                    continue
                season = seasons[0]
                expected_count = len(work.get("files") or [])
                bundle = ProviderBundle(self.db, settings)
                search_title = _season_query(title, season)
                candidates, search_errors = bundle.search(search_title, work["kind"], year, None)
                if not candidates and search_title != title:
                    candidates, more_errors = bundle.search(title, work["kind"], year, None)
                    search_errors.extend(more_errors)
                if expected_count and work["kind"] == "tv":
                    matching = [
                        candidate
                        for candidate in candidates
                        if candidate.get("episode_count")
                        and abs(int(candidate["episode_count"]) - expected_count) <= 1
                    ]
                    if matching:
                        candidates = matching
                candidates = rank_candidates(
                    candidates,
                    search_title,
                    year,
                    work["kind"],
                    season=season,
                    episode_count=expected_count,
                )
                best, confident = pick_best(candidates, threshold)
                if best is None:
                    counts["failed"] += 1
                    message = "；".join(search_errors) or "没有找到匹配"
                    self.db.set_work_status(work["id"], "error", message[:500])
                    errors.append({"title": title, "error": message})
                elif not confident:
                    counts["review"] += 1
                    self.db.set_work_status(
                        work["id"],
                        "review",
                        f"找到 {len(candidates)} 个候选，最高分 {best.get('score', 0)}，请手动确认",
                    )
                else:
                    meta = bundle.detail(best["provider"], best["id"], work["kind"])
                    self._apply_meta(work, meta)
                    counts["applied"] += 1
            except Exception as exc:  # noqa: BLE001 - per-item job isolation
                counts["failed"] += 1
                error_text = str(exc)[:300]
                try:
                    self.db.set_work_status(item["id"], "error", error_text)
                except Exception:
                    pass
                errors.append({"title": item.get("title") or item.get("parsed_title") or "", "error": error_text})

            self.db.update_job(
                job_id,
                done=index + 1,
                message=f"已处理 {index + 1}/{len(queue)}",
            )

        result = {"counts": counts, "errors": errors[:30]}
        self.db.update_job(
            job_id,
            status="done",
            done=len(queue),
            message=f"完成：成功 {counts['applied']}，待确认 {counts['review']}，失败 {counts['failed']}",
            result=result,
        )

    # ------------------------------------------------------------------ organize
    def organize_plan(self, work_id: int) -> dict[str, Any]:
        work = self.work(work_id)
        return plan_organize(work, work.get("meta"))

    def organize(self, work_id: int) -> dict[str, Any]:
        work = self.work(work_id)
        plan = plan_organize(work, work.get("meta"))
        if not plan["moves"]:
            return {"plan": plan, "result": {"moved": [], "work_root": work["root_path"]}}
        result = execute_organize(work, plan)
        moved = {Path(m["src"]): Path(m["dest"]) for m in result["moved"]}
        new_files = []
        for file_row in work.get("files") or []:
            old = Path(file_row["path"])
            new = moved.get(old, old)
            new_files.append((int(file_row["id"]), str(new)))
        new_root = result.get("work_root") or work["root_path"]
        if work["kind"] == "tv":
            new_root = str(Path(new_root))
        elif new_files:
            new_root = new_files[0][1]
        self.db.update_work_paths(work["id"], new_root, new_files)

        def moved_or_old(value: str | None) -> str | None:
            if not value:
                return value
            return str(moved.get(Path(value), Path(value)))

        self.db.set_work_artwork(
            work["id"],
            moved_or_old(work.get("poster_path")),
            moved_or_old(work.get("fanart_path")),
            moved_or_old(work.get("nfo_path")),
        )
        self.db.add_history("organize", {"work_id": work["id"], "plan": plan, "result": result})
        if result.get("errors"):
            raise AppError("部分文件移动失败：" + "；".join(result["errors"][:3]))
        return {"plan": plan, "result": result, "work": self.work(work["id"])}

    # ------------------------------------------------------------------ misc
    def nfo_text(self, work_id: int) -> str:
        work = self.work(work_id)
        path = work.get("nfo_path")
        if path and Path(path).is_file():
            return Path(path).read_text(encoding="utf-8", errors="replace")
        meta = work.get("meta") or {}
        if work["kind"] == "movie":
            return build_movie_nfo(meta)
        return "还没有生成 tvshow.nfo"

    def stats(self) -> dict[str, Any]:
        return self.db.stats()

    def recent_history(self) -> list[dict[str, Any]]:
        return self.db.recent_history(30)

    def choose_folder(self) -> str | None:
        return self._ask_path("选择影视文件夹", "folder")

    def choose_image(self) -> str | None:
        return self._ask_path("选择封面图片", "image")

    def _ask_path(self, title: str, mode: str) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as exc:  # noqa: BLE001
            raise AppError("系统文件选择器不可用，请直接输入路径") from exc
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if mode == "image":
                selected = filedialog.askopenfilename(
                    title=title,
                    filetypes=[
                        ("图片", "*.jpg *.jpeg *.png *.webp *.gif"),
                        ("所有文件", "*.*"),
                    ],
                )
            else:
                selected = filedialog.askdirectory(title=title)
            root.destroy()
            return selected or None
        except Exception as exc:  # noqa: BLE001
            raise AppError(f"无法打开文件选择器：{exc}") from exc

    def open_path(self, work_id: int) -> None:
        work = self.work(work_id)
        target = Path(work["root_path"])
        if work["kind"] == "movie" and work.get("files"):
            target = Path(work["files"][0]["path"]).parent
        if not target.exists():
            raise AppError("路径不存在")
        folder = Path(work["folder_path"]).resolve(strict=False)
        if not _within(target, folder):
            raise AppError("路径不在媒体库内")
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    def poster_file(self, work_id: int) -> Path | None:
        work = self.work(work_id)
        path = work.get("poster_path")
        if path and Path(path).is_file():
            return Path(path)
        return None

    def fanart_file(self, work_id: int) -> Path | None:
        work = self.work(work_id)
        path = work.get("fanart_path")
        if path and Path(path).is_file():
            return Path(path)
        return None

    def content_type(self, path: Path) -> str:
        return image_content_type(path)

    # ------------------------------------------------------------------ demo
    def make_demo(self) -> dict[str, Any]:
        base = self.data_root / "demo-library"
        shows = {
            "流浪地球 (2019)": ["流浪地球.2019.1080p.BluRay.x264.mkv"],
            "三体 (2023)": [f"Season 01/三体.S01E{i:02d}.2160p.WEB-DL.mkv" for i in range(1, 7)],
            "疯狂动物城 (2016)": ["Zootopia.2016.1080p.BluRay.x264.mkv"],
            "漫长的季节 (2023)": [f"Season 01/漫长的季节.S01E{i:02d}.1080p.mkv" for i in range(1, 4)],
            "沙丘2 (2024)": ["Dune.Part.Two.2024.2160p.WEB-DL.mkv"],
            "奥本海默 (2023)": ["Oppenheimer.2023.1080p.BluRay.x265.mkv"],
        }
        for folder_name, files in shows.items():
            for name in files:
                target = base / folder_name / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes(b"")

        folder = self.add_folder(str(base))
        self.scan(folder["id"])
        demo_meta = {
            "流浪地球": {
                "title": "流浪地球",
                "original_title": "The Wandering Earth",
                "year": 2019,
                "kind": "movie",
                "overview": "太阳即将毁灭，人类在地球表面建造出巨大的推进器，寻找新的家园。然而宇宙之路危机四伏，为了拯救地球，流浪地球时代的年轻人再次挺身而出。",
                "rating": 7.9,
                "votes": 2060235,
                "runtime": 125,
                "release_date": "2019-02-05",
                "genres": ["科幻", "冒险", "灾难"],
                "countries": ["中国大陆"],
                "directors": ["郭帆"],
                "actors": [{"name": "吴京", "role": "", "order": 0}, {"name": "屈楚萧", "role": "", "order": 1}],
                "provider": "demo",
                "provider_id": "douban:26266893",
                "provider_label": "豆瓣 · 演示数据",
            },
            "三体": {
                "title": "三体",
                "original_title": "Three-Body",
                "year": 2023,
                "kind": "tv",
                "overview": "21世纪初，地球基础科学遭到前所未有的动摇。纳米科学家汪淼被警官史强请到联合作战中心，逐步接近一个名为“科学边界”的组织，以及一款神秘游戏《三体》。",
                "rating": 8.7,
                "votes": 540503,
                "runtime": 45,
                "release_date": "2023-01-15",
                "genres": ["剧情", "科幻"],
                "countries": ["中国大陆"],
                "directors": ["杨磊"],
                "actors": [{"name": "张鲁一", "role": "", "order": 0}, {"name": "于和伟", "role": "", "order": 1}],
                "episodes_count": 30,
                "provider": "demo",
                "provider_id": "douban:26647087",
                "provider_label": "豆瓣 · 演示数据",
            },
            "疯狂动物城": {
                "title": "疯狂动物城",
                "original_title": "Zootopia",
                "year": 2016,
                "kind": "movie",
                "overview": "兔子朱迪通过努力成为动物城警察局的第一个兔子警官。为了证明自己，她不得不与狐狸尼克合作，揭开动物城失踪案背后的阴谋。",
                "rating": 9.2,
                "votes": 1800000,
                "runtime": 109,
                "release_date": "2016-03-04",
                "genres": ["动画", "喜剧", "冒险"],
                "countries": ["美国"],
                "directors": ["拜伦·霍华德", "瑞奇·摩尔"],
                "actors": [{"name": "金妮弗·古德温", "role": "", "order": 0}],
                "provider": "demo",
                "provider_id": "douban:25662329",
                "provider_label": "豆瓣 · 演示数据",
            },
            "漫长的季节": {
                "title": "漫长的季节",
                "original_title": "The Long Season",
                "year": 2023,
                "kind": "tv",
                "overview": "桦林小城，一桩尘封多年的碎尸案，三个老人，一条漫长的季节。时间像一列火车，载着秘密驶向终点。",
                "rating": 9.4,
                "votes": 900000,
                "runtime": 45,
                "release_date": "2023-04-22",
                "genres": ["剧情", "悬疑", "犯罪"],
                "countries": ["中国大陆"],
                "directors": ["辛爽"],
                "actors": [{"name": "范伟", "role": "", "order": 0}, {"name": "秦昊", "role": "", "order": 1}],
                "episodes_count": 12,
                "provider": "demo",
                "provider_id": "douban:35546329",
                "provider_label": "豆瓣 · 演示数据",
            },
            "沙丘2": {
                "title": "沙丘2",
                "original_title": "Dune: Part Two",
                "year": 2024,
                "kind": "movie",
                "overview": "保罗·厄崔迪与契妮和弗雷曼人联手，踏上一段复仇之路。他必须阻止一场只有他能预见的可怕未来。",
                "rating": 8.3,
                "votes": 500000,
                "runtime": 166,
                "release_date": "2024-03-08",
                "genres": ["科幻", "冒险"],
                "countries": ["美国"],
                "directors": ["丹尼斯·维伦纽瓦"],
                "actors": [{"name": "提莫西·查拉梅", "role": "", "order": 0}],
                "provider": "demo",
                "provider_id": "tmdb:693134",
                "provider_label": "TMDb · 演示数据",
            },
            "奥本海默": {
                "title": "奥本海默",
                "original_title": "Oppenheimer",
                "year": 2023,
                "kind": "movie",
                "overview": "原子弹之父罗伯特·奥本海默的传奇人生，以及他在曼哈顿计划之后面对的审判。",
                "rating": 8.9,
                "votes": 700000,
                "runtime": 180,
                "release_date": "2023-07-21",
                "genres": ["剧情", "传记", "历史"],
                "countries": ["美国"],
                "directors": ["克里斯托弗·诺兰"],
                "actors": [{"name": "基里安·墨菲", "role": "", "order": 0}],
                "provider": "demo",
                "provider_id": "tmdb:872585",
                "provider_label": "TMDb · 演示数据",
            },
        }
        for work in self.db.all_works(folder["id"]):
            meta = None
            for key, value in demo_meta.items():
                if key in (work.get("parsed_title") or "") or key in work["root_path"]:
                    meta = dict(value)
                    meta["provider_id"] = f"demo:{work['id']}"
                    break
            if meta and work["status"] == "new":
                self.db.set_work_match(
                    work["id"],
                    title=meta["title"],
                    year=meta["year"],
                    provider="demo",
                    provider_id=meta["provider_id"],
                    meta=meta,
                    status="matched",
                )
        self.db.add_history("demo", {"folder": str(base)})
        return {"folder": str(base), "stats": self.db.stats()}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _provider_label(provider: str | None) -> str:
    return {
        "douban": "豆瓣",
        "fankuhub": "番库",
        "bilibili": "哔哩哔哩",
        "tmdb": "TMDb",
        "demo": "演示",
        "manual": "手动",
    }.get(provider or "", provider or "未匹配")


SEASON_SUFFIX_RE = re.compile(
    r"(?i)\s*(?:第\s*[一二三四五六七八九十\d]{1,3}\s*季|season\s*\d{1,2}|s\d{1,2})\s*$"
)


def _work_seasons(work: dict[str, Any]) -> list[int]:
    seasons = sorted(
        {
            int(file["season"])
            for file in (work.get("files") or [])
            if file.get("season") is not None
        }
    )
    return seasons or [1]


def _strip_season_suffix(value: str) -> str:
    text = SEASON_SUFFIX_RE.sub("", value or "").strip(" -_—:：")
    return text or value


def _strip_tv_title(value: str, season: int | None) -> str:
    text = _strip_season_suffix(value)
    if season and season > 1:
        text = re.sub(rf"\s+{season}\s*$", "", text).strip(" -_—:：")
    return text or value


def _season_query(title: str, season: int | None) -> str:
    base = _strip_tv_title(title, season)
    if season and season > 1:
        return f"{base} 第{season}季"
    return base
