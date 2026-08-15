# GEOPLOT MIL 引き継ぎ資料

最終更新: 2026-08-10
対象バージョン: `geoplot-mil.html` = `v1.1.7` / `msa_scraper.py` = `DETAIL_EXTRACTION_VERSION 14`

> 前回の引き継ぎ資料（2026-08-06版、`geoplot-mil.html` = `v20260805.28` / `DETAIL_EXTRACTION_VERSION 12`
> 時点）からの続き。GitHub Actions + GitHub Pages 運用への移行までは前回資料を参照。

## 概要

中国各地の海事局（MSA）が公開する航行警告（航行警報）の一覧・詳細ページを巡回し、
軍事関連（演習・射撃訓練など）の警告だけを抽出して地図上にプロットするツール。

- `msa_scraper.py`：各局のサイトを巡回し、`msa_out/military.geojson`（軍事関連の座標データ）
  と `msa_out/state.json`（全既知警告の累積データ）を出力する。
- `commit_state.py`：**今回新規追加**。GitHub Actions側で`state.json`をコミット・push する際、
  Gitの行単位マージではなくPythonの辞書レベルでの統合を行う補助スクリプト（詳細は後述）。
  ローカル運用（`start_msa_loop.bat`）では使わない、GitHub運用専用。
- `geoplot-mil.html`：geojsonを読み込んで地図表示するビューア。単体HTML、ビルド不要。
- `audit_extraction_gaps.py` / `deep_scan_report.py`：**今回新規追加**。有効期間・座標の
  抽出漏れを見つけるための診断ツール（詳細は後述）。
- `start_msa_loop.bat`：ローカル運用用（Windows）。今回のセッションでは変更なし。

## 今回のセッションでの主な出来事

時系列でざっくり言うと：①GitHub Actions側で大規模障害が発生し、cronの実行間隔が
不安定になった（これ自体はGitHub側の問題で本プロジェクトの不具合ではない）→
②その対応として`scrape.yml`の実行間隔を調整 → ③並行して、過去の警報本文を
`--pages 10`まで深掘りして再スキャンし、有効期間・座標を抽出できていない記事を洗い出し、
`msa_scraper.py`の抽出ロジックを3件修正 → ④その過程で`state.json`のコミットが
繰り返しマージコンフリクトを起こす別問題が発覚し、`commit_state.py`を新設して対応、
という流れ。

## geoplot-mil.html 側の変更点

| 内容 |
|---|
| `APP_VERSION`のバージョン表記方式を変更。従来の`vYYYYMMDD.N`（日付＋当日ビルド連番）から、`v1.1.7`から始まるシンプルな`vMAJOR.MINOR.PATCH`（10進数）方式に切り替え。PATCH=通常の修正、MINOR=新機能、MAJOR=破壊的変更、という運用ルールをコメントに明記済み。 |

## msa_scraper.py 側の変更点

### `--pages`が初回実行時に無視されるバグを修正

`state.json`が存在しない「初回実行」判定時、`--pages`を明示的に指定しても無視され、
常に`--first-run-pages`（既定3）が使われてしまっていた（`deep_scan_report.py`で
`--pages 10`を指定したはずが実際は3ページしか遡っていなかったことで発覚）。
`--pages`のデフォルトを`None`にし、明示指定があれば初回実行かどうかに関わらず
そちらを優先するよう修正。

### 有効期間・座標の抽出ロジック修正（`DETAIL_EXTRACTION_VERSION` 12→14）

**v13**: 日付だけの表記（時刻無し）を有効期間として受理する条件を拡張。従来は
直後が`在`/`将在`/`拟在`で始まる場合のみ受理していたが、`以下`/`如下`（座標を
箇条書きで示す前の導入句、例:「8月8日，以下四点连线海域内进行射击训练：（1）...」）
にも対応。

**v14**: `--pages 10`の深掘りスキャン（後述の`deep_scan_report.py`）で見つかった
実際の未対応フォーマット3件に対応：
- 座標のプライム記号対応：`38-42.80′N`のように、ハイフン区切り形式でも分の後に
  プライム記号（正規化後はアポストロフィ）が付くケースを`DDM_HYPHEN`パターンで受理
- `至`抜けの許容：`每天0800时1900时`のように、原文タイプミスで`至`が抜けている
  同日内の時間範囲表記に対応
- 見出しラベル形式の対応：`一、活动时间： 2026年6月24日至7月8日。`のように、日付の
  直後ではなく「〜时间：」ラベルの直後に日付が来る、番号付き箇条書きスタイルの
  通告テンプレートに対応

これら3件は「軍事関連なのに抽出できていない」もののみ対応。同じ深掘りスキャンで
見つかった残り6件（撤回通知そのもの、航標調整作業、浮標名や目印からの相対距離でしか
場所を示していない記事）は、座標・期間が本文に存在しない／軍事目的でないため対象外
（意図的に何もしていない）。

### `state.json`のキー順を`sort_keys=True`に変更

`save_state()`が`sort_keys`無しでdumpしていたため、新規記事が常にファイル末尾に
追記される形になっていた。これにより「2つの実行がそれぞれ別の新規記事を追記した
だけ」でもGitが「同じ行を編集した」と誤認しやすい構造になっていた（後述の
マージコンフリクト問題の一因）。`sort_keys=True`にしてキー（記事URL）順に書き出す
ことで、新規記事の追記位置がファイル全体に分散されるよう修正。`build_geojson()`側も
`state.items()`ではなく`sorted(state.items())`でイテレートするよう合わせて修正（features
の並び順も決定的になる）。

> 注意: この修正を反映した直後の1回だけ、既存の`state.json`全体が並び替えられるため
> 通常よりかなり大きい差分のコミットになる。データの中身自体は変わらないので問題ない。

## 新規ツール

### `audit_extraction_gaps.py`

`state.json`に保存済みの本文（`raw_text`、本文取得済みのものだけ）に対して、
現行の`msa_scraper.py`の`summarize_validity()`/`parse_coordinate_groups()`を
**その場でもう一度**かけ直し、有効期間・座標が取れていない記事を一覧化する診断ツール。
`state.json`にキャッシュされている過去の結果は一切参照せず、常に最新コードで
再判定する。`--only no_period` / `--only no_coords`などで絞り込み可能。

### `deep_scan_report.py`

内部で`msa_scraper.py --once --pages <N>`（既定10）を実行しつつ、進捗ログの中の
`本文取得OK: <タイトル> → 座標N点...、有効期限.../有効期間は検出できず`という行を
横取りし、`座標0点`または`有効期間は検出できず`に該当する記事だけを抽出。実行後の
最新`state.json`から本文全文・URL等を引いてレポート化する。通常運用の`state.json`を
そのまま更新するので、実行後も普段通り使い続けられる。`--dry-run-parse-only`で
既存ログファイルの再解析のみも可能。

今回はこのツールで見つかった9件（no_period=2、no_coords=3、both=4）のうち、
実際に軍事関連で本文に座標データが存在するもの3件を上記v13/v14の修正対象とした。

## GitHub Actions運用まわりの変更点

### 大規模障害への対応（2026-08-06〜08頃）

GitHub Actions/Pages側で断続的な大規模障害（[githubstatus.com](https://www.githubstatus.com/)
上でMajor Outage扱い）が発生し、webhookトリガーのスロットリングにより`schedule`
cronの発火が数十分〜数時間規模で不規則に遅延・スキップされる事象が続いた。これは
リポジトリ側の設定・コードの問題ではなく、GitHub側インフラの問題。

対策として`scrape.yml`のcronを`*/30`→`*/10`に変更し、「直近の`state.json`コミットから
25分未満なら何もせず即終了する」フレッシュネスチェックを追加。10分おきに3回
「弾」を撃つことで、GitHub側が多少トリガーを間引いても実質30分間隔に近い頻度を
保てるようにした（GitHub全体規模の完全障害中はどうしようもないが、それ以外の
軽微な遅延・間引きには効く）。

```yaml
- name: Check if a scrape is actually due
  id: freshness
  run: |
    last_ts=$(git log -1 --format=%ct -- msa_out/state.json 2>/dev/null || echo 0)
    now_ts=$(date +%s)
    elapsed_min=$(( (now_ts - last_ts) / 60 ))
    if [ "$last_ts" != "0" ] && [ "$elapsed_min" -lt 25 ]; then
      echo "should_run=false" >> "$GITHUB_OUTPUT"
    else
      echo "should_run=true" >> "$GITHUB_OUTPUT"
    fi
```

以降の各ステップに`if: steps.freshness.outputs.should_run == 'true'`を付与し、
不要な回はセットアップごとスキップするようにしている。

**注意点**: このジョブが「成功（緑）」表示でも、フレッシュネスチェックで
スキップされただけで実質何もしていないケースがある。ログの「Run scraper」の
所要時間を見れば、実際にスクレイプが走ったか（数十秒〜数分）、即終了したか
（1秒未満）を区別できる。

### `state.json`のマージコンフリクト問題と`commit_state.py`の新設

上記の障害対応と並行して、`git pull --rebase && git push`のリトライがすべて
失敗し続け、`main`ブランチの自動コミットが全く進まなくなる事象が発生：

```
CONFLICT (content): Merge conflict in msa_out/state.json
push failed after retries
```

原因は、`state.json`が「JSON全体を毎回丸ごと書き直すファイル」であるため、
2つの実行が近いタイミングでそれぞれ別の新規記事を追記すると、内容としては
競合していなくてもGitからは「同じ行を編集した」と見なされ、行単位のマージに
失敗しやすい構造だったこと（`sort_keys=True`化だけでは、必ずしも解消しきれない
ケースがあった）。

対策として`commit_state.py`を新設。push が拒否された場合、Gitの行単位マージに
頼らず、originの最新`state.json`と自分（このラン）が計算した`state`を**Pythonの
辞書として`dict.update()`で統合**してから再コミット・pushする方式に変更。
`state.json`は記事URLをキーにした独立レコードの集合でしかないため、この方式なら
「同じキーがあれば新しい方を採用」という単純なルールだけで必ず解決できる
（フィールドレベルの深い競合が起こり得ない構造のため）。`military.geojson`は
統合後の`state`から毎回re-buildするだけでよい。

`scrape.yml`の最後のステップは、この方式に合わせて以下のように変更：

```yaml
- name: Commit updated geojson/state
  if: steps.freshness.outputs.should_run == 'true'
  run: |
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    python commit_state.py --out-dir msa_out --branch main
```

**⚠️ 反映時の注意（今回、実際に2回事故った点）**:
- `commit_state.py`は`msa_scraper.py`と**別の独立した新規ファイル**として
  リポジトリ直下に追加する必要がある。`msa_scraper.py`を上書きする形で
  `commit_state.py`の中身を貼ってしまい、スクレイパー本体が壊れる事故が発生した
  （`python msa_scraper.py --once`が`commit_state.py`の引数パーサーに解釈されて
  `unrecognized arguments: --once`エラーになる形で発覚）。GitHubで各ファイルを
  編集する際は、**貼り付け前に編集画面上部のファイル名を必ず確認**すること。
- `scrape.yml`だけ更新して`commit_state.py`本体の追加を忘れると、
  `python: can't open file '.../commit_state.py': No such file or directory`
  で「Commit updated geojson/state」ステップが失敗する。2ファイルはセットで
  反映すること。
- フレッシュネスチェックにより、更新直後の実行が「25分経っていないためスキップ」
  で見かけ上「成功」してしまい、実際には新しいコードが一度も呼ばれないまま
  「動作確認OK」と誤認しやすい。本当に新しいコードが動いたかは、ログの
  「Run scraper」「Commit updated geojson/state」ステップの所要時間・中身を
  必ず確認すること。

## 主要ファイル一覧

```
geoplot-mil.html              地図ビューア本体（単体HTML、ビルド不要）v1.1.7
msa_scraper.py                スクレイパー本体 DETAIL_EXTRACTION_VERSION 14
commit_state.py               【新規】state.jsonの辞書レベルマージ&コミット補助（GitHub運用専用、ローカルでは不要）
audit_extraction_gaps.py      【新規】抽出漏れ診断ツール（既存state.jsonのraw_textを再判定）
deep_scan_report.py           【新規】深掘りスキャン+ログ仕分けラッパー（--pages N + 抽出漏れレポート）
start_msa_loop.bat            ローカル運用用（Windows、任意）※今回変更なし
.github/workflows/scrape.yml  GitHub Actions定期実行ワークフロー（*/10 cron + フレッシュネスチェック + commit_state.py呼び出し）
msa_out/military.geojson      出力（軍事関連座標データ）
msa_out/state.json            出力（全既知警告の累積state、継続運用に必須、sort_keys=Trueで書き出し）
```

**ローカル運用（`start_msa_loop.bat`）に必要なのは `msa_scraper.py` / `geoplot-mil.html` /
`start_msa_loop.bat` の3つのみ。** `commit_state.py`・`scrape.yml`はGitHub運用専用。

## 運用上のリスク・今後の検討事項（未対応）

前回資料からの継続分に加え、今回判明した分：

- **深掘りスキャンで見つかった「原理上直せない」6件**：座標が浮標の名前や目印からの
  相対距離でしか書かれておらず、本文に緯度経度の数値が一切無い記事が存在する
  （黄海航警30/26、沪航警538/26など）。外部の浮標位置データベース等と突き合わせない
  限り座標化できないため、対応範囲外とした。今後同種の記事が増えるようなら、
  「座標抽出不可」として地図上には出さずカード一覧にのみテキストで表示する、
  といった別対応を検討してもよいかもしれない。
- **`commit_state.py`の`git reset --hard origin/<branch>`について**：マージ時に
  一度ローカルの作業ツリーをoriginの最新に強制リセットしてから、統合済みの
  `state.json`/`military.geojson`だけを再度書き込む方式にしている。他のファイル
  （`msa_scraper.py`本体など）に対する**未pushのローカル変更がある状態でこの
  スクリプトを走らせると失われる**ため、CI環境（チェックアウト直後、他の変更が
  無い状態）でのみ使う前提になっている。ローカル手元での実験的な用途には使わない
  こと。
- **失敗の検知方法が未整備**（前回資料からの継続）：連続失敗時にexit code非ゼロで
  終了させる改修は、ユーザー判断待ちのまま今回も未着手。
- 前回資料に記載の「クラウドIPからのアクセスブロックリスク」「リポジトリの肥大化」
  「同一LAN内の他端末からのアクセス」も引き続き未対応・様子見。
