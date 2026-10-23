"""webpush.py のテスト(暗号化は RFC 8291 の例、署名は公開鍵で確かめる。新着の数え方はビューアの🔔と同じ規則)。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import webpush as W  # noqa: E402


class WebPushTest(unittest.TestCase):
    def test_rfc8291_example(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        asp = ec.derive_private_key(int.from_bytes(W.b64u_dec("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
        out = W.encrypt_payload(b"When I grow up, I want to be a watermelon",
                                "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                                "BTBZMqHH6r4Tts7J_aSIgg", as_private=asp, salt=W.b64u_dec("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(W.b64u(out), "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6"
                                      "TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_vapid_signature(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        v = W.keygen()
        h = W.vapid_auth("https://web.push.apple.com/abc", v["private_pem"], v["public_key"], now=1_800_000_000)
        t = h.split("t=")[1].split(",")[0]
        a, b, c = t.split(".")
        self.assertTrue(h.endswith("k=" + v["public_key"]))
        self.assertEqual(json.loads(W.b64u_dec(b))["aud"], "https://web.push.apple.com")
        sig = W.b64u_dec(c)
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), W.b64u_dec(v["public_key"]))
        pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                   f"{a}.{b}".encode(), ec.ECDSA(hashes.SHA256()))

    def test_events_and_pick_new(self):
        poly = {"type": "Polygon", "coordinates": [[[120, 25], [121, 25], [121, 26], [120, 25]]]}
        nf = lambda **p: {"type": "Feature", "geometry": p.pop("geom", poly), "properties": {
            "geometry_source": "text-polygon", "classification": "INTL", "status": "active", "issuer": "ZBBBYNYX",
            "fir": "ZSHA", "number": "A1/26", "valid_start": "2026-10-23T00:00:00Z", **p}}
        notam = {"features": [
            nf(), nf(),                                              # 同じ番号+開始は1件
            nf(number="A2/26", classification="DOM"),                # DOM は除く
            nf(number="A3/26", geometry_source="qline-circle"),      # 図形が円は除く
            nf(number="A4/26", issuer="RCTPYNYX", raw_text="MIL EXER"),          # 台北は除く
            nf(number="A5/26", issuer="RCTPYNYX", raw_text="ROCKET LAUNCH BY CHINA"),   # BY CHINA は通知
            nf(number="A6/26", status="expired")]}
        self.assertEqual([k for k, _ in W.notam_events(notam)],
                         ["notam:ZBBBYNYX|A1/26|2026-10-23T00:00:00Z", "notam:RCTPYNYX|A5/26|2026-10-23T00:00:00Z"])
        msa = {"features": [{"properties": {"url": "https://msa/x?t=1", "title": "军事演习"}},
                            {"properties": {"url": "https://msa/x?t=2", "title": "军事演习"}}]}
        self.assertEqual(len(W.msa_events(msa)), 1)
        news = {"items": [{"id": "a", "title": "演习", "notify": True, "source": "weibo", "published": "2026-10-23T08:00:00+08:00"},
                          {"id": "b", "title": "x", "notify": False, "source": "weibo"},
                          {"id": "c", "title": "古い", "notify": True, "backfilled": True, "source": "mna"}]}
        self.assertEqual([k for k, _ in W.news_events(news)], ["news:a"])

        state = {}
        ev = {"news": W.news_events(news), "notam": W.notam_events(notam), "msa": W.msa_events(msa)}
        self.assertEqual(W.pick_new(ev, state), [])                 # 初めては送らない(今ある分を送ったことにする)
        news["items"].insert(0, {"id": "d", "title": "东部战区新闻发言人发布", "notify": True, "source": "weibo",
                                 "published": "2026-10-23T09:00:00+08:00"})
        ev = {"news": W.news_events(news), "notam": None, "msa": W.msa_events(msa)}   # NOTAM が読めなかった回
        new = W.pick_new(ev, state)
        self.assertEqual([k for k, _ in new], ["news:d"])
        self.assertIn("notam", state["sent"])                       # 読めなかった種類は前のまま
        p = W.build_payload(new)
        self.assertEqual((p["title"], p["body"]), ("警報現況表示", "新しい報道"))
        mix = W.build_payload([("news:a", "x"), ("msa:https://m/1", "y"), ("notam:ZBBBYNYX|A1/26|x", "z")])
        self.assertEqual(mix["body"], "新しいNOTAM / 航行警報 / 報道")

    def test_send_all_drops_gone(self):
        v = W.keygen()
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        ua = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        sub = lambda e: {"endpoint": e, "keys": {"p256dh": W.b64u(ua), "auth": W.b64u(b"0123456789abcdef")}}
        codes = {"https://a.example/1": 201, "https://b.example/2": 410}
        keep, ok = W.send_all({"title": "t"}, v, [sub(e) for e in codes], post=lambda url, body, headers: codes[url])
        self.assertEqual(([s["endpoint"] for s in keep], ok), (["https://a.example/1"], 1))


if __name__ == "__main__":
    unittest.main()
