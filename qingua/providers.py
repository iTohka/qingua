from __future__ import annotations

import gzip
import html as html_lib
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .db import Database

DOUBAN_SUGGEST = "https://movie.douban.com/j/subject_suggest?q={query}"
DOUBAN_DETAIL = "https://m.douban.com/rexxar/api/v2/movie/{id}"
DOUBAN_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
FANKUHUB_BASE = "https://fankuhub.com"
BILIBILI_SPI = "https://api.bilibili.com/x/frontend/finger/spi"
BILIBILI_SEARCH = "https://api.bilibili.com/x/web-interface/search/type"
BILIBILI_SEASON = "https://api.bilibili.com/pgc/view/web/season"
BILIBILI_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}

CACHE_SEARCH_TTL = 6 * 60 * 60
CACHE_DETAIL_TTL = 24 * 60 * 60


class ProviderError(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self, interval: float = 0.35):
        self.interval = interval
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0)
            delay = self.interval - (now - last)
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


class HttpClient:
    def __init__(self, timeout: float = 20, proxy: str = ""):
        self.timeout = max(5, min(int(timeout), 90))
        self.proxy = proxy.strip()
        self.limiter = _RateLimiter()
        handlers: list[urllib.request.BaseHandler] = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        self.opener = urllib.request.build_opener(*handlers)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        data = self.get_bytes(url, headers=headers)
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("服务器返回了无法解析的数据") from exc

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        data = self.get_bytes(url, headers=headers)
        return data.decode("utf-8", "replace")

    def get_bytes(self, url: str, headers: dict[str, str] | None = None, retries: int = 2) -> bytes:
        host = urllib.parse.urlparse(url).netloc
        request_headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json,text/html,image/avif,image/webp,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Accept-Encoding": "gzip",
        }
        if headers:
            request_headers.update(headers)

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            self.limiter.wait(host)
            try:
                req = urllib.request.Request(url, headers=request_headers)
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read()
                    if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    return raw
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise ProviderError("请求太频繁，被数据源限流了，请稍后再试") from exc
                if exc.code == 412:
                    raise ProviderError("请求被数据源风控拦截（412）") from exc
                if exc.code in (400, 401, 403, 404):
                    message = {
                        401: "接口需要授权，请检查 API Key",
                        403: "请求被拒绝，可能需要代理",
                        404: "没有找到这个条目",
                    }.get(exc.code, f"HTTP {exc.code}")
                    raise ProviderError(message) from exc
                last_error = exc
            except ProviderError:
                raise
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - network boundary
                last_error = exc

            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))

        detail = str(last_error)
        if isinstance(last_error, urllib.error.URLError):
            detail = str(last_error.reason)
        raise ProviderError(f"网络连接失败：{detail[:160]}")


@dataclass
class ProviderBundle:
    db: Database
    settings: dict[str, Any]

    def __post_init__(self) -> None:
        self.http = HttpClient(
            timeout=float(self.settings.get("request_timeout", 20)),
            proxy=str(self.settings.get("http_proxy", "") or ""),
        )

    @property
    def language(self) -> str:
        return str(self.settings.get("language", "zh-CN") or "zh-CN")

    @property
    def tmdb_key(self) -> str:
        return str(self.settings.get("tmdb_api_key", "") or "").strip()

    @property
    def tmdb_base(self) -> str:
        return str(self.settings.get("tmdb_base", "https://api.themoviedb.org/3") or "").rstrip("/")

    @property
    def tmdb_image_base(self) -> str:
        return str(self.settings.get("tmdb_image_base", "https://image.tmdb.org/t/p") or "").rstrip("/")

    def _proxy_image(self, url: str) -> str:
        prefix = str(self.settings.get("image_proxy", "") or "").strip()
        if prefix and url:
            if "{url}" in prefix:
                return prefix.replace("{url}", urllib.parse.quote(url, safe=""))
            return prefix.rstrip("/") + "/" + url
        return url

    # ------------------------------------------------------------------ Douban
    def douban_search(self, query: str, kind: str | None, year: int | None, limit: int = 12) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        cache_key = f"search:douban:{kind}:{year}:{query.lower()}"
        cached = self.db.cache_get(cache_key, CACHE_SEARCH_TTL)
        if cached is not None:
            return cached[:limit]

        url = DOUBAN_SUGGEST.format(query=urllib.parse.quote(query))
        payload = self.http.get_json(
            url,
            headers={"User-Agent": DESKTOP_UA, "Referer": "https://movie.douban.com/"},
        )
        if not isinstance(payload, list):
            raise ProviderError("豆瓣搜索返回格式异常")

        results: list[dict] = []
        for item in payload:
            try:
                item_id = str(item.get("id") or "").strip()
                title = str(item.get("title") or "").strip()
                if not item_id or not title:
                    continue
                episode = str(item.get("episode") or "").strip()
                item_kind = "tv" if episode else "movie"
                item_year = _to_int(item.get("year"))
                results.append(
                    {
                        "provider": "douban",
                        "id": item_id,
                        "title": title,
                        "original_title": str(item.get("sub_title") or "").strip(),
                        "year": item_year,
                        "kind": item_kind,
                        "rating": None,
                        "votes": None,
                        "poster_url": self._proxy_image(str(item.get("img") or "")),
                        "episode_count": _to_int(episode) or 0,
                        "source_label": "豆瓣",
                    }
                )
            except Exception:
                continue
        self.db.cache_set(cache_key, results)
        return results[:limit]

    def douban_detail(self, item_id: str) -> dict:
        cache_key = f"detail:douban:{item_id}"
        cached = self.db.cache_get(cache_key, CACHE_DETAIL_TTL)
        if cached is not None:
            return cached

        url = DOUBAN_DETAIL.format(id=urllib.parse.quote(str(item_id)))
        data = self.http.get_json(
            url,
            headers={
                "User-Agent": DOUBAN_MOBILE_UA,
                "Referer": "https://m.douban.com/",
                "Accept": "application/json, text/plain, */*",
            },
        )
        if not isinstance(data, dict) or not data.get("title"):
            raise ProviderError("豆瓣详情为空")

        kind = "tv" if (data.get("is_tv") or data.get("type") == "tv" or _to_int(data.get("episodes_count"))) else "movie"
        rating = data.get("rating") or {}
        pubdates = data.get("pubdate") or []
        durations = data.get("durations") or []
        actors = []
        for index, actor in enumerate((data.get("actors") or [])[:24]):
            if isinstance(actor, dict) and actor.get("name"):
                actors.append(
                    {
                        "name": actor["name"],
                        "role": actor.get("role") or "",
                        "order": index,
                        "thumb": actor.get("avatar") or "",
                    }
                )
        directors = [a.get("name") for a in (data.get("directors") or []) if isinstance(a, dict) and a.get("name")]
        genres = [str(g) for g in (data.get("genres") or []) if g]
        countries = [str(c) for c in (data.get("countries") or []) if c]
        languages = [str(c) for c in (data.get("languages") or []) if c]
        aliases = [str(a) for a in (data.get("aka") or []) if a]
        original_title = str(data.get("original_title") or "").strip()
        if not original_title:
            for alias in aliases:
                if re.search(r"[A-Za-z]", alias):
                    original_title = alias
                    break

        meta = {
            "provider": "douban",
            "provider_id": str(item_id),
            "kind": kind,
            "title": str(data.get("title") or "").strip(),
            "original_title": original_title,
            "sort_title": str(data.get("title") or "").strip(),
            "year": _to_int(data.get("year")),
            "overview": _strip_html(str(data.get("intro") or "")),
            "tagline": "",
            "runtime": _parse_duration(durations[0]) if durations else None,
            "rating": _to_float(rating.get("value")),
            "votes": _to_int(rating.get("count")),
            "release_date": _normalize_date(pubdates[0]) if pubdates else None,
            "genres": genres,
            "countries": countries,
            "languages": languages,
            "directors": directors,
            "writers": [],
            "actors": actors,
            "poster_url": self._proxy_image(str(data.get("cover_url") or "")),
            "fanart_url": "",
            "tmdb_id": None,
            "imdb_id": str(data.get("imdb_id") or "").strip() or None,
            "douban_id": str(item_id),
            "episodes_count": _to_int(data.get("episodes_count")) or 0,
            "seasons_count": 1,
            "studios": [],
            "aliases": aliases,
            "source_label": "豆瓣",
            "raw_url": str(data.get("url") or ""),
        }
        self.db.cache_set(cache_key, meta)
        return meta

    # ------------------------------------------------------------------ Fankuhub
    def _fankuhub_html(self, url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        for attempt in range(2):
            html = self.http.get_text(url, headers=headers)
            if "人机校验" not in html:
                return html
            if attempt == 0:
                time.sleep(1.5)
        raise ProviderError("番库触发了人机校验，请稍后再试（不要连续快速搜索）")

    def fankuhub_search(self, query: str, kind: str | None, year: int | None, limit: int = 12) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        cache_key = f"search:fankuhub:{kind}:{year}:{query.lower()}"
        cached = self.db.cache_get(cache_key, CACHE_SEARCH_TTL)
        if cached is not None:
            return cached[:limit]

        url = f"{FANKUHUB_BASE}/anime?q={urllib.parse.quote(query)}"
        html = self._fankuhub_html(url)
        items = _extract_fanku_items(html)
        if items is None:
            raise ProviderError("番库搜索页解析失败，站点结构可能已变化")

        results: list[dict] = []
        for item in items:
            try:
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                title = (
                    item.get("titleZh")
                    or item.get("titleEn")
                    or item.get("titleRomaji")
                    or item.get("titleJa")
                    or ""
                ).strip()
                if not title:
                    continue
                aliases = [
                    str(item.get(key) or "").strip()
                    for key in ("titleJa", "titleEn", "titleRomaji")
                    if item.get(key)
                ]
                item_kind = "movie" if str(item.get("type") or "").upper() == "MOVIE" else "tv"
                results.append(
                    {
                        "provider": "fankuhub",
                        "id": item_id,
                        "title": title,
                        "original_title": str(item.get("titleJa") or item.get("titleEn") or "").strip(),
                        "aliases": aliases,
                        "year": _year_from_date(item.get("airDate")),
                        "kind": item_kind,
                        "rating": _to_float(item.get("aggregateScore")),
                        "votes": _to_int(item.get("ratingCount")),
                        "poster_url": _https_url(str(item.get("coverUrl") or "")),
                        "episode_count": _to_int(item.get("episodes")) or 0,
                        "source_label": "番库",
                    }
                )
            except Exception:
                continue
        self.db.cache_set(cache_key, results)
        return results[:limit]

    def fankuhub_detail(self, item_id: str, kind: str | None = None) -> dict:
        cache_key = f"detail:fankuhub:{item_id}"
        cached = self.db.cache_get(cache_key, CACHE_DETAIL_TTL)
        if cached is not None:
            return cached

        url = f"{FANKUHUB_BASE}/anime/{urllib.parse.quote(str(item_id))}"
        time.sleep(0.7)
        html = self._fankuhub_html(url)
        detail = _extract_fanku_detail(html)
        if not detail:
            raise ProviderError("番库详情页解析失败，站点结构可能已变化")

        title = (
            detail.get("titleZh")
            or detail.get("titleEn")
            or detail.get("titleRomaji")
            or detail.get("titleJa")
            or ""
        ).strip()
        if not title:
            raise ProviderError("番库详情缺少标题")
        aliases = [
            str(detail.get(key) or "").strip()
            for key in ("titleJa", "titleEn", "titleRomaji")
            if detail.get(key)
        ]
        episodes = _to_int(detail.get("episodes")) or 0
        meta = {
            "provider": "fankuhub",
            "provider_id": str(item_id),
            "kind": "movie" if str(detail.get("type") or "").upper() == "MOVIE" else "tv",
            "title": title,
            "original_title": str(detail.get("titleJa") or detail.get("titleEn") or "").strip(),
            "sort_title": title,
            "year": _year_from_date(detail.get("airDate")),
            "overview": _clean_text(str(detail.get("synopsis") or "")),
            "release_date": _date_part(detail.get("airDate")),
            "episodes_count": episodes or len(detail.get("episodes") or []),
            "seasons_count": 1,
            "poster_url": _https_url(str(detail.get("coverUrl") or "")),
            "fanart_url": "",
            "genres": [],
            "countries": [],
            "directors": [],
            "writers": [],
            "actors": [],
            "rating": None,
            "votes": None,
            "runtime": None,
            "aliases": aliases,
            "source_label": "番库",
            "raw_url": url,
        }
        self.db.cache_set(cache_key, meta)
        return meta

    # ------------------------------------------------------------------ Bilibili
    def _bilibili_cookie(self) -> str:
        cached = self.db.cache_get("bilibili:buvid3", 12 * 60 * 60)
        if isinstance(cached, str) and cached:
            return cached
        data = self.http.get_json(BILIBILI_SPI, headers=dict(BILIBILI_HEADERS))
        cookie = str(((data or {}).get("data") or {}).get("b_3") or "").strip()
        if not cookie:
            raise ProviderError("无法取得 B 站访客标识")
        self.db.cache_set("bilibili:buvid3", cookie)
        return cookie

    def _bilibili_headers(self) -> dict[str, str]:
        headers = dict(BILIBILI_HEADERS)
        headers["Cookie"] = f"buvid3={self._bilibili_cookie()}"
        return headers

    def bilibili_search(self, query: str, kind: str | None, year: int | None, limit: int = 12) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        cache_key = f"search:bilibili:{kind}:{year}:{query.lower()}"
        cached = self.db.cache_get(cache_key, CACHE_SEARCH_TTL)
        if cached is not None:
            return cached[:limit]

        params = {"search_type": "media_bangumi", "keyword": query}
        url = f"{BILIBILI_SEARCH}?{urllib.parse.urlencode(params)}"
        data = None
        for attempt in range(2):
            try:
                data = self.http.get_json(url, headers=self._bilibili_headers())
                break
            except ProviderError as exc:
                if attempt == 0 and ("412" in str(exc) or "风控" in str(exc)):
                    self.db.cache_delete("bilibili:buvid3")
                    continue
                raise
        if not isinstance(data, dict) or data.get("code") != 0:
            message = (data or {}).get("message") if isinstance(data, dict) else "B 站返回异常"
            raise ProviderError(f"B 站搜索失败：{message or '未知错误'}")

        results: list[dict] = []
        for item in ((data.get("data") or {}).get("result") or []):
            try:
                season_id = str(item.get("season_id") or item.get("media_id") or "").strip()
                title = _strip_tags(str(item.get("title") or "")).strip()
                if not season_id or not title:
                    continue
                eps = item.get("eps") or []
                poster = ""
                if eps and isinstance(eps[0], dict):
                    poster = str(eps[0].get("cover") or "")
                if not poster:
                    poster = str(item.get("cover") or "")
                results.append(
                    {
                        "provider": "bilibili",
                        "id": season_id,
                        "title": title,
                        "original_title": _strip_tags(str(item.get("org_title") or "")).strip(),
                        "aliases": [_strip_tags(str(item.get("org_title") or "")).strip()],
                        "year": None,
                        "kind": "tv",
                        "rating": None,
                        "votes": None,
                        "poster_url": _https_url(poster),
                        "episode_count": _to_int(item.get("ep_size")) or 0,
                        "source_label": "哔哩哔哩",
                    }
                )
            except Exception:
                continue
        self.db.cache_set(cache_key, results)
        return results[:limit]

    def bilibili_detail(self, item_id: str, kind: str | None = None) -> dict:
        cache_key = f"detail:bilibili:{item_id}"
        cached = self.db.cache_get(cache_key, CACHE_DETAIL_TTL)
        if cached is not None:
            return cached

        url = f"{BILIBILI_SEASON}?season_id={urllib.parse.quote(str(item_id))}"
        data = self.http.get_json(url, headers=self._bilibili_headers())
        if not isinstance(data, dict) or data.get("code") != 0:
            raise ProviderError(f"B 站详情失败：{(data or {}).get('message') or '未知错误'}")
        result = data.get("result") or {}
        title = str(result.get("title") or "").strip()
        if not title:
            raise ProviderError("B 站详情缺少标题")
        episodes = result.get("episodes") or []
        pub_time = ((result.get("publish") or {}).get("pub_time") or "")
        release_date = _date_part(pub_time)
        episode_titles: dict[str, str] = {}
        for index, episode in enumerate(episodes, start=1):
            if not isinstance(episode, dict):
                continue
            long_title = str(episode.get("long_title") or episode.get("title") or "").strip()
            if long_title:
                episode_titles[f"S01E{index:02d}"] = long_title

        meta = {
            "provider": "bilibili",
            "provider_id": str(item_id),
            "kind": "tv",
            "title": title,
            "original_title": str(result.get("jp_title") or "").strip(),
            "sort_title": title,
            "year": _year_from_date(pub_time),
            "overview": _clean_text(str(result.get("evaluate") or "")),
            "release_date": release_date,
            "episodes_count": _to_int(result.get("total")) or len(episodes),
            "seasons_count": 1,
            "poster_url": _https_url(str(result.get("cover") or "")),
            "fanart_url": "",
            "genres": [],
            "countries": [],
            "directors": [],
            "writers": [],
            "actors": [],
            "rating": _to_float((result.get("rating") or {}).get("score")),
            "votes": None,
            "runtime": None,
            "episode_titles": episode_titles,
            "aliases": [str(result.get("jp_title") or "").strip()],
            "source_label": "哔哩哔哩",
            "raw_url": f"https://www.bilibili.com/bangumi/play/ss{item_id}",
        }
        self.db.cache_set(cache_key, meta)
        return meta

    # ------------------------------------------------------------------ TMDb
    def _tmdb_request(self, path: str, params: dict[str, Any] | None = None) -> dict:
        if not self.tmdb_key:
            raise ProviderError("尚未配置 TMDb API Key")
        query = dict(params or {})
        query["api_key"] = self.tmdb_key
        query.setdefault("language", self.language)
        url = f"{self.tmdb_base}{path}?{urllib.parse.urlencode(query)}"
        data = self.http.get_json(url, headers={"Accept": "application/json"})
        if not isinstance(data, dict):
            raise ProviderError("TMDb 返回格式异常")
        return data

    def tmdb_search(self, query: str, kind: str | None, year: int | None, limit: int = 12) -> list[dict]:
        if not self.tmdb_key:
            raise ProviderError("尚未配置 TMDb API Key")
        query = query.strip()
        if not query:
            return []
        cache_key = f"search:tmdb:{kind}:{year}:{query.lower()}"
        cached = self.db.cache_get(cache_key, CACHE_SEARCH_TTL)
        if cached is not None:
            return cached[:limit]

        kinds = [kind] if kind in ("movie", "tv") else ["movie", "tv"]
        results: list[dict] = []
        for item_kind in kinds:
            params: dict[str, Any] = {"query": query, "include_adult": "false", "page": 1}
            if year:
                params["year" if item_kind == "movie" else "first_air_date_year"] = year
            data = self._tmdb_request(f"/search/{item_kind}", params)
            for item in (data.get("results") or [])[:limit]:
                title = item.get("title") if item_kind == "movie" else item.get("name")
                if not title:
                    continue
                date = item.get("release_date") if item_kind == "movie" else item.get("first_air_date")
                poster_path = item.get("poster_path")
                results.append(
                    {
                        "provider": "tmdb",
                        "id": str(item.get("id")),
                        "title": title,
                        "original_title": (item.get("original_title") or item.get("original_name") or ""),
                        "year": _to_int(str(date or "")[:4]),
                        "kind": item_kind,
                        "rating": _to_float(item.get("vote_average")),
                        "votes": _to_int(item.get("vote_count")),
                        "poster_url": self._proxy_image(f"{self.tmdb_image_base}/w342{poster_path}") if poster_path else "",
                        "episode_count": 0,
                        "source_label": "TMDb",
                    }
                )
        results.sort(key=lambda x: (x.get("year") == year, x.get("votes") or 0), reverse=True)
        self.db.cache_set(cache_key, results)
        return results[:limit]

    def tmdb_detail(self, item_id: str, kind: str | None = None) -> dict:
        if not self.tmdb_key:
            raise ProviderError("尚未配置 TMDb API Key")
        cache_key = f"detail:tmdb:{kind}:{item_id}"
        cached = self.db.cache_get(cache_key, CACHE_DETAIL_TTL)
        if cached is not None:
            return cached

        kinds = [kind] if kind in ("movie", "tv") else ["movie", "tv"]
        last_error: Exception | None = None
        data: dict | None = None
        used_kind = kinds[0]
        for item_kind in kinds:
            try:
                data = self._tmdb_request(
                    f"/{item_kind}/{urllib.parse.quote(str(item_id))}",
                    {
                        "append_to_response": "credits,external_ids,images",
                        "include_image_language": "zh,en,null",
                    },
                )
                used_kind = item_kind
                break
            except ProviderError as exc:
                last_error = exc
        if data is None:
            raise last_error or ProviderError("TMDb 详情为空")

        title = data.get("title") if used_kind == "movie" else data.get("name")
        original = data.get("original_title") if used_kind == "movie" else data.get("original_name")
        date = data.get("release_date") if used_kind == "movie" else data.get("first_air_date")
        runtime = data.get("runtime") if used_kind == "movie" else ((data.get("episode_run_time") or [None])[0])
        genres = [g.get("name") for g in (data.get("genres") or []) if isinstance(g, dict) and g.get("name")]
        countries = [
            c.get("name") or c.get("iso_3166_1")
            for c in (data.get("production_countries") or [])
            if isinstance(c, dict)
        ]
        languages = [
            l.get("name") or l.get("iso_639_1")
            for l in (data.get("spoken_languages") or [])
            if isinstance(l, dict)
        ]
        credits = data.get("credits") or {}
        cast = []
        for index, person in enumerate((credits.get("cast") or [])[:24]):
            if not isinstance(person, dict):
                continue
            cast.append(
                {
                    "name": person.get("name") or "",
                    "role": person.get("character") or "",
                    "order": index,
                    "thumb": f"{self.tmdb_image_base}/w185{person['profile_path']}" if person.get("profile_path") else "",
                }
            )
        crew = credits.get("crew") or []
        directors = [p.get("name") for p in crew if isinstance(p, dict) and p.get("job") == "Director"]
        writers = [
            p.get("name")
            for p in crew
            if isinstance(p, dict) and p.get("job") in ("Screenplay", "Writer", "Story")
        ]
        # Prefer a Chinese/English poster, then any poster.
        images = data.get("images") or {}
        posters = images.get("posters") or []
        backdrop = data.get("backdrop_path")
        poster = data.get("poster_path")
        for image in posters:
            if image.get("iso_639_1") in ("zh", "en") or image.get("iso_639_1") is None:
                poster = image.get("file_path") or poster
                break

        meta = {
            "provider": "tmdb",
            "provider_id": str(item_id),
            "kind": used_kind,
            "title": str(title or "").strip(),
            "original_title": str(original or "").strip(),
            "sort_title": str(title or "").strip(),
            "year": _to_int(str(date or "")[:4]),
            "overview": str(data.get("overview") or "").strip(),
            "tagline": str(data.get("tagline") or "").strip(),
            "runtime": _to_int(runtime),
            "rating": _to_float(data.get("vote_average")),
            "votes": _to_int(data.get("vote_count")),
            "release_date": str(date or "").strip() or None,
            "genres": genres,
            "countries": countries,
            "languages": languages,
            "directors": directors,
            "writers": writers,
            "actors": cast,
            "poster_url": self._proxy_image(f"{self.tmdb_image_base}/w500{poster}") if poster else "",
            "fanart_url": self._proxy_image(f"{self.tmdb_image_base}/w1280{backdrop}") if backdrop else "",
            "tmdb_id": str(item_id),
            "imdb_id": (data.get("external_ids") or {}).get("imdb_id") or data.get("imdb_id"),
            "douban_id": None,
            "episodes_count": _to_int(data.get("number_of_episodes")) or 0,
            "seasons_count": _to_int(data.get("number_of_seasons")) or 1,
            "studios": [
                c.get("name")
                for c in (data.get("production_companies") or [])
                if isinstance(c, dict) and c.get("name")
            ],
            "aliases": [a.get("title") or a.get("name") for a in (data.get("alternative_titles", {}).get("titles") or [])],
            "source_label": "TMDb",
            "raw_url": f"https://www.themoviedb.org/{used_kind}/{item_id}",
        }
        self.db.cache_set(cache_key, meta)
        return meta

    # ------------------------------------------------------------------ facade
    def search(
        self,
        query: str,
        kind: str | None = None,
        year: int | None = None,
        provider: str | None = None,
        limit: int = 12,
    ) -> tuple[list[dict], list[str]]:
        requested = provider or str(self.settings.get("source", "auto"))
        if requested == "auto":
            order = []
            if self.settings.get("douban_enabled", True):
                order.append("douban")
            if kind != "movie" and self.settings.get("fankuhub_enabled", True):
                order.append("fankuhub")
            if kind != "movie" and self.settings.get("bilibili_enabled", True):
                order.append("bilibili")
            if self.settings.get("tmdb_enabled", False) and self.tmdb_key:
                order.append("tmdb")
            if not order:
                raise ProviderError("没有启用任何刮削源，请到设置中至少开启一个来源")
        else:
            order = [requested]

        labels = {
            "douban": "豆瓣",
            "fankuhub": "番库",
            "bilibili": "哔哩哔哩",
            "tmdb": "TMDb",
        }
        errors: list[str] = []
        for name in order:
            try:
                if name == "douban":
                    results = self.douban_search(query, kind, year, limit)
                elif name == "fankuhub":
                    results = self.fankuhub_search(query, kind, year, limit)
                elif name == "bilibili":
                    results = self.bilibili_search(query, kind, year, limit)
                elif name == "tmdb":
                    results = self.tmdb_search(query, kind, year, limit)
                else:
                    errors.append(f"未知刮削源：{name}")
                    continue
                if results:
                    return results, errors
            except ProviderError as exc:
                errors.append(f"{labels.get(name, name)}：{exc}")
            except Exception as exc:  # noqa: BLE001 - keep fallback alive
                errors.append(f"{labels.get(name, name)}：{str(exc)[:160]}")
        return [], errors

    def detail(self, provider: str, item_id: str, kind: str | None = None) -> dict:
        if provider == "douban":
            return self.douban_detail(item_id)
        if provider == "fankuhub":
            return self.fankuhub_detail(item_id, kind)
        if provider == "bilibili":
            return self.bilibili_detail(item_id, kind)
        if provider == "tmdb":
            return self.tmdb_detail(item_id, kind)
        raise ProviderError(f"未知刮削源：{provider}")


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _year_from_date(value: Any) -> int | None:
    match = re.search(r"((?:19|20)\d{2})", str(value or ""))
    return int(match.group(1)) if match else None


def _date_part(value: Any) -> str | None:
    match = re.search(r"((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", str(value or ""))
    if not match:
        year = _year_from_date(value)
        return f"{year}-01-01" if year else None
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _https_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://"):
        return "https://" + value[7:]
    return value


def _strip_tags(value: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", value or ""))


def _clean_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _unescape_rsc(html: str) -> str:
    return html.replace('\\"', '"').replace("\\\\", "\\")


def _extract_rsc_value(html: str, marker: str, opener: str) -> Any | None:
    text = _unescape_rsc(html)
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find(opener, index)
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
        return value
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _extract_fanku_items(html: str) -> list[dict] | None:
    value = _extract_rsc_value(html, '"initialData":{"items":', "[")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    # Some pages put the list in a serialized RSC chunk with an extra prefix.
    match = re.search(r'"items":\s*(\[)', _unescape_rsc(html))
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(_unescape_rsc(html)[match.start(1) :])
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _extract_fanku_detail(html: str) -> dict | None:
    value = _extract_rsc_value(html, '"detail":{"id":', "{")
    if isinstance(value, dict):
        return value
    value = _extract_rsc_value(html, '"detail":{', "{")
    return value if isinstance(value, dict) else None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(str(value).strip()), 2)
    except (TypeError, ValueError):
        return None


def _parse_duration(value: str) -> int | None:
    match = re.search(r"(\d{1,4})\s*(?:分钟|min|mins|minutes)?", str(value))
    return int(match.group(1)) if match else None


def _normalize_date(value: str) -> str | None:
    match = re.search(r"((?:19|20)\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", str(value))
    if not match:
        year = re.search(r"((?:19|20)\d{2})", str(value))
        return f"{year.group(1)}-01-01" if year else None
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _strip_html(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
