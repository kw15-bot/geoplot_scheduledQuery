#!/usr/bin/env python3
"""news_collect.py のテスト(標準ライブラリのみ)。  実行: python dev/test_news_collect.py
fixture(dev/news_fixtures/): ccg_hjyw.html(合成。実ページの見出し・日時を再現)、ccg_wqzf.html(実ページのリンク部分)、
mna.xml(実RSSの先頭12件＋國防訊息の「聯合戰備警巡」1件。本文は省略)"""
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

    def items(self):
        return {i["id"]: i for i in self.state()["items"]}

    def test_ccg_parse(self):
        items = {i["id"]: i for i in N.parse_ccg((FX / "ccg_hjyw.html").read_text(encoding="utf-8"), "https://www.ccg.gov.cn/hjyw/")}
        self.assertEqual(set(items), {"ccg:20261008_3248", "ccg:20261008_3247", "ccg:20260930_3240", "ccg:20260929_3238",
                                      "ccg:20260704_3129"})      # 国际合作(gjhz)は拾わない
        self.assertEqual(items["ccg:20261008_3248"]["url"], "https://www.ccg.gov.cn/hjyw/202610/t20261008_3248.html")
        self.assertEqual(items["ccg:20261008_3248"]["published"], "2026-10-08T23:25:00")
        self.assertEqual(items["ccg:20260930_3240"]["title"], "中国海警位中国黄岩岛领海及周边区域执法巡查")   # title属性を優先(本文は省略形)

    def test_wqzf_parse_real_markup(self):
        items = {i["id"]: i for i in N.parse_ccg((FX / "ccg_wqzf.html").read_text(encoding="utf-8"), "https://www.ccg.gov.cn/wqzf/")}
        self.assertEqual(items["ccg:20261010_3249"]["title"], "中国海警舰艇编队10月10日在我钓鱼岛及其附属岛屿领海内维权巡航")
        self.assertEqual(items["ccg:20261010_3249"]["url"], "https://www.ccg.gov.cn/wqzf/202610/t20261010_3249.html")
        self.assertEqual(items["ccg:20260927_3235"]["url"], "https://www.ccg.gov.cn/hjyw/202609/t20260927_3235.html")  # 海警要闻からの転載
        self.assertNotIn("ccg:20250411_2657", items)

    def test_notify_rules(self):
        self.assertEqual(self.run_once(), 0)
        it = self.items()
        on = {k for k, v in it.items() if v["notify"]}
        self.assertEqual(on, {
            "ccg:20260929_3238",      # 海警要闻: 台湾岛以东…依法开展常态化执法巡查
            "ccg:20260704_3129",      # 海警要闻: 「依法」の無い言い方
            "ccg:20260902_3198",      # 维权执法だけに載った 台湾岛以东(3つの規則は両方の一覧に使う)
            "ccg:20261010_3249",      # 维权执法: 在我钓鱼岛
            "ccg:20260929_3239",      # 维权执法: 金门
        } | {k for k, v in it.items() if v["source"] == "mna" and "聯合戰備警巡" in v["title"]})
        self.assertFalse(it["ccg:20250406_2648"]["notify"])          # 「进入我钓鱼岛」は「在我钓鱼岛」ではない
        self.assertFalse(it["ccg:20261008_3248"]["notify"])
        self.assertEqual(it["ccg:20261010_3249"]["page"], "ccg_wqzf")
        self.assertEqual(it["ccg:20261010_3249"]["matched"], "在我钓鱼岛")
        mna_on = [v for v in it.values() if v["source"] == "mna" and v["notify"]]
        self.assertEqual(len(mna_on), 1)
        self.assertEqual(sum(v["source"] == "mna" for v in it.values()), 3)

    def test_mna_rss_only_category(self):
        items = N.parse_mna_rss((FX / "mna.xml").read_text(encoding="utf-8"), "國防訊息")
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0]["url"].startswith("https://mna.mnd.gov.tw/news/detail/?UserKey="))
        self.assertEqual(items[0]["published"], "2026-10-12T09:00:00+08:00")

    def test_no_rewrite_when_unchanged(self):
        self.assertEqual(self.run_once(), 0)
        first = (self.out / "news.json").read_text(encoding="utf-8")
        self.assertEqual(self.run_once(), 0)
        self.assertEqual((self.out / "news.json").read_text(encoding="utf-8"), first)   # 同じ日・新着なしなら書き換えない

    def test_failed_page_keeps_items(self):
        self.run_once()
        part = self.tmp / "fx"
        part.mkdir()
        shutil.copy(FX / "mna.xml", part / "mna.xml")
        shutil.copy(FX / "ccg_hjyw.html", part / "ccg_hjyw.html")
        (part / "ccg_wqzf.html").write_text("<html>maintenance</html>", encoding="utf-8")
        self.assertEqual(self.run_once(part), 0)
        s = self.state()
        self.assertTrue(self.items()["ccg:20261010_3249"]["notify"])          # 维权执法の前回分は残る
        self.assertIn("ccg_wqzf", s["sources"]["ccg"]["last_error"])
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(self.run_once(empty), 2)                              # 全部失敗なら 2


if __name__ == "__main__":
    unittest.main(verbosity=1)
