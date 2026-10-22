from __future__ import annotations

import unittest
from pathlib import Path

from qingua.matching import pick_best, score_candidate, title_key


class MatchingTests(unittest.TestCase):
    def test_title_key_ignores_punctuation(self):
        self.assertEqual(title_key("The.Matrix (1999)"), "thematrix1999")

    def test_exact_title_with_year_is_confident(self):
        candidate = {"title": "流浪地球", "year": 2019, "kind": "movie", "rating": 7.9}
        self.assertGreaterEqual(score_candidate(candidate, "流浪地球", 2019, "movie"), 90)

    def test_exact_title_without_year_still_auto(self):
        candidate = {"title": "流浪地球", "year": None, "kind": "movie", "rating": 7.9}
        self.assertGreaterEqual(score_candidate(candidate, "流浪地球", None, "movie"), 80)

    def test_year_mismatch_is_penalized(self):
        candidate = {"title": "流浪地球", "year": 2023, "kind": "movie"}
        self.assertLess(score_candidate(candidate, "流浪地球", 2019, "movie"), 70)

    def test_pick_best_requires_margin(self):
        candidates = [
            {"title": "A", "year": 2020, "kind": "movie", "score": 95},
            {"title": "A", "year": 2020, "kind": "movie", "score": 93},
        ]
        best, confident = pick_best(candidates, 82)
        self.assertEqual(best["score"], 95)
        self.assertFalse(confident)

    def test_kind_mismatch(self):
        candidate = {"title": "三体", "year": 2023, "kind": "movie", "episode_count": 30}
        self.assertLess(score_candidate(candidate, "三体", 2023, "tv"), 80)

    def test_explicit_season_wins_over_first_season(self):
        first = {"title": "赛博朋克：边缘行者", "year": 2022, "kind": "tv"}
        second = {"title": "赛博朋克：边缘行者 2", "year": 2026, "kind": "tv"}
        self.assertGreater(
            score_candidate(second, "Cyberpunk Edgerunners", None, "tv", season=2),
            score_candidate(first, "Cyberpunk Edgerunners", None, "tv", season=2),
        )

    def test_episode_count_filters_wrong_season(self):
        season_one = {"title": "葬送的芙莉莲", "year": 2023, "kind": "tv", "episode_count": 28}
        season_two = {"title": "葬送的芙莉莲 第二季", "year": 2026, "kind": "tv", "episode_count": 10}
        self.assertGreater(
            score_candidate(season_one, "葬送的芙莉莲", None, "tv", season=1, episode_count=28),
            score_candidate(season_two, "葬送的芙莉莲", None, "tv", season=1, episode_count=28),
        )


if __name__ == "__main__":
    unittest.main()
