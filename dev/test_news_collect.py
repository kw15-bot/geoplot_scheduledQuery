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
        # fixture の投稿(2026-10-04〜08)が「見つけた時点で古い投稿」(is_old_post)にならないよう、時計を固定する
        self._now_iso, N.now_iso = N.now_iso, lambda: "2026-10-09T00:00:00+00:00"

    def tearDown(self):
        N.now_iso = self._now_iso
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
            "ccg:20261010_3249",      # 维权执法: 在我钓鱼岛
            "ccg:20260929_3239",      # 维权执法: 金门
        } | {k for k, v in it.items() if v["source"] == "mna" and "聯合戰備警巡" in v["title"]})
        # 「台湾岛以东…常态化执法巡查」は 2026-10-21 に通知の対象から外した
        for k in ("ccg:20260929_3238", "ccg:20260704_3129", "ccg:20260902_3198"):
            self.assertFalse(it[k]["notify"], k)
        self.assertNotIn("ccg:20250406_2648", it)                    # 横の欄(日時なし)の記事は拾わない(2026-10-20)
        self.assertFalse(it["ccg:20261008_3248"]["notify"])
        self.assertEqual(it["ccg:20261010_3249"]["page"], "ccg_wqzf")
        self.assertEqual(it["ccg:20261010_3249"]["matched"], "在我钓鱼岛")
        mna_on = [v for v in it.values() if v["source"] == "mna" and v["notify"]]
        self.assertEqual(len(mna_on), 1)
        self.assertEqual(sum(v["source"] == "mna" for v in it.values()), 3)

    def test_notify_recomputed_for_saved_items(self):
        """保存済みの記事も、毎回いまの規則で通知の印を付け直す(規則から外した語句の記事は印が消える)"""
        self.run_once()
        st = json.loads((self.out / "news.json").read_text(encoding="utf-8"))
        for i in st["items"]:
            if i["id"] == "ccg:20260929_3238":
                i["notify"], i["matched"] = True, "中国台湾岛以东海域依法开展常态化执法巡查"
        (self.out / "news.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        self.run_once()
        self.assertFalse(self.items()["ccg:20260929_3238"]["notify"])

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

    def test_mna_list_parse(self):
        items = N.parse_mna_list((FX / "mna_list.html").read_text(encoding="utf-8"))
        self.assertEqual([i["published"] for i in items], ["2026-10-04", "2026-09-30"])   # 民國115年 → 2026年
        self.assertEqual(items[1]["title"], "中共假「聯合戰備警巡」之名擾臺 國軍嚴密監控適切應處")   # 全角空白は半角に(RSSと同じ)
        self.assertEqual(items[1]["url"], "https://mna.mnd.gov.tw/news/detail/?UserKey=ab7cc840-211f-4c09-a1fe-a973c7e745ad")
        self.assertTrue(items[0]["summary"].startswith("（軍聞社記者侯凱議臺北4日電）"))

    def test_import_json_is_backfilled_and_keeps_all(self):
        self.run_once()
        rows = [{"title": "中国海警舰艇编队1月7日在我钓鱼岛领海巡航", "date": "2024-01-07 10:00:00",
                 "url": "https://www.ccg.gov.cn/wqzf/202401/t20240107_1000.html"},
                {"title": "福建海警位金门附近海域开展执法巡查", "url": "https://www.ccg.gov.cn/hjyw/202312/t20231201_900.html"},
                {"title": "リンクが記事でない", "url": "https://www.ccg.gov.cn/gjhz/"}]
        f = self.tmp / "rows.json"
        f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(N.main(["--out-dir", str(self.out), "--import-json", str(f)]), 0)
        it = self.items()
        a, b = it["ccg:20240107_1000"], it["ccg:20231201_900"]
        self.assertTrue(a["backfilled"] and a["notify"] and a["matched"] == "在我钓鱼岛")
        self.assertEqual((a["page"], a["published"]), ("ccg_wqzf", "2024-01-07T10:00:00"))
        self.assertEqual((b["page"], b["published"], b["matched"]), ("ccg_hjyw", "2023-12-01", "金门"))
        self.assertFalse(it["ccg:20261008_3248"].get("backfilled"))      # 普段の収集で入った分はそのまま
        self.run_once()                                                     # 普段の収集の後も消えない(全件を蓄積)
        self.assertIn("ccg:20231201_900", self.items())

    def test_old_post_found_later_is_backfilled(self):
        N.now_iso = lambda: "2026-10-21T00:00:00+00:00"                   # 10/04〜08 の投稿を 10/21 に初めて見つけた
        self.run_once()
        it = self.items()
        self.assertTrue(it["ccg:20261008_3248"].get("backfilled"))        # 7日より前 → 通知しない
        self.assertTrue(N.is_old_post({"published": "2026-10-13"}, "2026-10-21T00:00:00Z"))
        self.assertFalse(N.is_old_post({"published": "2026-10-14"}, "2026-10-21T00:00:00Z"))



class ArticleTest(unittest.TestCase):
    def test_parse_mna_article(self):
        a = N.parse_mna_article((FX / "mna_article.html").read_text(encoding="utf-8"),
                                "https://mna.mnd.gov.tw/news/detail/?UserKey=f5441bd9")
        self.assertEqual(a["title"], "中共解放軍臺海周邊活動 國軍嚴密監控應處")
        self.assertEqual(a["category"], "國防訊息")
        self.assertEqual(a["date"], "民國115年10月20日")
        self.assertEqual(len(a["figures"]), 1)                    # 拡大表示用の同じ写真・「你可能也會有興趣」の写真は入れない
        self.assertTrue(a["figures"][0]["src"].startswith("https://mna.mnd.gov.tw/Cms_Data/"))
        self.assertIn("示意圖", a["figures"][0]["caption"])
        self.assertEqual(len(a["paragraphs"]), 2)
        self.assertTrue(a["paragraphs"][0].startswith("（軍聞社記者"))
        self.assertEqual(a["keywords"], ["共機", "共艦"])

    def test_collect_articles(self):
        page = (FX / "mna_article.html").read_text(encoding="utf-8")
        st = {"items": [{"id": "mna:a1", "source": "mna", "url": "https://mna.mnd.gov.tw/news/detail/?UserKey=a1",
                         "published": "2026-10-20", "title": "x"},
                        {"id": "ccg:20261010_3249", "source": "ccg", "notify": True, "published": "2026-10-10T17:48",
                         "url": "https://www.ccg.gov.cn/wqzf/202610/t20261010_3249.html", "title": "y"}]}

        def fetch(url):
            if "ccg" in url:
                raise OSError("blocked")
            return page
        orig = N.image_data_uri
        N.image_data_uri = lambda url, referer=None: "data:image/jpeg;base64,AAAA"
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(N.collect_articles(st, Path(d), "2026-10-20T00:00:00+00:00", fetch=fetch), 1)
                art = json.loads((Path(d) / "articles" / "mna_a1.json").read_text(encoding="utf-8"))
        finally:
            N.image_data_uri = orig
        self.assertEqual(st["items"][0]["article"], "articles/mna_a1.json")
        self.assertEqual(art["figures"][0]["data"], "data:image/jpeg;base64,AAAA")
        self.assertEqual(st["items"][1]["article_tries"], 1)             # 読めなかった記事は回数を数える
        self.assertNotIn("article", st["items"][1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
