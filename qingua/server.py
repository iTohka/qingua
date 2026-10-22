from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sys
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .config import WEB_DIR
from .core import App, AppError
from .providers import ProviderError

MAX_BODY = 2 * 1024 * 1024


class ApiHandler(BaseHTTPRequestHandler):
    app: App
    server_version = "QingGua/" + __version__

    # ------------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("QINGUA_DEBUG"):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise AppError("请求内容过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("请求 JSON 格式错误") from exc
        if not isinstance(data, dict):
            raise AppError("请求必须是 JSON 对象")
        return data

    def _query(self) -> dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def _send_file(self, path: Path, *, download_name: str | None = None, cache: str = "no-cache") -> None:
        if not path.is_file():
            self._error("文件不存在", 404)
            return
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".svg":
            mime = "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        if download_name:
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{urllib.parse.quote(download_name)}",
            )
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ HTTP verbs
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET", head=True)

    def _dispatch(self, method: str, head: bool = False) -> None:
        path = self._path()
        try:
            if path.startswith("/api/"):
                self._api(method, path)
                return
            self._static(path, head=head)
        except AppError as exc:
            self._error(str(exc), 400)
        except ProviderError as exc:
            self._error(str(exc), 502)
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            if os.environ.get("QINGUA_DEBUG"):
                traceback.print_exc()
            self._error(f"内部错误：{str(exc)[:200]}", 500)

    # ------------------------------------------------------------------ static
    def _static(self, path: str, head: bool = False) -> None:
        if path in ("", "/"):
            path = "/index.html"
        name = path.lstrip("/")
        if not name or ".." in name or "/" in name or "\\" in name:
            self._error("Not found", 404)
            return
        target = (WEB_DIR / name).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._error("Not found", 404)
            return
        if not target.is_file():
            self._error("Not found", 404)
            return
        if head:
            data = target.read_bytes()
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return
        self._send_file(target, cache="no-cache" if name == "index.html" else "public, max-age=300")

    # ------------------------------------------------------------------ API
    def _api(self, method: str, path: str) -> None:
        app = self.app
        q = self._query()

        if method == "GET" and path == "/api/health":
            self._json({"ok": True, "version": __version__, "app": "QingGua"})
            return
        if method == "GET" and path == "/api/bootstrap":
            self._json(
                {
                    "ok": True,
                    "version": __version__,
                    "settings": app.db.public_settings(),
                    "stats": app.stats(),
                    "folders": app.db.folders(),
                    "series": app.series(),
                }
            )
            return
        if path == "/api/settings" and method in ("GET", "PUT", "POST"):
            if method == "GET":
                self._json({"ok": True, "settings": app.db.public_settings()})
                return
            body = self._body()
            if body.pop("clear_tmdb_key", False):
                app.db.clear_secret("tmdb_api_key")
            self._json({"ok": True, "settings": app.db.update_settings(body)})
            return

        if path == "/api/folders" and method == "GET":
            self._json({"ok": True, "folders": app.db.folders()})
            return
        if path == "/api/folders" and method == "POST":
            body = self._body()
            folder = app.add_folder(str(body.get("path") or ""))
            self._json({"ok": True, "folder": folder, "folders": app.db.folders()})
            return
        match = re.fullmatch(r"/api/folders/(\d+)", path)
        if match and method == "DELETE":
            app.remove_folder(int(match.group(1)))
            self._json({"ok": True, "folders": app.db.folders(), "stats": app.stats()})
            return
        match = re.fullmatch(r"/api/folders/(\d+)/scan", path)
        if match and method == "POST":
            result = app.scan(int(match.group(1)))
            self._json({"ok": True, **result})
            return
        if path == "/api/scan" and method == "POST":
            body = self._body()
            folder_id = int(body["folder_id"]) if body.get("folder_id") else None
            result = app.scan(folder_id)
            self._json({"ok": True, **result})
            return

        if path == "/api/series" and method == "GET":
            folder_id = int(q["folder_id"]) if q.get("folder_id") else None
            self._json({"ok": True, "series": app.series(folder_id)})
            return

        if path == "/api/works" and method == "GET":
            folder_id = int(q["folder_id"]) if q.get("folder_id") else None
            result = app.works(
                folder_id=folder_id,
                status=q.get("status"),
                kind=q.get("kind"),
                query=q.get("q"),
                series=q.get("series"),
            )
            self._json({"ok": True, **result})
            return

        match = re.fullmatch(r"/api/works/(\d+)", path)
        if match and method == "GET":
            self._json({"ok": True, "work": app.work(int(match.group(1)))})
            return
        if match and method == "PATCH":
            self._json({"ok": True, "work": app.set_manual(int(match.group(1)), self._body())})
            return
        if match and method == "DELETE":
            app.delete_work(int(match.group(1)))
            self._json({"ok": True, "stats": app.stats()})
            return

        match = re.fullmatch(r"/api/works/(\d+)/search", path)
        if match and method == "POST":
            body = self._body()
            result = app.search(
                int(match.group(1)),
                query=body.get("query"),
                year=int(body["year"]) if body.get("year") else None,
                kind=body.get("kind"),
                provider=body.get("provider"),
                season=int(body["season"]) if body.get("season") else None,
            )
            self._json({"ok": True, **result})
            return

        match = re.fullmatch(r"/api/works/(\d+)/apply", path)
        if match and method == "POST":
            body = self._body()
            result = app.apply(
                int(match.group(1)),
                provider=body.get("provider"),
                item_id=str(body["id"]) if body.get("id") is not None else None,
                overrides=body.get("overrides"),
                write_nfo=body.get("write_nfo"),
                download_images=body.get("download_images"),
            )
            self._json({"ok": True, **result})
            return

        match = re.fullmatch(r"/api/works/(\d+)/organize", path)
        if match and method == "POST":
            body = self._body()
            work_id = int(match.group(1))
            if body.get("dry_run", True):
                self._json({"ok": True, "plan": app.organize_plan(work_id)})
            else:
                self._json({"ok": True, **app.organize(work_id)})
            return

        match = re.fullmatch(r"/api/works/(\d+)/nfo", path)
        if match and method == "GET":
            text = app.nfo_text(int(match.group(1)))
            payload = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        match = re.fullmatch(r"/api/works/(\d+)/(poster|fanart)", path)
        if match and method == "GET":
            work_id, kind = int(match.group(1)), match.group(2)
            target = app.poster_file(work_id) if kind == "poster" else app.fanart_file(work_id)
            if not target:
                self._error("没有图片", 404)
                return
            self._send_file(target, cache="public, max-age=60")
            return

        if path == "/api/jobs/auto" and method == "POST":
            body = self._body()
            job = app.start_auto_job(
                folder_id=int(body["folder_id"]) if body.get("folder_id") else None,
                status=str(body.get("status") or "new"),
            )
            self._json({"ok": True, "job": job})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
        if match and method == "GET":
            job = app.db.get_job(match.group(1))
            if not job:
                self._error("任务不存在", 404)
                return
            self._json({"ok": True, "job": job})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/cancel", path)
        if match and method == "POST":
            app.db.request_cancel(match.group(1))
            self._json({"ok": True})
            return

        if path == "/api/demo" and method == "POST":
            self._json({"ok": True, **app.make_demo()})
            return
        if path == "/api/dialog/folder" and method == "POST":
            self._json({"ok": True, "path": app.choose_folder()})
            return
        if path == "/api/dialog/file" and method == "POST":
            self._json({"ok": True, "path": app.choose_image()})
            return
        if path == "/api/open" and method == "POST":
            body = self._body()
            app.open_path(int(body.get("work_id") or 0))
            self._json({"ok": True})
            return
        if path == "/api/history" and method == "GET":
            self._json({"ok": True, "history": app.recent_history()})
            return

        self._error("接口不存在", 404)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def find_port(host: str, start: int = 8787) -> int:
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def serve(app: App, host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> None:
    port = find_port(host, port)
    handler = type("BoundApiHandler", (ApiHandler,), {"app": app})
    httpd = _Server((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"\n  轻刮 QingGua {__version__}")
    print(f"  {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        httpd.server_close()
