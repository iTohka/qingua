from __future__ import annotations

import time
import unittest
from pathlib import Path

from qingua.core import App, _season_query, _strip_season_suffix, _strip_tv_title
from qingua.db import Database
from qingua.organize import plan_organize
from tmp import cleanup, workspace_temp


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp("qingua-core")
        self.media = self.temp / "media"
        self.media.mkdir()
        self.app = App(Database(self.temp / "test.db"), data_root=self.temp)

    def tearDown(self):
        self.app.db.close()
        cleanup(self.temp)

    def touch(self, relative: str):
        path = self.media / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def test_scan_and_apply_tv(self):
        self.touch("三体/Season 01/三体.S01E01.mkv")
        self.touch("三体/Season 01/三体.S01E02.mkv")
        folder = self.app.add_folder(str(self.media))
        result = self.app.scan(folder["id"])
        self.assertEqual(result["works"], 1)
        self.assertEqual(result["files"], 2)
        work = self.app.works()["items"][0]
        self.assertEqual(work["kind"], "tv")
        self.assertEqual(work["episode_count"], 2)

        detail = self.app.work(work["id"])
        meta = {
            "title": "三体",
            "original_title": "Three-Body",
            "year": 2023,
            "kind": "tv",
            "overview": "测试简介",
            "rating": 8.7,
            "release_date": "2023-01-15",
            "genres": ["剧情", "科幻"],
            "countries": ["中国大陆"],
            "provider": "manual",
            "provider_id": "1",
        }
        applied = self.app._apply_meta(detail, meta, write_nfo=True, download_images=False)
        self.assertEqual(applied["work"]["status"], "applied")
        self.assertTrue((self.media / "三体" / "tvshow.nfo").is_file())
        self.assertTrue((self.media / "三体" / "Season 01" / "三体.S01E01.nfo").is_file())

        organized = plan_organize(applied["work"], meta)
        self.assertGreaterEqual(organized["move_count"], 2)

    def test_manual_update(self):
        self.touch("Movie.2020.1080p.mkv")
        folder = self.app.add_folder(str(self.media))
        self.app.scan(folder["id"])
        work = self.app.works()["items"][0]
        updated = self.app.set_manual(work["id"], {"title": "改名", "year": 2021})
        self.assertEqual(updated["title"], "改名")
        self.assertEqual(updated["year"], 2021)
        self.assertEqual(updated["status"], "matched")

    def test_scan_removes_missing_files(self):
        path = self.touch("Show.S01E01.mkv")
        folder = self.app.add_folder(str(self.media))
        self.app.scan(folder["id"])
        self.assertEqual(len(self.app.works()["items"]), 1)
        path.unlink()
        self.app.scan(folder["id"])
        self.assertEqual(len(self.app.works()["items"]), 0)

    def test_rescan_regroups_old_movie_rows_into_tv(self):
        first = self.touch("[LoliHouse] Neko to Ryuu - 01 [WebRip 1080p].mkv")
        second = self.touch("[LoliHouse] Neko to Ryuu - 02 [WebRip 1080p].mkv")
        folder = self.app.add_folder(str(self.media))
        # Simulate an older scan that treated each file as a separate movie.
        for path in (first, second):
            work_id = self.app.db.create_work(
                folder["id"], "movie", str(path), path.stem, None, 1, 1, 60
            )
            self.app.db.replace_files(
                work_id,
                [{"path": str(path), "season": None, "episode": None, "size": 0, "mtime": 0}],
            )
        result = self.app.scan(folder["id"])
        self.assertEqual(result["works"], 1)
        items = self.app.works()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "tv")
        self.assertEqual(items[0]["episode_count"], 2)

    def test_manual_title_and_local_cover_are_written_to_nfo(self):
        self.touch("Movie.2020.1080p.mkv")
        folder = self.app.add_folder(str(self.media))
        self.app.scan(folder["id"])
        work = self.app.works()["items"][0]
        cover = self.temp / "cover.png"
        cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048)
        updated = self.app.set_manual(
            work["id"],
            {"title": "手动标题", "poster_url": str(cover), "overview": "手动简介"},
        )
        applied = self.app._apply_meta(
            updated, updated["meta"], write_nfo=True, download_images=True
        )
        nfo = Path(applied["work"]["nfo_path"])
        text = nfo.read_text(encoding="utf-8")
        self.assertIn("<title>手动标题</title>", text)
        self.assertIn("poster.png", text)
        self.assertTrue(Path(applied["work"]["poster_path"]).is_file())

        # Applying an edited title again must overwrite the existing NFO even
        # though the global overwrite option is off.
        self.app.set_manual(work["id"], {"title": "第二次修改"})
        second = self.app.apply(work["id"])
        second_text = Path(second["work"]["nfo_path"]).read_text(encoding="utf-8")
        self.assertIn("<title>第二次修改</title>", second_text)


    def test_season_nfo_is_written_inside_season_folder(self):
        self.touch("Shows/one punch man/S1/One Punch Man - 01.mkv")
        self.touch("Shows/one punch man/S1/One Punch Man - 02.mkv")
        self.touch("Shows/one punch man/S2/One Punch Man - 01.mkv")
        folder = self.app.add_folder(str(self.media))
        self.app.scan(folder["id"])
        works = self.app.works()["items"]
        self.assertEqual(len(works), 2)
        season_work = next(work for work in works if work["title"].endswith("第1季"))
        detail = self.app.work(season_work["id"])
        meta = {
            "title": "一拳超人",
            "year": 2015,
            "kind": "tv",
            "overview": "测试",
            "provider": "manual",
            "provider_id": "1",
        }
        applied = self.app._apply_meta(detail, meta, write_nfo=True, download_images=False)
        self.assertTrue((self.media / "Shows" / "one punch man" / "S1" / "season.nfo").is_file())
        self.assertIn("第1季", applied["work"]["title"])

    def test_series_title_helpers(self):
        self.assertEqual(_strip_season_suffix("赛博朋克：边缘行者 第一季"), "赛博朋克：边缘行者")
        self.assertEqual(_strip_season_suffix("Cyberpunk: Edgerunners Season 2"), "Cyberpunk: Edgerunners")
        self.assertEqual(_season_query("赛博朋克：边缘行者", 2), "赛博朋克：边缘行者 第2季")
        self.assertEqual(_season_query("赛博朋克：边缘行者", 1), "赛博朋克：边缘行者")
        self.assertEqual(_strip_tv_title("赛博朋克：边缘行者 2", 2), "赛博朋克：边缘行者")

    def test_multi_season_work_requires_manual_confirmation(self):
        self.touch("Show/Show.S01E01.mkv")
        self.touch("Show/Show.S02E01.mkv")
        folder = self.app.add_folder(str(self.media))
        self.app.scan(folder["id"])
        work = self.app.works()["items"][0]
        detail = self.app.work(work["id"])
        self.assertEqual(detail["seasons"], [1, 2])
        self.assertIsNone(detail["season"])
        job = self.app.start_auto_job(status="new")
        for _ in range(40):
            time.sleep(0.1)
            current = self.app.db.get_job(job["id"])
            if current["status"] in ("done", "cancelled"):
                break
        detail = self.app.work(work["id"])
        self.assertEqual(detail["status"], "review")
        self.assertIn("多季", detail["error"] or "")


if __name__ == "__main__":
    unittest.main()
