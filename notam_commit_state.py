#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_commit_state.py
======================

GitHub Actionsワークフロー（notam-collect.yml）の「Commit updated notam_out」ステップ用。
MSA側の commit_state.py と同じ理由・同じ戦略（dict-level merge + retry）を、
notam_cn_collect.py の出力構成（state_active.json / notam_cn.geojson /
archive/notam_YYYY-MM.geojson の3種）に合わせて書き直したもの。

なぜ commit_state.py をそのまま使い回せないか:
  MSA側は state.json(dict) + military.geojson(毎回全部re-build)の2ファイルだけで完結するが、
  NOTAM側は state_active.json(dict) + notam_cn.geojson(re-build) に加えて、月ごとに
  「追記のみ・idで重複排除」で増え続ける archive/notam_YYYY-MM.geojson があり、
  ファイル構成が違う。中身のマージ戦略（同じキーが競合したらローカル優先の
  dict.update）自体は同じなので、notam_cn_collect.py の to_feature()/sort_key() を
  import して使い回している。

流れ（commit_state.pyと同じ2段構え）:
  1. 素直に add/commit/push を試す（衝突が無ければこれで終わり）。
  2. push が拒否されたら、最大 --max-retries 回、乱数バックオフを挟みながら:
       a. git fetch origin <branch>
       b. state_active.json は origin 版とローカル版を dict.update で統合（ローカル優先）
       c. notam_cn.geojson はその統合結果から作り直す（re-buildできるファイルなので
          Git的にマージしようとしない）
       d. archive/notam_YYYY-MM.geojson は、このランで変更のあった月ファイルだけ、
          properties.nms_id をキーに origin 版とローカル版を統合（ローカル優先）
       e. git reset --hard origin/<branch> でブランチ先端を合わせてから統合結果を書き出し、
          add/commit(--amend)/push を再試行

使い方（notam-collect.yml から呼ばれる想定。単体でも動く）:
  python notam_commit_state.py --out-dir ./notam_out --branch main

前提:
  - notam_cn_collect.py と同じフォルダに置く(to_feature/sort_key/read_json/write_if_changedを
    そのままimportして使うため)。
  - 呼び出す前に notam_cn_collect.py の実行が終わっていて、<out-dir>/state_active.json が
    このランの最新状態になっていること。
  - git の user.name/user.email は呼び出し元（ワークフロー側）で設定済みであること。
"""

import argparse
import datetime as dt
import json
import random
import subprocess
import sys
import time
from pathlib import Path

try:
    import notam_cn_collect as C
    import data_crypt as DC
except ImportError:
    sys.exit("notam_cn_collect.py / data_crypt.py が同じフォルダに見つかりません。同じフォルダで実行してください。")


def run(cmd, check=True, capture=False):
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, text=True, capture_output=capture)
    if capture:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result


def git_diff_staged_has_changes() -> bool:
    result = subprocess.run(["git", "diff", "--staged", "--quiet"])
    return result.returncode != 0  # quiet mode: 0=no diff, 1=has diff


def try_push(branch: str) -> bool:
    result = subprocess.run(["git", "push", "origin", branch])
    return result.returncode == 0


def fetch_remote_json(branch: str, rel_path: str):
    """origin/<branch> にある指定パスのJSONを、fetchしてから読み込んで返す。
    リモート側にまだ無ければ None。2026-10-09: リポジトリには暗号化した <rel_path>.enc だけがあるので、
    それを復号して読む(data_crypt.py、HANDOFF_NOTAM.md §25)。"""
    return DC.git_show_json(f"origin/{branch}", rel_path)


def rebuild_geojson(active: dict, now):
    features = sorted(
        (C.to_feature(r, now) for r in active.values() if r.get("type") != "C" and r.get("geometry")),
        key=C.sort_key,
    )
    return {"type": "FeatureCollection", "features": features}


def merge_archive_fc(local_fc: dict, remote_fc: dict) -> dict:
    """archive/notam_YYYY-MM.geojson を properties.nms_id キーで統合(ローカル優先)。
    notam_cn_collect.py の archive_ended() 内の byid パターンと同じ考え方。"""
    byid = {}
    for f in (remote_fc or {}).get("features", []):
        nid = (f.get("properties") or {}).get("nms_id")
        if nid:
            byid[nid] = f
    for f in (local_fc or {}).get("features", []):
        nid = (f.get("properties") or {}).get("nms_id")
        if nid:
            byid[nid] = f  # ローカル(このラン)を優先
    return {"type": "FeatureCollection", "features": sorted(byid.values(), key=C.sort_key)}


def changed_archive_paths(out_dir: Path):
    """このランで変更した(git add 済みの)archive配下のファイルパス一覧(平文の名前の相対パス、posix区切り)。
    平文は .gitignore で除外しているので、暗号化した .enc のステージ済みの変更(新規を含む)から拾う。
    2026-10-09: 以前は commit の後に git status で拾っていたため、常に空になっていた(commit 前に呼ぶ)。"""
    result = subprocess.run(["git", "diff", "--staged", "--name-only", "--", str(out_dir / "archive")],
                             text=True, capture_output=True)
    return [p[:-len(DC.ENC)] for p in result.stdout.split() if p.endswith(".geojson" + DC.ENC)]


def encrypt_outputs(paths):
    """平文(このスクリプトが扱うファイル)を暗号化し、git add する .enc のパスを返す。"""
    return DC.encrypt_paths([p for p in paths if Path(p).exists()])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="./notam_out")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--max-retries", type=int, default=5)
    ap.add_argument("--commit-message", default="Auto-update notam_cn.geojson [skip ci]")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    state_path = out_dir / "state_active.json"
    geojson_path = out_dir / "notam_cn.geojson"
    meta_path = out_dir / "meta.json"

    if not state_path.exists():
        sys.exit(f"{state_path} が見つかりません。先に notam_cn_collect.py を実行してください。")

    now = dt.datetime.now(dt.timezone.utc)
    local_state = C.read_json(state_path, {})

    add_paths = [str(geojson_path), str(state_path)]
    if meta_path.exists():
        add_paths.append(str(meta_path))
    archive_dir = out_dir / "archive"
    if archive_dir.exists():
        add_paths.append(str(archive_dir))

    run(["git", "add", *encrypt_outputs(add_paths)])
    if not git_diff_staged_has_changes():
        print("no changes to commit", file=sys.stderr)
        return

    # push拒否に備え、このランがarchiveに書いた変更点を先に控えておく
    # (git reset --hard すると .enc がoriginに戻ってしまうため、ローカルの変更内容は
    # "今この時点のファイル中身" として先に読んでおく必要がある)。
    local_archive_changed = changed_archive_paths(out_dir)
    local_archive_content = {p: C.read_json(Path(p), {"type": "FeatureCollection", "features": []})
                              for p in local_archive_changed}

    run(["git", "commit", "-m", args.commit_message])

    if try_push(args.branch):
        print("push succeeded on first try", file=sys.stderr)
        return

    print("push rejected, switching to dict-level merge against origin...", file=sys.stderr)

    rel_state = str(state_path).replace("\\", "/")
    rel_geojson = str(geojson_path).replace("\\", "/")
    rel_meta = str(meta_path).replace("\\", "/")

    for attempt in range(1, args.max_retries + 1):
        run(["git", "fetch", "origin", args.branch], check=True)
        remote_state = fetch_remote_json(args.branch, rel_state)

        if remote_state is None:
            merged_state = local_state
        else:
            # 独立レコードの単純な辞書統合。同じキー(NOTAM id)が両方にあれば
            # ローカル(このラン)を優先 -- フィールド単位の深い競合は構造上起こらない。
            merged_state = dict(remote_state)
            merged_state.update(local_state)

        # 先にブランチ先端を最新originへ合わせ、その「後」でマージ結果を書き出す
        # (commit_state.py と同じ順序。書き出し→resetの順だと書いたものが消える)。
        run(["git", "reset", "--hard", f"origin/{args.branch}"])

        C.write_if_changed(state_path, merged_state, indent=1)
        merged_geojson = rebuild_geojson(merged_state, now)
        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump(merged_geojson, f, ensure_ascii=False, indent=2)

        # meta.jsonはブックキーピング用途で衝突しても実害が薄いため、単純にローカル優先で
        # 上書きする(存在すれば)。無ければorigin版のまま(このランはmeta更新なしだった扱い)。
        local_meta = C.read_json(meta_path, None)
        if local_meta is not None:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(local_meta, f, ensure_ascii=False, indent=1)

        add_paths2 = [rel_geojson, rel_state]
        if Path(rel_meta).exists():
            add_paths2.append(rel_meta)

        for rel_path, local_fc in local_archive_content.items():
            remote_fc = fetch_remote_json(args.branch, rel_path)
            merged_fc = merge_archive_fc(local_fc, remote_fc)
            p = Path(rel_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(merged_fc, f, ensure_ascii=False, indent=2)
            add_paths2.append(rel_path)

        run(["git", "add", *encrypt_outputs(add_paths2)])
        if not git_diff_staged_has_changes():
            print("merged result is identical to origin -- nothing new to commit", file=sys.stderr)
            return
        run(["git", "commit", "-m", args.commit_message])

        if try_push(args.branch):
            print(f"push succeeded after merge (attempt {attempt}/{args.max_retries})", file=sys.stderr)
            return

        print(f"push still rejected, retrying merge ({attempt}/{args.max_retries})...", file=sys.stderr)
        time.sleep(random.randint(5, 15))

    sys.exit("push failed after retries (dict-level merge)")


if __name__ == "__main__":
    main()
