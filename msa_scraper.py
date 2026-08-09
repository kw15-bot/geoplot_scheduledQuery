#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
commit_state.py
================

GitHub Actionsワークフロー（scrape.yml）の「Commit updated geojson/state」ステップを、
今までの素朴な git pull --rebase && git push の連打ではなく、この専用スクリプトに
置き換えるためのもの。

これまでの問題:
  msa_out/state.json は毎回JSON全体を丸ごと書き直すファイルなので、2つの実行が近い
  タイミングでそれぞれ別の新規記事を追記すると、中身は競合していなくても
  Gitからは「同じファイルの近い行を編集した」と見なされて本物のマージコンフリクト
  になり得る。git pull --rebase はこの種の衝突を自動解決できないため、5回リトライ
  しても全滅し、main ブランチが全く前に進まなくなる、という詰まり方をしていた。

このスクリプトの方針:
  state.json は「記事URLをキーにした独立レコードの集合」でしかないので、Gitの行単位
  マージに頼らず、"originの最新state.json" と "このランで新しく計算したstate" を
  Pythonの辞書として素直に統合（dict.update）すれば、衝突しようがない
  （同じキーを両方が書き換えていた場合だけ「新しい方を採用」という単純な規則で
  必ず解決できる。フィールドレベルの深い競合は起こり得ない構造のため）。

  military.geojson は state から毎回re-buildできるものなので、統合後のstateから
  作り直すだけでよい（geojson自体をgit的にマージしようとする必要がそもそも無い）。

流れ:
  1. まず素直に `git add / commit / push` を試す（衝突が起きていなければこれで終わり、
     今まで通り一瞬で終わる）。
  2. push が拒否されたら:
       a. git fetch origin <branch>
       b. `git show origin/<branch>:msa_out/state.json` でリモートの最新state.jsonを取得
       c. ローカルでこのランが書き出した state.json（このスクリプト実行前に
          msa_scraper.py が既に書き出し済みのもの）と辞書として統合
          （キーが重複していればローカル＝このランの結果を優先。fetch取得漏れ・
          fetch自体の新規追加分はそのまま引き継がれる）
       d. 統合結果で state.json を上書きし、military.geojson も作り直す
       e. add / commit(--amend) / push を再試行
     を最大5回、乱数バックオフを挟みながら繰り返す。

使い方（scrape.yml から呼ばれる想定。単体でも動く）:
  python commit_state.py --out-dir ./msa_out --branch main

前提:
  - msa_scraper.py と同じフォルダに置く（load_state/save_state/build_geojsonを
    そのままimportして使うため、実際の抽出ロジックと100%同じ書式で書き出せる）。
  - 呼び出す前に msa_scraper.py --once の実行が終わっていて、
    <out-dir>/state.json がこのランの最新状態になっていること。
  - git のuser.name/user.emailは呼び出し元（ワークフロー側）で設定済みであること。
"""

import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

try:
    import msa_scraper as scraper
except ImportError:
    sys.exit("msa_scraper.py が同じフォルダに見つかりません。同じフォルダで実行してください。")


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


def fetch_remote_state(branch: str, rel_path: str):
    """origin/<branch> にある state.json の中身を、fetchしてから読み込んで返す。
    リモート側にまだファイルが無い（初回など）場合は None。"""
    run(["git", "fetch", "origin", branch], check=True)
    result = subprocess.run(
        ["git", "show", f"origin/{branch}:{rel_path}"],
        text=True, capture_output=True,
    )
    if result.returncode != 0:
        return None  # リモートにまだ無い(初回コミット前など) -> 呼び出し側でローカルのみ採用
    import json
    return json.loads(result.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="./msa_out")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--max-retries", type=int, default=5)
    ap.add_argument("--commit-message", default="Auto-update military.geojson [skip ci]")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    state_path = out_dir / "state.json"
    geojson_path = out_dir / "military.geojson"

    if not state_path.exists():
        sys.exit(f"{state_path} が見つかりません。先に msa_scraper.py --once を実行してください。")

    # このランが計算した最新state(ローカル版)。衝突時のマージで「ローカル優先」の
    # 基準として何度も参照するので、ここで一度だけ読み込んでおく。
    local_state = scraper.load_state(str(state_path))

    run(["git", "add", str(geojson_path), str(state_path)])
    if not git_diff_staged_has_changes():
        print("no changes to commit", file=sys.stderr)
        return

    run(["git", "commit", "-m", args.commit_message])

    if try_push(args.branch):
        print("push succeeded on first try", file=sys.stderr)
        return

    print("push rejected, switching to dict-level merge against origin...", file=sys.stderr)

    for attempt in range(1, args.max_retries + 1):
        remote_state = fetch_remote_state(args.branch, "msa_out/state.json")

        if remote_state is None:
            merged_state = local_state
        else:
            # 独立レコードの単純な辞書統合。同じキー(記事URL)が両方にあれば
            # ローカル(このラン)を優先 -- フィールド単位の深い競合は構造上起こらない。
            merged_state = dict(remote_state)
            merged_state.update(local_state)

        scraper.save_state(str(state_path), merged_state)
        geojson = scraper.build_geojson(merged_state)
        import json
        with open(geojson_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        # いま origin が指している場所まで、いったんローカルのブランチ先端を合わせて
        # おく(このコミットの親を最新originにする)。中身は上でもう手作業マージ済み
        # なので、ここでの `reset --hard origin/<branch>` はマージ元の作業内容を
        # 失わせない(state.json/military.geojsonはこの後すぐ書き直して再コミットする)。
        run(["git", "reset", "--hard", f"origin/{args.branch}"])
        run(["git", "add", str(geojson_path), str(state_path)])
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
  
