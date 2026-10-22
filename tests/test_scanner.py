from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from qingua.scanner import scan_tree
from tmp import workspace_temp


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp("qingua-scan")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def touch(self, relative: str) -> None:
        path = self.temp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    def test_groups_movie_and_tv(self):
        self.touch("Movies/流浪地球.2019.1080p.mkv")
        self.touch("Shows/三体/Season 01/三体.S01E01.mkv")
        self.touch("Shows/三体/Season 01/三体.S01E02.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 2)
        movie = next(g for g in groups if g.kind == "movie")
        show = next(g for g in groups if g.kind == "tv")
        self.assertEqual(movie.title, "流浪地球")
        self.assertEqual(show.title, "三体")
        self.assertEqual(show.episode_count, 2)
        self.assertEqual(show.season_count, 1)
        self.assertEqual(len(show.files), 2)

    def test_bare_episode_sequence(self):
        self.touch("Shows/漫长的季节/01.mkv")
        self.touch("Shows/漫长的季节/02.mkv")
        self.touch("Shows/漫长的季节/03.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 1)
        show = groups[0]
        self.assertEqual(show.kind, "tv")
        self.assertEqual(show.title, "漫长的季节")
        self.assertEqual([f.episode for f in show.files], [1, 2, 3])

    def test_skips_samples(self):
        self.touch("Movies/Film.2020.1080p.mkv")
        self.touch("Movies/Film.2020.sample.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].episode_count, 1)

    def test_sequential_episode_names_in_one_folder(self):
        folder = "Shows/[LoliHouse] Neko to Ryuu [01-12][WebRip 1080p HEVC-10bit AAC]"
        self.touch(f"{folder}/[LoliHouse] Neko to Ryuu - 01 [WebRip 1080p HEVC-10bit AAC SRTx2].mkv")
        self.touch(f"{folder}/[LoliHouse] Neko to Ryuu - 02 [WebRip 1080p HEVC-10bit AAC SRTx2].mkv")
        self.touch(f"{folder}/[LoliHouse] Neko to Ryuu - 03 [WebRip 1080p HEVC-10bit AAC SRTx2].mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 1)
        show = groups[0]
        self.assertEqual(show.kind, "tv")
        self.assertEqual(show.title, "Neko to Ryuu")
        self.assertEqual(show.episode_count, 3)
        self.assertEqual([f.episode for f in show.files], [1, 2, 3])

    def test_single_sequential_file_stays_movie(self):
        self.touch("Movies/Some Movie 01.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].kind, "movie")

    def test_similar_numeric_siblings_are_one_show(self):
        for index in range(1, 4):
            self.touch(f"Shows/Some Show {index:02d}.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].kind, "tv")
        self.assertEqual(groups[0].title, "Some Show")
        self.assertEqual([file.episode for file in groups[0].files], [1, 2, 3])

    def test_different_numeric_series_in_one_folder_stay_separate(self):
        for index in (1, 2):
            self.touch(f"Shows/Show A {index:02d}.mkv")
            self.touch(f"Shows/Show B {index:02d}.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 2)
        self.assertEqual({group.title for group in groups}, {"Show A", "Show B"})

    def test_part_numbers_are_not_episodes(self):
        self.touch("Movies/Part 1.mkv")
        self.touch("Movies/Part 2.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(group.kind == "movie" for group in groups))

    def test_nested_series_folder_uses_main_show_name(self):
        self.touch("Shows/EVA新世纪福音战士/旧TV+剧场版/01.mkv")
        self.touch("Shows/EVA新世纪福音战士/旧TV+剧场版/02.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 1)
        show = groups[0]
        self.assertEqual(show.kind, "tv")
        self.assertEqual(show.title, "EVA新世纪福音战士")
        self.assertEqual(show.root_path.name, "EVA新世纪福音战士")
        self.assertEqual([file.episode for file in show.files], [1, 2])

    def test_explicit_season_folders_become_separate_works(self):
        self.touch("Shows/one punch man/S1/One Punch Man - 01.mkv")
        self.touch("Shows/one punch man/S1/One Punch Man - 02.mkv")
        self.touch("Shows/one punch man/S2/One Punch Man - 01.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {group.title for group in groups},
            {"one punch man 第1季", "one punch man 第2季"},
        )
        self.assertEqual({group.root_path.name for group in groups}, {"S1", "S2"})

    def test_parent_folder_is_not_a_work_when_seasons_are_subfolders(self):
        self.touch("Shows/Shingeki no Kyojin/[Group] Shingeki no Kyojin/Shingeki - 01.mkv")
        self.touch("Shows/Shingeki no Kyojin/[Group] Shingeki no Kyojin/Shingeki - 02.mkv")
        self.touch("Shows/Shingeki no Kyojin/[Group] Shingeki no Kyojin Season 2/Shingeki - 01.mkv")
        self.touch("Shows/Shingeki no Kyojin/[Group] Shingeki no Kyojin The Final Season/Shingeki - 01.mkv")
        groups = scan_tree(self.temp)
        self.assertEqual(len(groups), 3)
        self.assertEqual(
            {group.title for group in groups},
            {
                "Shingeki no Kyojin 第1季",
                "Shingeki no Kyojin 第2季",
                "Shingeki no Kyojin 第3季",
            },
        )
        self.assertFalse(any(group.root_path.name == "Shingeki" for group in groups))


if __name__ == "__main__":
    unittest.main()
