"""Unit tests for the download target directory rules (no network, no downloads)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from limefun.download_manager import DownloadManager


class DownloadTargetDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="limefun-layout-")
        self.root = Path(self._temporary.name)
        self.manager = DownloadManager.__new__(DownloadManager)
        self.manager.output_root = self.root
        self.manager.local_roots = [self.root]
        self.manager.directory_aliases = {}

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def touch(self, relative: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def target(self, title: str) -> str:
        return self.manager._target_directory(title).relative_to(self.root).as_posix()

    def test_new_series_uses_base_and_suffix(self) -> None:
        self.assertEqual(self.target("NewAnime 第二季"), "NewAnime/第二季")
        self.assertEqual(self.target("NewAnime 第1部分"), "NewAnime/第1部分")
        self.assertEqual(self.target("NewAnime 第一季"), "NewAnime")
        self.assertEqual(self.target("剧场版 NewAnime 副标题"), "NewAnime/剧场版 副标题")
        self.assertEqual(self.target("NewAnime OAD"), "NewAnime/OAD")

    def test_existing_layout_is_reused(self) -> None:
        self.touch("入间同学入魔了/入间同学入魔了 第四季/第1集.mp4")
        self.touch("骸骨骑士大人异世界冒险中/第一季/第1集.mp4")
        self.touch("骸骨骑士大人异世界冒险中/第二季/第1集.mp4")
        self.touch("Re：从零开始的异世界生活/第四季 夺还篇/第78集.mp4")
        self.touch("进击的巨人/进击的巨人 第一季/第1集.mp4")
        self.touch("进击的巨人/进击的巨人 第三季/第1集.mp4")
        self.touch("星之梦/正片/第1集.mp4")
        self.assertEqual(self.target("入间同学入魔了 第四季"), "入间同学入魔了/入间同学入魔了 第四季")
        self.assertEqual(self.target("骸骨骑士大人异世界冒险中 第二季"), "骸骨骑士大人异世界冒险中/第二季")
        self.assertEqual(self.target("Re：从零开始的异世界生活 第四季 夺还篇"), "Re：从零开始的异世界生活/第四季 夺还篇")
        self.assertEqual(self.target("进击的巨人 第三季"), "进击的巨人/进击的巨人 第三季")
        self.assertEqual(self.target("进击的巨人 第一季"), "进击的巨人/进击的巨人 第一季")
        self.assertEqual(self.target("星之梦 第一季"), "星之梦/正片")

    def test_first_season_flat_base_and_new_suffix(self) -> None:
        self.touch("葬送的芙莉莲/第1集.mp4")
        self.touch("葬送的芙莉莲/葬送的芙莉莲 第二季/第1集.mp4")
        self.assertEqual(self.target("葬送的芙莉莲 第一季"), "葬送的芙莉莲")
        self.assertEqual(self.target("葬送的芙莉莲 第二季"), "葬送的芙莉莲/葬送的芙莉莲 第二季")
        self.assertEqual(self.target("葬送的芙莉莲 第三季"), "葬送的芙莉莲/第三季")

    def test_legacy_full_title_directory_is_reused(self) -> None:
        self.touch("剧场版 链锯人 蕾塞篇/电影.mp4")
        self.touch("链锯人/第1集.mp4")
        self.assertEqual(self.target("剧场版 链锯人 蕾塞篇"), "剧场版 链锯人 蕾塞篇")
        self.assertEqual(self.target("剧场版 链锯人 第二部"), "链锯人/剧场版 第二部")

    def test_season_parts_do_not_cross_match(self) -> None:
        self.touch("Re：从零开始的异世界生活/第四季 丧失篇/第1集.mp4")
        self.assertEqual(
            self.target("Re：从零开始的异世界生活 第四季 夺还篇"),
            "Re：从零开始的异世界生活/第四季 夺还篇",
        )


if __name__ == "__main__":
    unittest.main()
