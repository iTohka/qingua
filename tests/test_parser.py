from __future__ import annotations

import unittest
from pathlib import Path

from qingua.parser import clean_title, extract_year, parse_file, season_number_from_folder


class ParserTests(unittest.TestCase):
    def test_movie_with_year_and_release_tags(self):
        parsed = parse_file(Path("流浪地球.2019.1080p.BluRay.x264-GROUP.mkv"))
        self.assertEqual(parsed.kind, "movie")
        self.assertEqual(parsed.title, "流浪地球")
        self.assertEqual(parsed.year, 2019)

    def test_english_title_keeps_words(self):
        parsed = parse_file(Path("The.Matrix.1999.1080p.mkv"))
        self.assertEqual(parsed.title, "The Matrix")
        self.assertEqual(parsed.year, 1999)

    def test_sxxexx_and_season_folder(self):
        parsed = parse_file(Path("Show/Season 02/Show.S02E03.1080p.mkv"))
        self.assertEqual(parsed.kind, "tv")
        self.assertEqual(parsed.title, "Show")
        self.assertEqual(parsed.season, 2)
        self.assertEqual(parsed.episode, 3)
        self.assertEqual(parsed.root_path.name, "Show")

    def test_chinese_episode(self):
        parsed = parse_file(Path("剧名/第01集.mkv"))
        self.assertEqual(parsed.kind, "tv")
        self.assertEqual(parsed.title, "剧名")
        self.assertEqual(parsed.season, 1)
        self.assertEqual(parsed.episode, 1)

    def test_bare_chinese_number(self):
        parsed = parse_file(Path("剧名/Season 01/08.mkv"))
        self.assertEqual(parsed.kind, "tv")
        self.assertEqual(parsed.episode, 8)

    def test_bracketed_episode_and_title(self):
        parsed = parse_file(
            Path(
                "[Airota][Majo no Tabitabi][01][BDRip 1080p AVC AAC][CHS].mp4"
            )
        )
        self.assertEqual(parsed.kind, "tv")
        self.assertEqual(parsed.title, "Majo no Tabitabi")
        self.assertEqual(parsed.episode, 1)

    def test_bracketed_episode_respects_season_in_name(self):
        parsed = parse_file(
            Path("[Sakurato] Spy x Family Season 2 [01][HEVC-10bit 1080p AAC].mkv")
        )
        self.assertEqual(parsed.kind, "tv")
        self.assertEqual(parsed.season, 2)
        self.assertEqual(parsed.episode, 1)

    def test_clean_website_brackets(self):
        parsed = parse_file(Path("[电影天堂www.dytt89.com]疯狂动物城.2016.HD1080P.国语中字.mp4"))
        self.assertEqual(parsed.title, "疯狂动物城")
        self.assertEqual(parsed.year, 2016)

    def test_clean_title_does_not_eat_last_word(self):
        self.assertEqual(clean_title("The Matrix"), "The Matrix")
        self.assertEqual(clean_title("Dune Part Two"), "Dune Part Two")

    def test_year_extraction_ignores_resolution(self):
        self.assertEqual(extract_year("Movie.2160p.2024.mkv"), 2024)
        self.assertIsNone(extract_year("Movie.2160p.mkv"))

    def test_season_folder(self):
        self.assertEqual(season_number_from_folder("Season 3"), 3)
        self.assertEqual(season_number_from_folder("S02"), 2)
        self.assertEqual(season_number_from_folder("第二季"), 2)
        self.assertIsNone(season_number_from_folder("Specials"))


if __name__ == "__main__":
    unittest.main()
