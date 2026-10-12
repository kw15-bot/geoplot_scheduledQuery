#!/usr/bin/env python3
"""news_collect.py のテスト(標準ライブラリのみ)。  実行: python dev/test_news_collect.py
fixture: dev/news_fixtures/ccg.html(合成。実ページの見出し・日時を再現)、mna.xml(実RSSの先頭12件、本文は省略)"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import news_collect as N  # noqa: E402

FX = ROOT / "dev" / "news_fixtures"


class News(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_once(self, fx=FX):
        return N.main(["--out-dir", str(self.out), "--fixture-dir", str(fx)])

    def state(self):
        return json.loads((self.out / "news.json").read_text(encoding="utf-8"))

    def test_ccg_parse(self):
        items = {i["id"]: i for i in N.parse_ccg((FX / "ccg.html").read_text(encoding="utf-8"), "https://www.ccg.gov.cn/hjyw/")}
        self.assertEqual(set(items), {"ccg:20261008_3248", "ccg:20261008_3247", "ccg:20260930_3240", "ccg:20260929_3238"})
        self.assertEqual(items["ccg:20261008_3248"]["url"], "https://www.ccg.gov.cn/hjyw/202610/t20261008_3248.html")
        self.assertEqual(items["ccg:20261008_3248"]["published"], "2026-10-08T23:25:00")
        self.assertEqual(items["ccg:20260930_3240"]["title"], "中国海警位中国黄岩岛领海及周边区域执法巡查")   # title属性を優先(本文は省略形)

    def test_mna_rss_only_category(self):
        items = N.parse_mna_rss((FX / "mna.xml").read_text(encoding="utf-8"), "國防訊息")
        self.assertEqual(len(items), 2)
        self.assertTrue(all("臺海周邊活動" in i["title"] for i in items))
        self.assertTrue(items[0]["url"].startswith("https://mna.mnd.gov.tw/news/detail/?UserKey="))
        self.assertEqual(items[0]["published"], "2026-10-12T09:00:00+08:00")

    def test_new_items_and_no_rewrite_when_unchanged(self):
        self.assertEqual(self.run_once(), 0)
        s = self.state()
        self.assertEqual(len(s["items"]), 6)
        first = (self.out / "news.json").read_text(encoding="utf-8")
        self.assertEqual(self.run_once(), 0)
        self.assertEqual((self.out / "news.json").read_text(encoding="utf-8"), first)   # 同じ日・新着なしなら書き換えない

    def test_failed_source_keeps_items(self):
        self.run_once()
        part = self.tmp / "fx"
        part.mkdir()
        shutil.copy(FX / "mna.xml", part / "mna.xml")
        (part / "ccg.html").write_text("<html>maintenance</html>", encoding="utf-8")
        self.assertEqual(self.run_once(part), 0)                   # 片方成功なら 0
        s = self.state()
        self.assertEqual(sum(i["source"] == "ccg" for i in s["items"]), 4)    # 前回分は残る
        self.assertIn("記事を1件も", s["sources"]["ccg"]["last_error"])
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(self.run_once(empty), 2)                  # 全部失敗なら 2


if __name__ == "__main__":
    unittest.main(verbosity=1)
