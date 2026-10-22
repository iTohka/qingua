from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from pathlib import Path

from qingua.core import App
from qingua.db import Database
from qingua.server import ApiHandler, _Server
from tmp import cleanup, workspace_temp


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = workspace_temp("qingua-api")
        cls.app = App(Database(cls.temp / "test.db"), data_root=cls.temp)
        handler = type("TestHandler", (ApiHandler,), {"app": cls.app})
        cls.server = _Server(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.db.close()
        cleanup(cls.temp)

    def get(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, body: dict | None = None):
        data = json.dumps(body or {}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health(self):
        data = self.get("/api/health")
        self.assertTrue(data["ok"])

    def test_demo_and_works(self):
        data = self.post("/api/demo")
        self.assertTrue(data["ok"])
        works = self.get("/api/works")
        self.assertGreaterEqual(len(works["items"]), 6)
        self.assertGreaterEqual(works["stats"]["episodes"], 10)

    def test_static_index(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as response:
            text = response.read().decode("utf-8")
        self.assertIn("轻刮", text)


if __name__ == "__main__":
    unittest.main()
