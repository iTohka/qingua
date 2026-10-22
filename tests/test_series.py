from __future__ import annotations

import unittest

from qingua.series import series_base, series_info


class SeriesTests(unittest.TestCase):
    def test_sequel_numbers_share_base(self):
        self.assertEqual(series_base("钢铁侠1"), "钢铁侠")
        self.assertEqual(series_base("钢铁侠2"), "钢铁侠")
        self.assertEqual(series_base("钢铁侠 3"), "钢铁侠")
        self.assertEqual(series_base("Iron Man 2"), "Iron Man")

    def test_series_groups_need_at_least_two(self):
        works = [
            {"id": 1, "title": "钢铁侠1"},
            {"id": 2, "title": "钢铁侠2"},
            {"id": 3, "title": "钢铁侠 3"},
            {"id": 4, "title": "流浪地球"},
        ]
        per_work, groups = series_info(works)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "钢铁侠")
        self.assertEqual(groups[0]["count"], 3)
        self.assertIn(1, per_work)
        self.assertNotIn(4, per_work)

    def test_season_suffix_is_not_a_series_number(self):
        self.assertEqual(series_base("Spy x Family Season 2"), "Spy x Family")


if __name__ == "__main__":
    unittest.main()
