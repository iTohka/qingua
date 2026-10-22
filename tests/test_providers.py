from __future__ import annotations

import unittest

from qingua.matching import score_candidate
from qingua.providers import _extract_fanku_detail, _extract_fanku_items


class ProviderParserTests(unittest.TestCase):
    def test_fanku_search_payload(self):
        html = (
            '<script>self.__next_f.push([1,"xx\\"initialData\\":{\\"items\\":['
            '{\\"id\\":\\"a1\\",\\"titleZh\\":\\"猫与龙\\",\\"titleRomaji\\":\\"Neko to Ryuu\\",'
            '\\"airDate\\":\\"2026-06-27T00:00:00.000Z\\",\\"episodes\\":12,'
            '\\"coverUrl\\":\\"https://assets.fankuhub.cn/a.webp\\"}]}"])</script>'
        )
        items = _extract_fanku_items(html)
        self.assertIsNotNone(items)
        self.assertEqual(items[0]["titleZh"], "猫与龙")
        self.assertEqual(items[0]["episodes"], 12)

    def test_fanku_detail_payload(self):
        html = (
            '<script>self.__next_f.push([1,"xx\\"detail\\":{\\"id\\":\\"a1\\",'
            '\\"titleZh\\":\\"猫与龙\\",\\"synopsis\\":\\"一只龙和猫的故事。\\",'
            '\\"airDate\\":\\"2026-06-27T00:00:00.000Z\\",\\"episodes\\":12,'
            '\\"coverUrl\\":\\"https://assets.fankuhub.cn/a.webp\\"}"])</script>'
        )
        detail = _extract_fanku_detail(html)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["titleZh"], "猫与龙")
        self.assertEqual(detail["episodes"], 12)

    def test_alias_helps_matching(self):
        candidate = {
            "title": "猫与龙",
            "original_title": "猫と竜",
            "aliases": ["Neko to Ryuu", "The Cat and the Dragon"],
            "year": 2026,
            "kind": "tv",
        }
        self.assertGreaterEqual(score_candidate(candidate, "Neko to Ryuu", None, "tv"), 80)


if __name__ == "__main__":
    unittest.main()
