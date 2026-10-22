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
        on = {k for k, v in it.items() if v["notify"] and v["source"] in ("ccg", "mna")}
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


    def test_jfjb_day(self):
        self.run_once()
        idx = json.loads((self.out / "jfjb" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(idx["newest"], "2026-10-22")
        self.assertEqual([p[:3] for p in idx["days"]["2026-10-22"]["pages"]], [["01", "要闻", 2], ["02", "要闻", 1]])  # 版の順に並べ直す
        day = json.loads((self.out / "jfjb" / "2026-10-22.json").read_text(encoding="utf-8"))
        a1, a2 = day["pages"][0]["articles"]
        self.assertEqual((a1["id"], a1["subtitle"], a1["author"]), ("jfjb:2026-10-22_988843", "——习近平主席引领人民军队弘扬伟大长征精神推进新时代强军事业", "■本报记者 费士廷"))
        self.assertEqual(a1["paragraphs"], ["长征，这一惊天动地的革命壮举。", "江西于都、广西全州。"])
        self.assertEqual(a2["paragraphs"], ["东部战区新闻发言人表示。", "第二行。"])              # <p> が無い本文は <br> で分ける
        self.assertIn("articleid=988843", a1["url"])
        self.assertEqual(a1["area"], [[3.9, 18.87], [49.32, 18.87], [49.32, 60.5], [3.9, 60.5]])           # 紙面上の位置(%)
        self.assertTrue(day["pages"][0]["img"].endswith("7b858d7c9810de8147d9277fcc5401c5.jpg"))         # 版面の画像(标准图)
        self.assertTrue(day["pages"][0]["img_large"].endswith("9fd40718698e7dcd46c281730cbe821a.jpg"))   # 拡大用(真图)
        b = day["pages"][1]["articles"][0]
        self.assertEqual(b["figures"], [{"src": "https://rmt-static-publish.81.cn/image/uploadpic/20261022/06fc46ef72ac9ae3c06dfb221f1ab461.jpg",
                                         "caption": "图为纪念活动现场。"}])
        self.assertEqual(b["paragraphs"], ["第一段。", "二、要求"])                                  # 写真の説明は本文に入れない
        st = self.state()
        self.assertEqual(st["sources"]["jfjb"]["days"]["2026-10-22"], {"articles": 3, "final": False, "v": N.JFJB_FORMAT})
        self.assertEqual(st["sources"]["jfjb"]["days"]["2026-10-21"], {"articles": 0, "final": True})  # 無い日(404)は読み直さない
        self.assertFalse([i for i in st["items"] if i["source"] == "jfjb"])                         # 通知の規則が無ければ items に入れない

    def test_jfjb_notify_rule(self):
        old = N.JFJB_NOTIFY
        N.JFJB_NOTIFY = [r"东部战区"]
        try:
            self.run_once()
        finally:
            N.JFJB_NOTIFY = old
        it = self.items()["jfjb:2026-10-22_988842"]
        self.assertEqual((it["notify"], it["matched"], it["section"], it["published"]), (True, "东部战区", "第01版：要闻", "2026-10-22"))

    def test_weibo(self):
        self.run_once()
        it = self.items()
        a, b, c = it["weibo:RnuVx14YM"], it["weibo:RnqZZlcES"], it["weibo:QzAAAAAAA"]
        self.assertEqual(a["published"], "2026-10-20T08:00:00+08:00")
        self.assertEqual(a["url"], "https://weibo.com/7483054836/RnuVx14YM")
        self.assertEqual(a["pics"], [{"src": "https://wx1.sinaimg.cn/large/006abc.jpg", "thumb": "https://wx1.sinaimg.cn/bmiddle/006abc.jpg"}])
        self.assertNotIn("\u200b", a["text"])
        self.assertFalse(a["notify"])                      # 「联合」があっても定時の投稿(#直击…#)は通知しない
        self.assertFalse(b["notify"])                      # #东部熄灯号# も
        self.assertEqual((c["notify"], c["matched"], c["video"]), (True, "演习代号(正义使命)", True))
        self.assertEqual(c["title"], "【东部战区新闻发言人发布】")
        self.assertEqual(c["pics"], [{"src": "https://wx3.sinaimg.cn/orj480/006cover.jpg",
                                      "thumb": "https://wx3.sinaimg.cn/orj480/006cover.jpg", "cover": True}])   # 動画は表紙を写真に
        self.assertEqual(N.weibo_pic_url(c["pics"][0]["src"]), "https://wx3.sinaimg.cn/mw690/006cover.jpg")
        st = self.state()["sources"]["weibo"]
        self.assertIsNone(st["auth_error_at"])

    def test_weibo_rules(self):
        # 過去の演習の最初の発表(東部戦区新聞発言人)はすべて当たる
        for t in ["东部战区新闻发言人施毅陆军大校表示，8月2日晚开始，中国人民解放军东部战区将在台岛周边开展一系列联合军事行动",
                  "东部战区新闻发言人施毅陆军大校表示，4月1日开始，中国人民解放军东部战区组织陆军、海军、空军、火箭军等兵力，位台岛周边组织舰机多向抵近台岛",
                  "东部战区新闻发言人施毅陆军大校表示，4月2日，中国人民解放军东部战区位台湾海峡中部、南部相关海域组织“海峡雷霆-2025A”演练",
                  "东部战区新闻发言人李熹海军大校表示，5月23日至24日，…位台岛周边开展“联合利剑—2024A”演习"]:
            self.assertIsNotNone(N.weibo_notify(t), t)
        # 2026-10-21〜22 に央视军事で誤って当たっていた投稿は当たらない
        for t in ["【#大八轮战车亮相中巴联合训练#】中国和巴基斯坦“喀喇昆仑-2026”陆军联合训练转入分段演练阶段",
                  "【航行警告！#东海海域军事演习#】据中国海事局网站消息，宁波海事局发布航行警告",
                  "#AI长征新唱三军会师尽开颜#【#AI歌曲长征新唱#第14集】1936年10月，红一、红二、红四方面军"]:
            self.assertIsNone(N.weibo_notify(t), t)

    def test_weibo_auth_error(self):
        state = {"sources": {}, "items": []}
        def deny(url):
            raise N.WeiboAuthError("HTTP 403")
        added, err = N.collect_weibo(state, "2026-10-22T01:00:00+00:00", get=deny, cookie="SUB=x")
        self.assertEqual(added, 0)
        self.assertIn("cookie", err)
        self.assertEqual(state["sources"]["weibo"]["auth_error_at"], "2026-10-22")
        _, err = N.collect_weibo(state, "2026-10-22T01:00:00+00:00", cookie="")   # Secret が無い
        self.assertIn("WEIBO_COOKIE", err)

    def test_weibo_pics(self):
        import io, tempfile
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (1000, 500), "red").save(buf, "JPEG")
        seen = []
        def get_bytes(url, referer=None):
            seen.append((url, referer))
            if "bad" in url:
                raise OSError("HTTP 403")
            return buf.getvalue(), "image/jpeg"
        now = "2026-10-22T01:00:00+00:00"
        mk = lambda mid, pub, pics: {"id": f"weibo:{mid}", "source": "weibo", "mid": mid, "published": pub, "pics": pics}
        state = {"sources": {}, "items": [
            mk("A1", "2026-10-21T08:00:00+08:00", [{"src": "https://wx1.sinaimg.cn/large/x1.jpg"}]),
            mk("B2", "2026-10-20T08:00:00+08:00", [{"src": "https://wx1.sinaimg.cn/large/bad.jpg"}]),
            mk("C3", "2026-07-01T08:00:00+08:00", [{"src": "https://wx1.sinaimg.cn/large/x3.jpg"}])]}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            r = N.collect_weibo_pics(state, out, now, get_bytes=get_bytes)
            a, b, c = state["items"]
            self.assertEqual((r["saved"], len(r["errors"])), (1, 1))
            self.assertEqual(seen[0], ("https://wx1.sinaimg.cn/mw690/x1.jpg", "https://weibo.com/"))
            saved = json.loads((out / a["pics_file"]).read_text(encoding="utf-8"))
            self.assertTrue(saved["pics"][0].startswith("data:image/jpeg;base64,"))
            self.assertEqual((b.get("pics_file"), b["pics_tries"]), (None, 1))
            self.assertNotIn("pics_file", c)                  # WEIBO_PICS_KEEP_DAYS(30日)より前の投稿は保存しない
            a["published"] = "2026-08-01T08:00:00+08:00"       # 古くなったら消す
            r = N.collect_weibo_pics(state, out, now, get_bytes=get_bytes)
            self.assertEqual(r["removed"], 1)
            self.assertNotIn("pics_file", a)
            self.assertEqual(list((out / N.WEIBO_PICS_DIR).glob("*.json")), [])


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
