from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from qingua.nfo import build_episode_nfo, build_movie_nfo, build_tvshow_nfo


class NfoTests(unittest.TestCase):
    def setUp(self):
        self.meta = {
            "title": "流浪地球",
            "original_title": "The Wandering Earth",
            "year": 2019,
            "overview": "太阳即将毁灭。",
            "rating": 7.9,
            "votes": 100,
            "runtime": 125,
            "release_date": "2019-02-05",
            "genres": ["科幻", "冒险"],
            "countries": ["中国大陆"],
            "directors": ["郭帆"],
            "actors": [{"name": "吴京", "role": "刘培强", "order": 0}],
            "provider": "douban",
            "douban_id": "26266893",
        }

    def parse(self, text: str) -> ET.Element:
        self.assertTrue(text.startswith("<?xml"))
        return ET.fromstring(text.split("\n", 1)[1])

    def test_movie_nfo(self):
        root = self.parse(build_movie_nfo(self.meta, "poster.jpg", "fanart.jpg"))
        self.assertEqual(root.tag, "movie")
        self.assertEqual(root.findtext("title"), "流浪地球")
        self.assertEqual(root.findtext("year"), "2019")
        self.assertEqual(root.findtext("runtime"), "125")
        self.assertEqual(root.findtext("uniqueid"), "26266893")
        self.assertEqual(root.find("thumb").text, "poster.jpg")
        self.assertEqual(len(root.findall("genre")), 2)

    def test_tvshow_nfo(self):
        self.meta["title"] = "三体"
        root = self.parse(build_tvshow_nfo(self.meta, "poster.jpg", ""))
        self.assertEqual(root.tag, "tvshow")
        self.assertEqual(root.findtext("title"), "三体")

    def test_episode_nfo(self):
        root = self.parse(build_episode_nfo(self.meta, 1, 2, "第二集"))
        self.assertEqual(root.tag, "episodedetails")
        self.assertEqual(root.findtext("season"), "1")
        self.assertEqual(root.findtext("episode"), "2")
        self.assertEqual(root.findtext("title"), "第二集")
        self.assertEqual(root.findtext("showtitle"), "流浪地球")


if __name__ == "__main__":
    unittest.main()
