#!/usr/bin/env python3
"""data_crypt.py のテスト(暗号化の形 v2)。  実行: python dev/test_data_crypt.py(cryptography が要る)"""
import base64
import gzip
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_crypt as D  # noqa: E402

KEY = "test-key"


class V2(unittest.TestCase):
    def setUp(self):
        self.data = "".join(f'{{"id": "A{i:05d}/26", "text": "{hashlib.sha256(str(i).encode()).hexdigest() * 3}"}},\n'
                            for i in range(4000)).encode()

    def test_roundtrip_and_deterministic(self):
        e = D.encrypt_bytes(self.data, KEY)
        self.assertEqual(json.loads(e)["v"], 2)
        self.assertEqual(D.decrypt_bytes(e, KEY), self.data)
        self.assertEqual(e, D.encrypt_bytes(self.data, KEY))             # 同じ中身なら同じ暗号文
        self.assertEqual(D.decrypt_bytes(D.encrypt_bytes(b"", KEY), KEY), b"")

    def test_local_change_changes_few_lines(self):
        e1 = D.encrypt_bytes(self.data, KEY)
        mid = len(self.data) // 2
        e2 = D.encrypt_bytes(self.data[:mid] + b"INSERTED" + self.data[mid:], KEY)    # 途中に挿入
        old = set(e1.split(b"\n"))
        changed = [x for x in e2.split(b"\n") if x not in old]
        total = e2.count(b"\n")
        self.assertLessEqual(len(changed), 3, f"{len(changed)}/{total} 行が変わった")
        self.assertGreater(total, 10)

    def test_reads_v1(self):
        k = D._derive(KEY)
        gz = D._gzip(self.data)
        iv = hmac.new(k[32:], gz, hashlib.sha256).digest()[:12]
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ct = AESGCM(k[:32]).encrypt(iv, gz, None)
        b64 = lambda b: base64.b64encode(b).decode()
        v1 = json.dumps({"v": 1, "kdf": "PBKDF2-SHA256", "iter": D.ITER, "salt": b64(D.SALT), "iv": b64(iv), "ct": b64(ct)}).encode()
        self.assertEqual(D.decrypt_bytes(v1, KEY), self.data)

    def test_wrong_key(self):
        with self.assertRaises(ValueError):
            D.decrypt_bytes(D.encrypt_bytes(b"abc", KEY), "other")


if __name__ == "__main__":
    unittest.main()
