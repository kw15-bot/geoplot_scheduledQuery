# geoplot-mil NOTAM機能 引き継ぎ資料

作成日: 2026-09-21 / 最終更新: 2026-09-27 / 対象: 「航行警報海域可視化」(geoplot-mil) への **NOTAM地図化機能の追加**

> このファイルは NOTAM 機能の引き継ぎ専用。既存の MSA(海事局航行警告) 側の引き継ぎは別紙 `HANDOFF.md`
> を参照。そちらの未完了事項（デプロイ・ワークフロー実行確認など）は本資料では扱わない。
>
> **2026-09-27時点で、NOTAM機能はMSA側の本番リポジトリ(`geoplot-mil.html` v1.8.9ベース)に
> 統合済み**(`geoplot-mil_msa+notam_2026-09-27.zip`)。§0・§9・§10・§15(新規)を先に読むこと。
> §1〜§8・§10.1〜10.9・§11〜14は2026-09-24までの調査・設計の記録として残しているが、
> 一部の数値・UI説明は2026-09-27の変更で古くなっている（該当箇所に注記あり）。

---

## 0. 30秒サマリ（2026-09-27更新）

- **目的**: 中国・香港・マカオ・台湾＋周辺国の参考FIR(フィリピン/オークランド洋上管制/福岡/仁川/平壌)が
  発行するNOTAMを地図化する（航行警報海域と同じ見せ方）。失効分はArchiveに蓄積する。
- **現在地**: **NOTAM機能はMSA本番リポジトリの`geoplot-mil.html`(v1.8.9)に統合済み**(v1.9.0、
  §15参照)。MAP(自動更新)/MAP(手動)と同格の第3モードタブ「NOTAM」として動作する。
  収集(`notam_cn_collect.py`)・コミット(`notam_commit_state.py`)・ワークフロー
  (`.github/workflows/notam-collect.yml`)も同梱済みで、`scrape.yml`(MSA)とは独立して
  並行実行できる。**ただし実リポジトリへのpush・Actions実行・Secrets設定はまだ行っていない**
  （このチャット上での実装・ローカルでのgit衝突再現テストまでが完了範囲）。
- **表示条件を再整理**(2026-09-27、§15): ①収集時点でFIR/地域(CN/HK/MO/TW/REF)を絞り込み(REFは
  4文字完全一致のみ、プレフィックスでは拾わない) → ②ビューアは**E項の実座標ポリゴン
  (`geometry_source: text-polygon`)のみ表示**(Q項由来の円・点は収集はするが表示しない)。
  `focus_tag`・`keyword_hit`によるビューア側の絞り込みチェックボックスは**撤廃**し、
  「収集条件に合致するものは全部ビューワに出す」方針に変更した。地域の絞り込みは
  チェックボックスではなく、航行警報側の海事局ドロップダウンと同じ方式のFIR単体選択に変更。
- **データ源**: FAAの**NMS-API**（認証情報取得済み・stagingで実データ検証済み。prod・再配布条件は
  §8参照、未確認のまま）。**当面はFAA NOTAM Searchで個別検索して見つけたPDF/生テキストを
  `notam_paste_import.py`で取り込む運用が主軸**(§15、実際に28件のサンプルをこの方法で処理済み)。
- **できていること**: 収集・調査・貼り付けインポート・コミット用の各スクリプト、GitHub Actions
  ワークフロー(NOTAM用1本、Pages用は別途要確認)、回帰テスト36件、ビューア統合(v1.9.0)。
- **次にやること**: 実リポジトリへの反映・Secrets設定・Actions初回実行確認（§9）。

---

## 0.5 このファイルの読み方（新規セッション向け最短ルート）

1. 本セクションと§9（次のステップ）を読む。
2. UIの現在の姿を知りたいだけなら§15（2026-09-27セッションログ）だけで足りることが多い。
3. データ源・判定ロジックの経緯や設計判断の理由を知りたい場合のみ§1〜§10.9（2026-09-24時点の
   記録）を読む。ただしUIの見た目・絞り込み方式の説明(§10)は2026-09-27の変更で古くなっている
   箇所が多い点に注意。

---

## 1. ユーザーの決定事項（確定済み）

| 項目 | 決定 |
|---|---|
| 対象 | **中国が発行するNOTAMのみ**。当初「全世界」だったが絞り込み。**中国本土・香港・マカオ・台湾**を含める（ユーザー指示） |
| 収集頻度 | 10分おき程度 |
| 運用形態 | GitHub で公開 + **cron-job.org から GitHub Actions(workflow_dispatch) を叩く**（既存MSA側と同じ運用） |
| Archive | 失効・取消は**即時Archive移行**で問題ない。完全削除はしない（蓄積） |
| 確度判定 | **不要。人間が実施する**（軍事かどうかの自動判定機能は作らない） |
| 開発方針 | 形が決まるまでは git を使わず**手元で試したい** |
| 認証情報 | NMS-API の KEY/SECRET は取得済み（環境は不明 → §8 参照） |
| リポジトリ公開範囲 | **Private**（再配布条件が未確認のため。§2・§8-10・§12） |
| データ収集の当面の方針 | **2026-09-24〜: NMS-APIの自動収集は保留し、FAA NOTAM SearchのArchiveから個別に集めたサンプルを軸に進める**（§0・§9・§10.7） |
| 目的別分類 | Qコード主題による粗いタグ`focus_tag`（§5.4）＋本文語句一致`keyword_hit`（§5.4・§10.5）の2層。どちらも**軍事かどうかの自動判定ではない**、あくまで人間が見る優先順位付け |
| 語句フィルタの採用語句（2026-09-27決定、**未適用**） | 将来ビューアに語句フィルタを適用するときは **`TEMPORARY`・`SPECIAL`** を採用する（これまでのサンプルで軍事関連に多用されているため。§16）。それまではビューアに種別・語句一致を一切表示しない |

> 用語: 台湾・香港・マカオは、ICAO地名指標の先頭2文字で機械的にグループ分けしているだけ（`area_group` = CN/HK/MO/TW）。表示ラベルは「中国本土/香港/マカオ/台湾」。

---

## 2. 調査結果（データ源の比較）

**結論: FAA NMS-API が本命。** 申請制だが、緯度経度・半径での検索、GeoJSON出力、`lastUpdatedDate` による差分取得（新規・更新・キャンセルを含む）に対応し、10分おきのポーリングに向く。

| ソース | 評価 |
|---|---|
| **FAA NMS-API** | 採用。FAA が唯一の正式な情報源にする方針。取得は FAA へのメール申請（`NOTAMS@faa.gov`）。GeoJSON/AIXM 5.1 |
| FAA SWIM (SCDS) / NMS-NDS・NPS | JMS によるプッシュ配信。常時接続が必要なので **GitHub Actions(cron方式)では受けられない**（設計上の判断）。SCDS は非運用目的・利用契約あり |
| ICAO API Data Service | 蓄積版は3時間ごとの取得、リアルタイム版は地点リスト必須、無料枠は約100コール。全世界巡回に不向き。取得元は米国 DINS（NMS移行後の稼働は未確認） |
| EUROCONTROL EAD (INO) | 世界の国際NOTAMを集めた中央リポジトリだが、EAD顧客向け・契約が必要（料金は未確認） |
| Notamify / Aviation Edge | 商用API。NMS が使えない場合のフォールバック候補 |
| 各国AIS直接 | 国ごとに別実装。中国の国内向けNOTAMを公開する一般向けサイトは**調べた範囲では見つからなかった** |
| **旧 `external-api.faa.gov/notamapi`** | **使わない**。旧システム側で廃止方向。2026-07-28時点の確認で 401 |

**参考実装（個人開発・OSS）**
- `RISCfuture/notams`（TypeScript）: NMS を定期取得→PostgreSQL→独自JSON API。最も近い参考実装
- `aerocontext`（Rust）: NMS と Leidos の両方を照合
- `faa-nms-api`（npm）: 型付きクライアント、本番/ステージング切替、トークン自動更新
- `NagoDede/notamloader`（Go）: 各国AISのWebフォーム取得。2021年で更新停止
- パーサ: `dfelix/notam-decoder`（JS）、`svoop/notam`（Ruby・保守終了・ICAO附属書15準拠のみ）

**制約・リスク（再掲）**
1. **NMS に流れるのは国際配信されたNOTAMが中心と推測**（未検証）。中国の国内向けのみのNOTAMは見えない可能性。
2. **NMSデータの再配布条件は未確認**。GitHub を public にして全文を公開してよいか、FAA(`NOTAMS@faa.gov`)への照会を推奨。確認できるまでは private リポジトリ、または `raw_text` を出力から外す選択肢。
3. NOTAM は運航・安全判断に使うものではない旨の注意（SCDS の条件）。本ツールは情報可視化用途。

---

## 3. NMS-API 仕様の要点（`nms-api.yaml` **1.0.18**, 2026-02-12 が最新）

ユーザー提供の `nms-api-1_0_17.yaml` は旧版。差分はロケーション形式の緩和と、GeoJSON内のフィールド名変更（`simpleText`/`formattedText`、旧 `domestic_message`/`icao_message`）など。コレクタは両方の名前を受ける。

### 接続
| 環境 | ホスト |
|---|---|
| FIT | `https://api-fit.cgifederal-aim.com` |
| Staging (Pre-Prod) | `https://api-staging.cgifederal-aim.com` |
| Prod | `https://api-nms.aim.faa.gov` |

- **認証**: `POST {host}/v1/auth/token`（**`/nmsapi` を付けない**）、Basic認証 `client_id:client_secret`、body `grant_type=client_credentials`（`application/x-www-form-urlencoded`。Content-Type を余計に付けると `Required param : grant_type` エラー）。
- トークン有効期間 **1799秒(30分)**。切れると 401。配布Excelの KEY = client_id、SECRET = client_secret。
- API本体は `{host}/nmsapi/v1/...`、ヘッダ `Authorization: Bearer <token>`。ブラウザからは不可（機械間インターフェース）。
- FAQ上の想定利用: 「直近3分など必要な期間の差分を取る」「全件が必要なら分類ごとの一括取得」。

### エンドポイント
| パス | 内容 |
|---|---|
| `GET /v1/ping` | 疎通（仕様書のパス一覧には無いが FAQ/curl例に記載） |
| `GET /v1/notams` | フィルタ検索。**ヘッダ `nmsResponseFormat: AIXM\|GEOJSON` が必須**。条件はAND結合。パラメータ無しはエラー |
| `GET /v1/notams/checklist` | チェックリスト（`accountability`/`classification`/`location`） |
| `GET /v1/notams/il`, `/il/{classification}` | 初期ロード。**AIXM(SOAP封筒)専用**。→ **このプロジェクトでは使わない** |
| `GET /v1/locationseries` | ロケーション↔国際シリーズ(accountId, AFTNアドレス)対応。`lastUpdatedDate` あり |
| `GET /v1/content/{token}` | 一括ファイルの取得。FAQ上は `/nmsapi/v1/content/{token}` で**Bearerトークン必須**（旧サンプルJSONの GCS署名URL直リンクは古い） |

### `/v1/notams` のフィルタ
`accountability`, `classification`, `location`(3〜4文字), `notamNumber`, `nmsId`, `feature`, `freeText`, `effectiveStartDate`/`effectiveEndDate`, `lastUpdatedDate`, `latitude`+`longitude`+`radius`(radius ≤ 100NM), `allowRedirect`。
- **FIR・国での絞り込みは無い** → 中国分は**全世界の差分を取ってクライアント側で絞る**設計にした。
- **`lastUpdatedDate`: 窓は最大24時間**。新規・更新・キャンセル(非有効)を返す。処理が30秒超で **408**。
- **`classification` を単独指定**すると、その分類の全件を含む**圧縮ファイルへの相対パス**（5分で失効）を返す。`nmsResponseFormat` に従い **AIXM か GeoJSON**。→ コレクタの `--bootstrap il` はこの経路（GeoJSON指定）。ファイル内部の構造は**未確認**（パーサは寛容に作ってある）。
- `classification` の値: `INTERNATIONAL, MILITARY, LOCAL_MILITARY, DOMESTIC, FDC`（MILITARY は米軍系。中国NOTAMは INTERNATIONAL と推測 → probe で確認）。

### GeoJSON の中身（1件）
`properties.coreNOTAMData.notam` に `id, series, number, year, type, issued, affectedFir, selectionCode(Qコード), location, icaoLocation, classification, accountId, effectiveStart, effectiveEnd, estimated, schedule, lowerLimit, upperLimit, minimumFl, maximumFl, coordinates, radius, text, lastUpdated, cancelationDate` など。`notamTranslation`(type=`ICAO`/`LOCAL_FORMAT`) に整形済み全文。`geometry` は GeometryCollection（点＋面）。

---

## 4. 実データから得た知見（ユーザー提供のNOTAM2件）

```
A4703/26 NOTAMN Q)ZXXX/QRTCA/IV/BO/W/000/999/2536N11651E039 A)ZGZU ZSHA B)2609172330 C)2609180330
E) A TEMPORARY RESTRICTED AREA ESTABLISHED BOUNDED BY: N230636E1162812-N234042E1174425-N233012E1174957-N225820E1163232- N230636E1162812. HEIGHT: 35,000M AND BELOW. F)GND G)UNL
A4705/26 NOTAMN Q)ZSHA/QRTCA/IV/BO/W/000/999/2343N11742E017 A)ZSHA B)2609172330 C)2609180330
E) A TEMPORARY RESTRICTED AREA ESTABLISHED BOUNDED BY: N240013E1174036-N235732E1175242-N232609E1174457-N232853E1173231- N240013E1174036 . HEIGHT: 50,000M AND BELOW. F)GND G)UNL
```
ユーザーによれば、これらは実際に**軍事用途と判定されたもの**（判定の根拠は聞いていない。MSA航行警告との重なりの可能性があるが未確認）。

**Q) 行の読み方**: `FIR / Qコード / 交通(IV=IFR+VFR) / 目的(BO) / 範囲(W=航行警報) / 下限FL / 上限FL / 中心座標+半径NM`
- `QRTCA` = Q + **RT**(臨時制限空域) + **CA**(発動)。2文字目 R=空域制限、W=警告。読み方は ICAO Doc 8126 に基づく私の理解で、**公式表での確認は未了**。
- **Qコードは軍事の証拠にならない**（理由の欄が無い。ロケット打ち上げ・要人・イベント等でも使われる）。逆に**軍事関連がQR/QWだけとも限らない**（QA=経路変更、QF=飛行場閉鎖、QG=GNSS妨害、QXXXX 等でも出うる）。→ ユーザー決定により、確度の自動判定は作らず、`category`(restriction/warning/other)を絞り込み用の分類としてだけ提供。
- 時刻: B)=開始、C)=終了（UTC, YYMMDDHHMM）。上例は 2026-09-17 23:30Z 〜 09-18 03:30Z。
- **`ZXXX`** = 複数FIRにまたがるNOTAMのQ項FIR欄（中国の仮コード）。→ 接頭辞 `ZX` を中国扱いにした。
- **A) が複数地点**（`ZGZU ZSHA`）になりうる。NMSがこれを `icaoLocation` にどう入れるかは未確認 → 空白等で分割して判定。

**Q項の中心座標の意味と信頼性**
- 影響範囲を**大まかに囲む円**の中心と半径（度分のみ、半径はNM。999=FIR全体以上）。**検索・絞り込み用の目安で、正確な境界ではない**。正確な形は E) 項。
- 2件で検証: 半径は多角形の外接円にほぼ一致（39NM / 17NM。計算値 39.5 / 17.2 でわずかに小さい＝厳密な外接ではない）。中心は A4705 では 0.7NM 差で一致するが、**A4703 では多角形の重心から138NM（主に緯度方向）ずれ**、Q項の円は多角形を含んでいない。原因（誤記か算出方法か）は不明。
- ⇒ **図形は E) 項の座標列を正とし、Q項の円は代替にとどめる**。ずれは `qline_offset_nm` で記録。
- NMS の緯度経度・半径検索はおそらくQ項の値を使うので、A4703 のようなNOTAMは検索で漏れうる（推測）。差分取得方式には影響しない。

**E項の座標書式**: `N230636E1162812` = 北緯23°06′36″ 東経116°28′12″（度分秒。度分のみの `N2306E11628` もパース対象）。先頭点に戻ってリングが閉じる。

---

## 5. 成果物

```
HANDOFF_NOTAM.md                       本資料
notam_cn_probe.py                      調査用（実データで前提を確認する。書き込みは probe_out/ のみ）
notam_cn_collect.py                    収集本体（差分取得・絞り込み・GeoJSON化・Archive・focus_tag・keyword_hit）
notam_paste_import.py                  FAA NOTAM Search等から手動で集めたICAO生テキストを--fixture用JSONに変換（§5.5）
.github/workflows/notam-probe.yml      probe を Actions で手動実行（Claude Codeが書き直し済み、§10.6）
.github/workflows/notam-collect.yml    収集（cron-job.org から起動。同上）
.github/workflows/pages-deploy.yml     geoplot-mil.html / notam_out/ の変更でGitHub Pagesへ自動デプロイ（Claude Codeが追加。§10.6で要確認点あり）
dev/test_notam_cn_collect.py           テスト35件（標準ライブラリのみ・合成データ中心）
dev/mock_nms.py                        probe用の合成データのモックAPI（127.0.0.1:8765）
.gitignore_notam_snippet.txt           .gitignore に追記する内容
```
依存: Python 3.9+ 標準ライブラリのみ。**現在、実物のGitHubリポジトリ（Private）にこの一式がpush済み**（§10.6）。

### 5.1 `notam_cn_probe.py`（調査）
実データで次を1回で確認する: ①認証 ②差分の窓が何時間幅まで通るか（408の有無） ③中国・香港・マカオ・台湾のNOTAMが返るか、および `classification`/`accountId`/`affectedFir`/Qコード/座標+半径/図形の入り方 ④`locationseries` から地点と accountId が引けるか（引ければ `accountability` でサーバ側絞り込みできる可能性）⑤FIR候補・空港の個別問い合わせ件数。
- 環境変数: `NMS_CLIENT_ID`, `NMS_CLIENT_SECRET`, `NMS_ENV`(fit|staging|prod、**既定 prod**), `NMS_HOST`(テスト用上書き), `CN_EXTRA_PREFIXES`, `PROBE_OUT`
- 出力: `probe_out/report.json`, `cn_sample.json`, `locationseries_cn.json`。**トークン・シークレットは一切出力しない**（テスト済み）。
- `cn_sample.json` はそのまま収集スクリプトの `--fixture` に渡せる。

### 5.2 `notam_cn_collect.py`（収集）
**対象判定**: `icaoLocation` か `affectedFir` を空白等で分割し、**4文字トークン**の先頭2文字が次のもの。
`CN: ZB ZG ZH ZJ ZL ZP ZS ZU ZW ZY ZX / HK: VH / MO: VM / TW: RC`
（**4文字に限る理由**: 米国ARTCCの3文字コード `ZBW ZHU ZJX ZLA ZSE ZUA` と先頭2文字が衝突するため。モンゴル ZM、北朝鮮 ZK は対象外）

**フロー**: 状態読込 → 取得(差分 or fixture or bootstrap) → 対象抽出・レコード化 → 置換/取消リンク → Archive移動 → GeoJSON生成 → 変化があるファイルだけ書き込み。

**CLI**
| オプション | 意味 |
|---|---|
| `--out-dir` | 出力先（既定 `./notam_out`） |
| `--fixture <json>` | API を呼ばず取り込む（Feature配列 / NMS応答 / FeatureCollection） |
| `--bootstrap il` | 初回全件投入。`GET /v1/notams?classification=<分類>&allowRedirect=false`(GEOJSON)→content→gz解凍→JSON/JSONL |
| `--bootstrap locations` | 代替。`locationseries` から対象地点を列挙→`location=` で個別問い合わせ |
| `--il-class` | il の分類（既定 INTERNATIONAL） |
| `--extra-locations` | locations 方式で追加する地点(カンマ区切り、FIRコード等) |
| `--keep-ended-days` | 失効後に有効側へ残す日数（**既定0=即時Archive**） |
| `--keep-notice-days` | 取消通知(C)を有効側に残す日数（既定2。順序入れ替わり対策） |
| `--heartbeat-hours` | 変化が無くても meta.json を更新する間隔（既定3時間） |
| `--now` | 現在時刻の上書き（テスト用） |
| `--dry-run` | 何も書かない |

環境変数: `NMS_CLIENT_ID`, `NMS_CLIENT_SECRET`, `NMS_ENV`(既定 prod), `NMS_HOST`。**終了コード: 0=成功 / 2=取得失敗**（Actions を赤くし、`last_success` を進めない）。

**差分の窓**: `since = 最終成功 - 10分`（重ね取り）。上限 23時間30分（超えたら警告して丸める→取りこぼしの可能性があるので bootstrap を再実行）。初回(meta無し)は直近6時間＋警告。408等は5秒待って1回だけ再試行。

### 5.3 出力構成
```
notam_out/
  state_active.json                 有効・未来のNOTAM(＋取消通知)。真実の源 {id: レコード}
  notam_cn.geojson                  ビューア用。有効(active)と未来(upcoming)のみ。毎回作り直し
  archive/notam_YYYY-MM.geojson     失効・取消したNOTAM。失効した月ごと。追記のみ・idで重複排除
  meta.json                         last_success 等。データ変化時＋3時間おきだけ更新（コミット抑制）
```
**ライフサイクル**: `upcoming`(開始前) → `active` → `expired`(終了) / `cancelled`(後続のR/Cで打ち切り) → **同じ回のうちに Archive へ移動**。取消通知(type C)は図形を持たず、描画せず、2日だけ有効側に保持してからArchiveへ。終了時刻が無いNOTAM(PERM等)は自動ではArchiveしない（取消でのみ終わる）。
**置換/取消の検出**: NOTAM本文の `A0110/26 NOTAMR A0101/26` の参照から元NOTAMの終了を発行時刻で打ち切る（追加のみ・消さない）。API側に「取消済み」を示す項目があるかは未確認。`cancelationDate` は、サンプルでは `effectiveEnd` と同値のことがあり、意味は不確か（現状は min(終了, 取消日, 打切り) を有効終了として使用）。
**無駄コミット対策**: 内容が同一なら書き込まない。Archive済みが差分に再登場しても `first_seen` を保持してファイルが変わらない。

**図形の優先順位**（`geometry_source`）: `api`(APIの面) > `text-polygon`(E項の座標列) > `qline-circle`(Q項の円・半径≤250NM・64角形) > `qline-point`(Q項の点・半径>250NMなど) > `api-point`。図形が全く取れないNOTAMは state には残すが geojson には出さない。

**Feature の properties**（既存 `military.geojson` の命名に寄せた）
`title, date, issuer(=accountId), raw_text, valid_start, valid_end, valid_raw, kind(area|point), source("nms"), nms_id, number, series, notam_type, area_group(CN|HK|MO|TW), icao_location, fir, q_code, category(restriction|warning|other), focus_tag(restriction|flag|watch|leisure|plaintext|admin), keyword_hit(bool), matched_keywords(string[]), lower, upper, radius_nm, geometry_source, qline_offset_nm, estimated_end, status(active|upcoming|expired|cancelled|cancel-notice), ended_by, first_seen, last_updated`
- `category`: QR* = restriction、QW* = warning、その他 = other（**Qコード分類の根拠は Doc 8126 の主題区分に基づく想定**。2026-09-24 に FAA 7930.2 Appendix B の正式テーブルで裏取り済み）。
- `focus_tag`（2026-09-24 追加）: `category` を補い、Wグループの中身をさらに粗く仕分ける絞り込み用タグ。**軍事かどうかの自動判定ではない**（あくまで人間が優先的に見る順番を決めるためのラベル）。
  - `restriction` = Rグループ全部(RA/RD/RM/RO/RP/RR/RT)。
  - `flag` = WM(射撃/砲撃。**実データでは花火が多数混入**)・WE(演習)・WF(空中給油)・WR(放射性/有害物質)・WD/WH(爆破)。
  - `watch` = WU(無人機。**実データは民間ドローン届出が大半**)・WL(自由気球)・WC(係留気球/凧)、および未知のWサブタイプ(安全側に倒す)。
  - `leisure` = WA/WB/WG/WJ/WP/WV/WY/WZ(航空ショー・曲技・グライダー・バナー曳航・パラ系・編隊飛行・航空測量・模型飛行)。
  - `plaintext` = QXXXX等、Qコード上分類できず本文(E項)を読むしかないもの。
  - `admin` = R/W以外(空港施設・航法援助施設・運航方式等)。基本的に対象外。
  - 実データ(1,717件)での分布: `admin` 49.9% / `watch` 40.0% / `flag` 5.1% / `plaintext` 4.0% / `leisure` 0.8% / `restriction` 0.2%。**ビューアの既定表示は `admin` を除外**するだけでも見るべき件数が約半分に減る。
  - 次の判断材料が貯まったら、`flag`/`watch` の中を本文キーワード(FORBIDDEN/PROHIBITED/MILITARY等)でさらに絞れるか検討する（ユーザー方針: 今回は保留）。
- `keyword_hit` / `matched_keywords`（2026-09-24 追加）: `raw_text`(本文全文)に対する語句一致。語句リストはユーザーが別ルートで入手したもの(`KEYWORD_LIST` = DANGER/TEMPORARY/CLSD/FORBIDDEN/PROHIBITED/DNG/CLOSED)。大文字小文字を区別しない部分一致で、ヒットした語句を`matched_keywords`に列挙し、1件でもあれば`keyword_hit=true`。
  - **`focus_tag`/`area_group`の絞り込みを置き換えるものではなく、その上に重ねるAND条件**として使う(ビューア側の`notamPassesFilter()`も同様)。
  - **注意**: `CLSD`/`CLOSED`は誘導路・滑走路・駐機場閉鎖など空港運用の事務連絡(`focus_tag=admin`)にも非常に高頻度でヒットする。実データ(1,717件)で`keyword_hit=true`は303件(17.6%)あったが、**その97%(294件)が`focus_tag=admin`**で、`restriction`は3件・`watch`は1件のみだった。単独運用は誤検出が多いが、`focus_tag`の既定フィルタ(`admin`を除外)と併用すれば実質的にノイズは消え、意味のある候補だけが残ることを確認済み。
  - ユーザー提供のNOTAM実物2件(A4703/26・A4705/26、`QRTCA`)はどちらも本文に`TEMPORARY`を含み、`keyword_hit=true`になることを確認済み。

### 5.4 ワークフロー（2026-09-24、Claude Codeによる書き直し後の内容）
元々こちらで用意した版から、実際にリポジトリにpushして運用する段階でClaude Codeが書き直した。**中身（`notam_cn_collect.py`/`notam_cn_probe.py`自体）は無傷**で、変わったのはワークフローYAMLだけ（§10.6のdiff確認済み）。

- **`notam-collect.yml`**: `NMS_ENV`をRepository Variableではなく**`workflow_dispatch`の入力(`env`、既定`staging`)**に変更。cron-job.orgからの自動起動(`{"ref":"main"}`のみ、入力省略)でも既定値`staging`が使われるので運用上問題ない。同時実行防止・失敗時コミットしない・`git pull --rebase`+push retry(5回)という設計は維持。
- **`notam-probe.yml`**: 同様に`env`入力化（既定`staging`）。**ただし2点、地味に機能が落ちている**: ①`extra_prefixes`入力（追加ICAO接頭辞指定）が削除 ②アーティファクトアップロードから`if: always()`が消え、**probe失敗時に`probe_out`がアップロードされずデバッグ材料が残らない**。直す場合は`if: always()`を`Upload probe output`ステップに戻すだけでよい。
- **`pages-deploy.yml`（新規追加、Claude Code）**: `geoplot-mil.html`や`notam_out/**`の変更をトリガに`actions/deploy-pages`で自動デプロイする正式な構成。**未確認の点が2つ**: ①リポジトリのSettings→Pages→Sourceを「GitHub Actions」に手動で切り替える必要がある（自動では有効にならない） ②**このリポジトリはPrivateのままのはずだが、無料プランのGitHub PagesはPublicリポジトリのみ対応**。Pro/Team/Enterpriseへのアップグレードをしたか、Publicに切り替えたかをユーザーに確認中（未回答）。Publicにした場合、`raw_text`（NOTAM本文全文）が世界に公開される点は§2・§8-10の再配布条件未確認リスクと合わせて要注意。
- **cron-job.org 側**: `POST https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/notam-collect.yml/dispatches`、ヘッダ `Authorization: Bearer <PAT>` / `Accept: application/vnd.github+json` / `X-GitHub-Api-Version: 2022-11-28`、body `{"ref":"main"}`、間隔10分。PATはこのリポジトリへの書き込み権限が必要。**まだ登録済みかは未確認**（§9）。

### 5.5 `notam_paste_import.py`（2026-09-24 追加。方針転換に伴う新規ツール）
NMS-APIを経由せず、**FAAのNOTAM Search（公開サイト）のArchive機能などから個別にコピー貼り付けしたICAO形式の生テキスト**を、`notam_cn_collect.py`の`--fixture`がそのまま読めるJSON(Feature配列)に変換する。

- 使い方: `python notam_paste_import.py pasted.txt -o pasted_fixture.json`、または`--collect-out-dir`で変換→`--fixture`投入まで一気に実行。
- 貼り付けテキストに複数件のNOTAMが混ざっていても、`<番号>/<年> NOTAM[NRC]`の出現位置で自動的に区切る。パースできないブロックは理由を表示してスキップし、他は処理を続ける。
- `notam_cn_collect.py`の`target_area()`等をそのまま`import`して再利用しているため、対象判定ロジックが二重管理にならない。
- **検証済み**: ユーザー提供のNOTAM実物2件（A4703/26・A4705/26）を変換し、既存の`RealNotams`テストが検証しているのと同じ値（`geometry_source: text-polygon`、`qline_offset_nm`が138.1/0.7、`focus_tag: restriction`）が出ることを確認。同じテキストの再取り込みでも重複しない（`id`を`PASTE-<番号>-<年>`で合成し安定させているため）ことも確認済み。
- **既知の割り切り**: 貼り付けテキストには発行日時(`issued`)が無いことが多く、有効開始時刻(B項)で代用。`accountId`（発行元）は取得不能なので常に空。

---

## 6. テスト状況（2026-09-24時点35件、2026-09-27時点36件、うち大半は合成データ・一部は実データを踏まえた回帰テスト）

> 2026-09-27追記: テストファイルは`dev/test_notam_cn_collect.py`に移動(`notam_cn_collect.py`と
> 同じ階層からの相対import前提のため、実行は`cd dev && python -m pytest test_notam_cn_collect.py`
> または `python -m pytest dev/test_notam_cn_collect.py`)。REF FIR(§15.4)の回帰テストを1件追加。

`python dev/test_notam_cn_collect.py`（リポジトリのルートから）。カバー範囲: 対象抽出(米ARTCC・モンゴル・日本の除外、香港・マカオ・台湾の包含)、複数地点A)、ZXXX、図形の優先順位、E項多角形の度分秒パース(閉じたリング・複数エリア・分60超の無効・1点のみは面にしない)、Q項座標パース、**API座標未設定(Point[0,0])の除外**、冪等性、更新、置換/取消、取消通知が先に届くケース、即時Archive、Archive月＝失効月、Archive再登場でファイル不変、追記のみ、PERM、Q分類、`focus_tag`（実データの主要Qコードで確認）、`keyword_hit`（語句一致・複数ヒット時の順序・プロパティ存在）、実NOTAM2件（多角形採用・ずれ検出・時刻・分類）、APIモード（トークン・窓計算・408再試行・失敗時に meta 据え置き・認証エラー・認証情報なし・24h超ギャップ・bootstrap il(gz)・il解析不能・locations・dry-run・出力に秘密が混入しない）、**周辺国参考FIR(REF)の完全一致判定**(2026-09-27追加)。
- **2026-09-24、staging環境の実データで検証済み**: probe実行(delta 164件)、`--fixture`での通し確認(40件)、`--bootstrap il`での初回投入(1,871件抽出)。いずれも正常終了。**さらに実際のリポジトリ上でも収集が走り、120件の実データが入った状態を確認済み**（§10.6）。
- 開発中に見つかった実バグ: 初期ロードがJSONでない場合に例外で落ちる → 説明付きエラーに修正済み。
- **実データ検証で見つかった実バグ（修正済み）**: APIが座標未設定を`Point[0,0]`で返すケース（複数FIRにまたがるトリガーNOTAM等）を実座標として誤採用していた。`build_geometry()`を修正し、回帰テスト`test_zero_zero_point_rejected`を追加。
- **`notam_paste_import.py`は上記スイートに含まれず、手動確認のみ**（§5.5）。ユーザー提供の実物2件で既存の`RealNotams`と同じ結果が出ることを個別に確認済みだが、専用のユニットテストは未整備。

## 7. 設計判断とその理由（要点）

1. **クライアント側で絞る**: API にFIR/国フィルタが無いため、全世界の差分→中国系のみ抽出。差分が大きすぎる(408)場合は bootstrap で補う。
2. **4文字トークン判定**: 米ARTCC(3文字)との衝突回避＋A)複数地点対応。
3. **E項多角形を最優先**: Q項の中心は誤りうる（実測138NMずれ）。
4. **JSON+GeoJSONの二本立てで状態を持つ**: 真実の源は `state_active.json`（レコード）。`notam_cn.geojson` は派生物として毎回再生成。
5. **失敗は赤く**: 取得失敗は終了コード2、`last_success` 不進行。
6. **コミット抑制**: 変化なしなら書かない・meta は間引く。それでも変化があれば都度コミットされる（中国側の更新頻度次第で1日数十〜百コミットの可能性。増えすぎるなら data ブランチや Pages への分離を検討）。
7. **データ量**: NOTAM は年間400万件超発行される（FAA の説明。対象範囲の内訳は未確認）。中国分に絞れば有効側は小さいが、Archive は月別ファイルで増え続ける。リポジトリ肥大は運用しながら要監視。
8. **プッシュ配信(SWIM)は採用しない**: 常時接続が必要で cron 方式と相性が悪い。

---

## 8. 未確認・未確定事項

**2026-09-24、staging環境の実データ（probe + `--bootstrap il` 1,717件）で以下が確定した。**

| # | 事項 | 結果 |
|---|---|---|
| 1 | 認証情報の環境（fit / staging / prod） | **staging と確定**(`report.json`の`host`が`api-staging.cgifederal-aim.com`)。**prod での確認はまだ**。本番相当のデータかは未確認 |
| 2 | 中国系NOTAMが実際に返るか、件数、`classification` | 返る。直近差分(23h窓)で164件(CN 125 / TW 38 / HK 1)、**全件`classification: INTL`**。`--bootstrap il`(`classification=INTERNATIONAL`)でも中国系1,871件取得でき、**中国分はINTERNATIONALに含まれることを確認** |
| 3 | 複数地点A)の`icaoLocation`への入り方／`ZXXX` | 2回の実データ(probe 40件、il 1,871件)とも**該当ゼロ**。空白分割ロジックは無害だが未検証のまま |
| 4 | APIの`geometry`が面を含むか | **含まない**。probe(164件)・il(1,717件)とも `geometry_source: api`(面)は0件。E項テキストのパースが唯一の面情報源、という設計判断は正しかった |
| 5 | 差分の窓の上限（408） | 23hは通ることを確認(11秒)。**24hちょうどまで通るかは未検証**(probeは成功したら打ち切る仕様のため) |
| 6 | `--bootstrap il`のファイル構造・中国分がINTERNATIONALに入るか | **成功**。GEOJSON→gz解凍→JSONのパースは実データで問題なく動作。全世界45,504件中、中国系1,871件抽出 |
| 7 | 取消・置換の返り方、`cancelationDate`の意味 | 本文参照(`NOTAMC G4294/26`)からの`ref_number`抽出は実データで機能を確認。加えて元NOTAM側に`cancelationDate`が別途入る実例も確認（`effectiveEnd`とは異なる値）が、**発生タイミングの正確な意味はなお不確か** |
| 8 | `accountId`/`locationseries`によるサーバ側絞り込み | 差分取得では`accountId`(`ZBBBYNYX`等)が入り、`locationseries_cn_accountIds`も少数に集約（絞り込みに使えそう）。**ただし`--bootstrap il`側では`account_id`が全1,717件で`None`**（il固有。原因未確認＝ilのレスポンス自体に無いのか、収集側のパース漏れか）。`accountability`パラメータ自体はまだ未使用 |
| 9 | QコードのR/W分類・各コードの意味をICAO公式表で確認 | 未着手 |
| 10 | NMSデータの再配布条件（GitHub公開の可否） | 未着手。FAAへの照会が必要 |
| 11 | レート制限・利用条件（個人・非商用の可否） | 未確認 |
| 12 | 国内向けのみのNOTAM（中国）が見えるか | 中国系はprobe・ilとも**全件INTL**で、DOMESTIC分類は1件も出現せず。**§2のリスク1（国際配信分のみ見えている可能性）を裏付ける結果**。国内向けNOTAMが別途存在するとしても、このAPI経由では見えない可能性が高い |

**新たに判明した事項（実データで気づいたもの）**

| 事項 | 内容 |
|---|---|
| **座標(0,0)バグ（修正済み）** | APIが座標未設定を`Point[0,0]`で返すケース（複数FIRにまたがるトリガーNOTAM等）を、収集スクリプトが実座標として誤採用していた。probe 40件中9〜11件、il 1,717件中32件で発生。`build_geometry()`を修正し、`(0,0)`は`no_geometry`扱いに変更。回帰テスト追加済み（§6） |
| 台湾(TW)がCNより件数が多い理由 | `--bootstrap il`結果でTW 901件・CN 781件。中身を見ると`RCAA`(台北FIR)だけで356件。主因は**`QWLLW`(無人気球打ち上げ)の予告NOTAMが1ヶ月ごとに別NOTAM番号で反復発行**されているため。台湾側の発行慣行による水増しで、実際の事象数の多寡を意味しない |
| `ping_http: 400` | probeの`/v1/ping`が400を返す。認証・`/v1/notams`本体は正常に通るため致命的ではなさそうだが、原因不明のまま残る |
| 地点別個別問い合わせの件数感 | `per_location`は地点あたり数十〜300件超（`RCAA`375件等）。`--bootstrap locations`方式も現実的な選択肢と確認 |

---

## 9. 次のステップ（推奨順、2026-09-27更新）

**今の方針**: ビューア統合(v1.9.0、MSA本番リポジトリへの合流)まで完了。次はこれを実際のGitHub
リポジトリに反映し、実運用に乗せる段階。

1. **`geoplot-mil_msa+notam_2026-09-27.zip`を実リポジトリにpush**する（このチャット上での作業は
   ローカルファイルの編集・構文検証・git衝突のローカル再現テストまでで、実リポジトリへの反映は
   まだ行っていない）。
2. **GitHub Secrets/Variablesを設定**: `NMS_CLIENT_ID`/`NMS_CLIENT_SECRET`(Secrets)、`NMS_ENV`
   (Variables、既定`prod`)。未設定のままだと`notam-collect.yml`は失敗するだけ(既存データは
   壊れない、安全側)。
3. **`notam-collect.yml`を手動実行(workflow_dispatch)して初回疎通確認**: NMS-APIのprod接続が
   実際に通るかはまだ未確認（§8）。失敗する場合は`--bootstrap il`または`--bootstrap locations`
   での初回全件投入が必要（`state_active.json`が空の状態で差分取得だけだと直近6時間分しか
   取れないため）。
4. **並行して、当面はFAA NOTAM Searchでの手動サンプル収集も継続**: `notam A####/26`のような
   キーワードでWeb検索すると、FAAが生成したPDF(`notams.aim.faa.gov/notamSearch/createNotamPdf`)
   が見つかることがある（§15参照、実際に28件処理済み）。見つかった生テキストを
   `notam_paste_import.py`で`--fixture`用JSONに変換して取り込める。ただし同じ番号が複数国で
   使われるため、**A項(場所)が中国国内〜沿岸のものかを必ず確認**すること。
5. **cron-job.orgへの登録**: `notam-collect.yml`を10分おきに叩く外部cronの設定（`scrape.yml`の
   30分おきと同じ仕組み）。登録状況は未確認のまま。
6. **`pages-deploy.yml`とリポジトリの公開範囲を確認**: Private repoのままだと無料枠のGitHub Pages
   が機能しない可能性がある（§10.6、未解決のまま持ち越し）。
7. 余裕があれば§8のNMS-API残課題（prod確認・再配布条件）も進める。

## 10. ビューア(`geoplot-mil.html`)統合 — 2026-09-24 実装済み（v1.9.1）

> **⚠️ 2026-09-27時点で古い**: このセクションは初回実装(v1.9.1、ヘッダーの🛰ボタン→ドロップダウン
> パネル方式)の記録。その後 v1.9.2 でMAP(自動更新)/MAP(手動)と同格の第3モードタブに変更され、
> v1.9.3〜v1.9.6でカード表示・絞り込みUIも大きく変わり、2026-09-27にMSA本番リポジトリへ統合
> (v1.9.0、バージョン番号はMSA側のカウンタにリセット)された。**現在の実装は§15を参照**。
> 以下は「絞り込みの考え方の変遷」を追う上での記録として残す。

MSA側バンドル(`geoplot-mil_bundle_2026-09-20.zip`)の `geoplot-mil.html` に、NOTAMレイヤーを追加した。
MSA(`warningsByUrl`/`msaLayer`)とは完全に別のデータモデル・別レイヤー(`notamLayer`)・別UIで、
`enterMode()` のモード切替(MAP自動更新/MAP手動)の対象外にしてある — **どちらのモードでも
ヘッダーの🛰ボタンからNOTAMパネルを開閉でき、地図上のNOTAM図形も常時表示され続ける**（ユーザー
要望「MAP(自動更新) MAP(手動) タブにNOTAMを追加」への対応）。

**実装したもの**:
- ヘッダーに🛰ボタン(未読件数バッジ付き)→クリックで開くパネル(`#notamPanel`、`.notif-panel`と
  同じ「ヘッダーボタン→ドロップダウン」の作り)。
- 接続: `notam_out/notam_cn.geojson` へのURL接続(10分ごと自動再読込、`notam-collect.yml`の
  収集間隔に合わせた)。MSA側が`?geojson=`で自動接続していれば、`/msa_out/`→`/notam_out/`の
  置換で `notam_cn.geojson` のURLを自動推測して接続する(`?notam=`で明示上書きも可)。
- タブ: Active / Upcoming / Archive / All。Archiveは年月(`<input type="month">`)を選んで
  `archive/notam_YYYY-MM.geojson` を都度読み込み(読み込んだ月ぶん`notamArchiveFeatures`に蓄積)。
- 絞り込み: 地域(CN/HK/MO/TW、既定全ON)と `focus_tag`(restriction/flag/watch/leisure/
  plaintext/admin。**既定でON: restriction/flag/watch、既定でOFF: leisure/plaintext/admin**
  — 実データでの分布・目的別分類の検討に基づく初期値)、および**`keyword_hit`(語句一致、
  既定ON)**（v1.9.1で追加、§10.5）。
- 地図描画: GeoJSONをそのまま`L.geoJSON()`に渡すだけ(MSA側のような独自座標パースは不要、
  サーバー側で完成済みのため)。`geometry_source`が`qline-*`(Q項からの推定図形)のものは
  破線で描き分け。色は`focus_tag`ごと(restriction=赤/flag=琥珀/watch=シアン/その他=グレー)。
  Archiveは既定では地図に出さない(`ArchiveもMAPに表示`チェックボックスで任意にON)。
- ポップアップ・一覧: タイトル・地域・ICAO地点・Qコード・`focus_tag`バッジ・**`matched_keywords`
  バッジ**(v1.9.1)・有効期間・(Q項推定図形なら)中心ずれ注記・本文全文。

**未着手・今後の検討事項**:
- Shapefile/PDF出力へのNOTAM反映（MSA側の警報と同様、今回は対象外のまま）。
- Archiveの月選択は都度1か月ずつ手動読み込み。複数月をまたいだ一括読み込みUIは無し。
- 動作確認はNode.jsでのJS構文チェックと、実データ(`--fixture samples/cn_sample.json`)で
  再生成した`notam_cn.geojson`のプロパティ形とJS側の参照キーの突き合わせまで。実ブラウザでの
  表示確認はまだ行っていない。

### 10.5 `keyword_hit`(語句一致絞り込み) — 2026-09-24 追加（v1.9.1）
ユーザーが別ルートで入手した語句リストを本文照合に使う機能。設計方針は**「収集時点でデータを
消さず、ビューア側の表示条件として`focus_tag`の上に重ねる」**（ユーザー合意済み）。

- 語句リスト(`notam_cn_collect.py`の`KEYWORD_LIST`): `DANGER, TEMPORARY, CLSD, FORBIDDEN, PROHIBITED, DNG, CLOSED`。`raw_text`への大文字小文字を区別しない部分一致。
- プロパティ`keyword_hit`(bool)・`matched_keywords`(配列)を`notam_cn_collect.py`側で計算し、GeoJSONに載せる（§5.3のプロパティ一覧参照）。
- ビューアは`notamPassesFilter()`で`area_group`・`focus_tag`・`keyword_hit`を**すべてAND条件**で評価する。
- **実データ検証**(1,717件): `keyword_hit=true`は303件(17.6%)。うち94%(284件)が`CLSD`単独ヒットで、**その97%(294/303件)が`focus_tag=admin`**（空港運用の事務連絡）だった。`restriction`タグでのヒットはわずか3件、`watch`は1件。→ **`keyword_hit`を単独で使うと`CLSD`のノイズにほぼ埋もれるが、`focus_tag`の既定フィルタ(`admin`除外)と組み合わせれば実質的にノイズは消える**ことを確認済み。
- ユーザー提供のNOTAM実物2件(A4703/26・A4705/26、`QRTCA`)はどちらも本文に`TEMPORARY`を含み、`keyword_hit=true`になることを確認済み。

### 10.6 GitHubリポジトリの実在化とClaude Codeによる変更 — 2026-09-24
ユーザーがPrivateリポジトリを新規作成し、こちらが用意したファイル一式をpush。その後**Claude Codeにワークフローを触ってもらい**、実際にstaging環境で収集を走らせた状態(zip)を共有してもらってレビューした。

- **Python/HTML(`notam_cn_collect.py`/`notam_cn_probe.py`/テスト/`geoplot-mil.html`)は無変更**（diffで確認済み）。Claude Codeが変更したのはワークフローYAMLと、新規追加の`pages-deploy.yml`のみ（§5.4に詳細）。
- **実データが入っていた**: `notam_out/meta.json`で`active_records: 126`・`active: 8 / upcoming: 112`、最終成功`2026-09-24T09:24:57Z`。`notam_cn.geojson`は120件、スキーマも29プロパティのまま欠けなし。`archive/notam_2026-09.geojson`も13件存在。→ **収集スクリプトは実運用で無傷のまま動作することを確認**。
- **要確認のまま残っている点**: `pages-deploy.yml`が追加されたことで、リポジトリがPrivateのままGitHub Pagesを使おうとしている可能性がある（無料枠はPublic限定）。Publicに切り替えた/有料プランに上げたのかは**ユーザーに確認したが、まだ回答を得ていない**。次のセッションで要フォロー。

### 10.7 絞り込みの全体像（ユーザーから質問があったため整理・記録）
「ビューアに表示されるものは何がどう絞られた結果か」という問いに対する回答をここに保存しておく。

1. **収集時点(`notam_cn_collect.py`)で地域を絞る**: `icaoLocation`/`affectedFir`の頭2文字がCN/HK/MO/TWの対象プレフィックスでなければ、そもそも`notam_out/`のファイルに入らない（§5.2）。
2. **ビューアで効く3つのフィルタ、すべてAND条件**:
   - 地域(`area_group`)チェックボックス — 既定は**全部ON**(①で絞ってあるものをさらに選別する用)
   - **`focus_tag`チェックボックス — 既定でrestriction/flag/watchのみON**。実データで`admin`だけで約半分を占めるため、実質これが一番効いている絞り込み。
   - **`keyword_hit` — 既定ON**（§10.5）
3. **地図(MAP)表示だけに効く追加条件**: Archiveは既定では地図に出さない(「ArchiveもMAPに表示」で任意にON)。ヘッダーの「MAPに表示」チェックボックス(既定ON)でNOTAMレイヤー全体をオンオフできる。一覧(パネル内リスト)側はタブ切り替えでArchiveも見られる。

### 10.8 中国関連FIRコード一覧（参考情報、2026-09-24 ICAO資料で確認）
ユーザーからの質問に答える過程で確認した、`AREA_BY_PREFIX`の裏付けとなるFIRの正式リスト。出典: ICAO Asia-Pacific FIR一覧PDF。

| FIR名 | ICAOコード |
|---|---|
| 北京 Beijing | `ZBPE` |
| 瀋陽 Shenyang | `ZYSH` |
| 上海 Shanghai | `ZSHA` |
| 広州 Guangzhou | `ZGZU` |
| 武漢 Wuhan | `ZHWH` |
| 三亜 Sanya | `ZJSA` |
| 昆明 Kunming | `ZPKM` |
| 蘭州 Lanzhou | `ZLHW` |
| ウルムチ Urumqi | `ZWUQ` |
| 香港 | `VHHK` |
| 台北 Taipei | `RCAA` |

マカオは独自FIRを持たず香港FIRの一部（空港コードは`VMMC`）。中国本土9 FIRの頭2文字は`ZB/ZY/ZS/ZG/ZH/ZJ/ZP/ZL/ZW`で、`notam_cn_collect.py`の`AREA_BY_PREFIX`(`ZB ZG ZH ZJ ZL ZP ZS ZU ZW ZY`)とちょうど対応する。**`ZU`だけはFIR名に存在しない**が、`ZUUU`(成都)等の空港コードを拾うために含めてある(§5.2に注記あり)。`ZXXX`は複数FIRにまたがるNOTAMの仮コード。

### 10.9 FAA NOTAM Searchのアーカイブ検索について（参考情報、2026-09-24調査）
FAA公式の公開ツール(`notams.aim.faa.gov`)には「Archive Search」機能があり、**過去5年分**を遡れるが、**地点(ICAO/FIRコード)と日付を1組ずつ指定する仕様**で、複数FIR・複数期間の一括検索やCSV一括ダウンロードは無い。**文書化された公開APIも無い**。裏のAJAXエンドポイントを叩けば技術的には自動化できる可能性があるが、非公式インターフェースへの依存になるため推奨しない（旧`external-api.faa.gov/notamapi`を使わなかった判断と同じ理由）。有料の第三者サービス(`notamhistory.com`、1回$4.99・世界中のFIR対応・過去2年分)も存在するが、FAA公式ではない再販業者でありデータ出所・再配布条件の不透明さはNMS-APIと同種かそれ以上。**→ 当面は手動での個別検索が現実解**（§9のステップ1）。

---

## 11. 既知の制約・残課題

- E項のパースは**座標列の多角形のみ**。「半径◯KMの円」「弧」「扇形」「回廊(線)」などの記述は未対応（1点だけの記述は面にせず、Q項の円/点にフォールバック）。
- 座標書式は `N/S…E/W…`（度分秒 or 度分）のみ。`DDMMSS N` 形式など別書式は未対応。
- 複数エリアの列挙は「先頭点に戻ったらリングを閉じる」規則。閉じない列挙は1つのリングになる。
- 反子午線(±180°)をまたぐ図形は考慮していない（対象地域では不要）。
- 高度は文字列のまま保持（`lower/upper`, `min_fl/max_fl`）。E項の「HEIGHT: 35,000M」等は解析していない。
- 開始・終了時刻はAPIの `effectiveStart/End` に依存。`schedule`(日次スケジュール)は `valid_raw` に文字列で入れるのみ。
- 種別 N/R/C は本文の `NOTAMN/R/C` を優先（合成データではAPIの `type` と本文が食い違うことがあった）。
- 対象判定は地名指標の接頭辞ベース。中国が管轄する空域を他国のコードで示すNOTAMは拾えない。

---

- **`--bootstrap il`では`account_id`が取得できない**（全件`None`）。差分取得では正常に入る。原因未確認（§8-8）。accountId前提の機能（accountabilityフィルタ等）は差分取得側のみで使うこと。
- 台湾(TW)の件数がFIR単位(`RCAA`)の反復発行(無人気球予告等)で大きく水増しされる。件数を「事象の多さ」と解釈しないよう注意（§8参照）。

## 12. 注意事項（重要）

- **認証情報**: `NMS-API-Pre-Prod-soapui-project_sample.xml`（ユーザー提供）に、**OAuthのクライアントID・シークレット・アクセストークンが平文で入っていた**。ユーザー自身の認証情報なら、このファイルをコミット・共有せず、FAAに再発行を相談すること。共通のサンプル値なら影響は小さい。本バンドルには含めていない。KEY/SECRET は今後もチャットに貼らず、**GitHub Secrets** に入れる。
- **public公開の可否は未確認**（§2, §8-10）。
- 本バンドルに入れていない再提供物（次のセッションで必要なら再アップロード）: `nms-api.yaml`(1.0.18・最新)、`FAQ_NMS-API.pdf`、`nms-api_curl_examples.txt`、サンプルXML/JSON。`nms-api-1_0_17.yaml` は旧版なので不要。SoapUI サンプルは上記の理由で再アップロードしないこと。
- MSA側のバンドル(`geoplot-mil_bundle_2026-09-20.zip`: `HANDOFF.md`, `geoplot-mil.html` v1.8.0, `msa_scraper.py`, `commit_state.py`, `state.json`, `military.geojson`)は別管理。NOTAM機能はMSA側のファイルを変更していない。

---

## 13. 新しいセッションでの再開手順（2026-09-27更新）

**成果物は `geoplot-mil_msa+notam_2026-09-27.zip` に集約済み**（MSA機能込みの完全版、これ1つで
足りる）。旧来のNOTAM単体バンドルは持ち越す情報がなければ不要。

1. `geoplot-mil_msa+notam_2026-09-27.zip`をアップロードし、「引き継いで」と伝える。
2. 本ファイル(`HANDOFF_NOTAM.md`)の§0・§9・§15を読む。
3. §9の次のステップ(実リポジトリへのpush・Secrets設定・初回Actions実行確認)から進める。
4. 変更後は `cd dev && python -m pytest test_notam_cn_collect.py` で36件(以降は追加分)が通ることを確認。
5. `geoplot-mil.html`を編集した場合は、必ずNode.jsでのJS構文チェック(`new Function(js)`)と
   HTMLのdivタグ対応チェックを行う（§15の「作業手順の教訓」参照。実際にこの2つを怠って
   バグを作り込んだ実例が複数ある）。
6. `notam_paste_import.py`を変更した場合は、ユーザー提供の実物サンプル(§15の28件、または
   §4のA4703/26・A4705/26)を通して期待通りの値が出ることを確認する。

---

## 14. 2026-09-24 セッションログ（実データ検証〜方針転換まで）

このセッションで行ったこと。今後の参考・再現用に記録する。

1. **probe をstaging環境で実行**（ユーザーがGitHub Actionsで実行）。結果(`report.json`/`cn_sample.json`/`locationseries_cn.json`)を共有してもらい、§8の#1〜5・8・12を確定。
2. **`notam_cn_collect.py --fixture cn_sample.json`** で通し確認。冪等性・Archive振り分け・取消リンク(`ref_number`)の抽出が実データで動作することを確認。
3. **地図プロトタイプ**（Visualizerで作成・保存はしていない。チャット上で確認のみ）: D3 + world-atlas(110m)の基盤地図に、実データの座標を点/円としてプロット。ホバーで詳細表示。
4. **`--bootstrap il --out-dir ./notam_out_local`** をユーザーの手元で実行。全世界45,504件から中国系1,871件を抽出、1,717件をgeojson化。結果(`notam_cn.geojson`)を共有してもらい、§8の#6を確定。
5. **バグ発見・修正**: `notam_cn.geojson`を分析中、`geometry_source: api-point`のうち座標(0,0)のものが多数(40件サンプルで11件、1,717件中32件)見つかった。原因はAPIが座標未設定を`Point[0,0]`で返し、`build_geometry()`がそれを実座標として採用していたこと。修正し、回帰テスト`test_zero_zero_point_rejected`を追加(§6)。
6. **台湾(TW)901件の内訳確認**: `RCAA`(台北FIR)だけで356件、うち大半が無人気球打ち上げの月次反復NOTAM(`QWLLW`)と判明（§8参照）。
7. **地点集計の気泡地図**（120→114地点、座標(0,0)分は除外）を再度Visualizerで作成し、規模感を確認。
8. **成果物をzipにまとめてセッション区切りを一度作成**。
9. **Qコードの目的別分類を実データで検討**: ICAO Doc 8126 / FAA 7930.2 Appendix Bの正式な第2・3文字テーブルをWeb検索で確認し、1,717件を機械的に再分類。`QWMLW`(コード上は射撃/砲撃)の中身が実は**花火**が大半、`QWULW`(無人機)の中身は**民間ドローン届出**が大半など、コード名を鵜呑みにできない実例を多数発見。これを踏まえ`focus_tag`(restriction/flag/watch/leisure/plaintext/admin)を設計・実装し、テスト追加(§5.3)。
10. **ビューア(`geoplot-mil.html`)へのNOTAMレイヤー統合**: v1.8.6→v1.9.0。MSA側のデータモデルを流用せず、独立したレイヤー・パネルとして実装(§10)。
11. **GitHub公開手順の説明**: 別リポジトリ(Private推奨)での公開手順をstep_card形式で案内。
12. **GitHubアップロード用ファイル一式をzipで提供**。
13. **「Claude Codeを使った方が楽か」という質問に回答**: 今回のようなコード編集主体の作業はClaude Code向き、設計相談はこのチャット向きと整理し、`claude_code_desktop`を推薦。
14. **Claude Codeで実際に作業した後の状態(zip)をレビュー**: Python/HTMLは無変更、ワークフローが書き直され`pages-deploy.yml`が新規追加されていることを確認。実データ(120件)が収集済みであることも確認(§10.6)。
15. **方針転換**: ユーザーから「FAAのArchive検索機能を使って過去のNOTAMを個別に提供し、それをサンプルに進める」と提案があり合意。`notam_paste_import.py`(FAA NOTAM Search等からの生テキストを`--fixture`用JSONに変換)を新規作成し、実物2件で動作確認(§5.5)。
16. **中国関連FIRコードの確認**: ICAO資料をWeb検索し、9 FIR＋香港＋台湾のリストを確認・記録(§10.8)。
17. **FAA NOTAM Searchのアーカイブ検索の制約を調査**: 地点+日付を1組ずつ指定する仕様で、公式な一括/自動化手段が無いことを確認(§10.9)。
18. **`keyword_hit`(語句一致絞り込み)を実装**: ユーザー提供の語句リスト(DANGER/TEMPORARY/CLSD/FORBIDDEN/PROHIBITED/DNG/CLOSED)で`raw_text`を照合。`focus_tag`の上に重ねるAND条件として設計・実装し、実データで`CLSD`のノイズが`focus_tag=admin`除外と組み合わせることでほぼ解消することを確認(§10.5)。ビューアにもチェックボックスと語句バッジを追加(v1.9.1)。
19. **ビューアの絞り込みの全体像について質問があり、整理して回答**(§10.7として記録)。
20. **本セッションのまとめとして本資料を更新**。

**このセッションで得た実データサンプル**（`samples/`に同梱。再配布条件は未確認のため取り扱い注意・§8-10）
- `samples/report.json` — probe実行結果のサマリ（staging、23h窓、delta_cn_total=164）
- `samples/cn_sample.json` — probeで取得した中国系NOTAM生データ40件
- `samples/locationseries_cn.json` — locationseriesの中国系地点一覧
- `samples/notam_cn_il_bootstrap.geojson` — `--bootstrap il`の出力(1,717件、**修正前のバグ入り**。座標(0,0)の32件が混入したままなので、再利用する際は修正後の収集スクリプトに通し直すこと)

---

## 15. 2026-09-27 セッションログ（UI仕上げ〜MSA本番リポジトリへの統合まで）

長いセッションだったため、今後の参考・再現用に詳しく記録する。

### 15.1 NOTAMパネルの表示形式を航行警報側に揃える(v1.9.2→v1.9.3)
- ユーザーからのスクリーンショット指示で、NOTAM一覧の各カードを航行警報側(`.warn-item`)と
  同じ見た目(枠線ボックス・角丸・余白、restriction/flag/watchは枠線とタイトル色を強調)に統一。
  有効期間も`.validity-badge`の色分きバッジ表示に変更(active=緑/upcoming=シアン/archive系=グレー)。
- 個々のNOTAMに「地図に表示」チェックボックスを追加(`notamHiddenMapIds`、キーは
  `notamFeatureKey()`の安定キー。featureは10分おきの再取得で配列ごと差し替わるため、feature
  自体ではなく安定キーで状態を持つ設計)。タブ内一括表示/非表示ボタンも追加。
- 「原文を開く」「PDF出力」「Export to Shp」はMSA側が警報検索サイトの元URL・詳細取得済み
  レコードを前提にした機能で、NOTAM側のGeoJSONレコードには対応するURLが無いため**追加しなかった**
  (意図的な差)。
- **教訓**: この作業中、リポジトリの実体が複数の派生版に分岐していることが判明した。最初に
  もらったzip(NOTAMがヘッダーの🛰ドロップダウン)を編集して渡したところ、ユーザーから
  「もともとのhtmlが古かったみたい」と指摘され、実際に使われている版(NOTAMが独立モードタブに
  再構成済み)を再アップロードしてもらって同じ変更を当て直した。**複数セッション・複数ツール
  (このチャット/Claude Code)が同じリポジトリを触っている場合、作業前に必ず「これが最新か」を
  確認すること**。

### 15.2 FAA NOTAM Searchからの手動サンプル収集を実運用（28件処理）
- ユーザーが提示したNOTAM番号(`A4696/26`等)をこちらで検索しようとしたが、
  `notams.aim.faa.gov/notamSearch/`はAngular SPAでフォーム送信が必要、`web_search`ツールでは
  ヒットしないことを確認。TinyFish(MCP接続の第三者Webエージェント)で試したが、
  「No NOTAMs found」という誤った結果を返し、動作も遅く、**ユーザー判断で使用中止**。
- **代替手段が判明**: ユーザーが普通にGoogle検索で`notam A4696/26`のように検索したところ、
  Googleの検索結果(AI概要)がFAAの生成PDF
  `https://notams.aim.faa.gov/notamSearch/createNotamPdf?...`(パラメータ不明、URLを直接構築
  してのfetchはツールの制約上できない)への直リンクを提示してくれた。ユーザーがそのPDFの
  スクリーンショット/PDFファイル自体を貼ってくれる、という運用に落ち着いた。
- **同一番号の衝突に注意**: `A####/YY`という採番はFAA固有ではなく、各国のNOTAM発行局が
  独自に振っている(ネパール・カタール等でも同じ番号体系を使う)。届いたPDF/テキストの
  **A項(場所)が中国国内〜沿岸のFIRかどうかを必ず確認**してから取り込むこと。
- 最終的に、ユーザーから「私が軍事と判定したサンプルのすべて」として23個のPDF+テキスト
  ドキュメント1件(zip、Shift-JISファイル名で日本語フォルダ名だったため`cp437→cp932`変換で
  文字化けを解消して展開)が提示され、これを**唯一の正**として処理し直した。
  - 結果: 28件、重複除いて全部取り込み成功。全件A項が中国国内FIR(ZBPE/ZLHW/ZWUQ/ZSHA/ZPKM)、
    Q)コードは27件が`QRDCA`(危険区域設定)、1件(`A0799/26`)だけ`QARLC`(航空路区間閉鎖)。
    全件`text-polygon`でポリゴン生成成功、全件有効期間切れでArchive行き。
  - **共通パターンの観察**(ユーザーからの依頼で分析、実装はしていない): 垂直制限は全件
    `SFC-UNL`、文言はほぼ定型(「A TEMPORARY DANGER AREA ESTABLISHED BOUNDED BY: …
    AIRCRAFT ARE FORBIDDEN TO FLY INTO THE AREA.」)。発行FIRはZLHW(蘭州)・ZWUQ(ウルムチ)に
    偏り(28件中20件)、大半が70〜210分の短時間。例外2件(上海`ZSHA`)だけ「CIVIL ACFT ARE
    FORBIDDEN」(民間機のみ禁止＝非民間機の活動を示唆する書きぶり)で、継続時間も8〜9時間と
    長い。この観察を踏まえたフィルター案(垂直制限・継続時間・発行FIRの偏り・特定表現・
    多角形の形状を重み付けスコアとして使う案)を提示したが、**サンプルが「軍事と判定した
    ものだけ」で偽陰性側の検証ができないため実装は保留**。

### 15.3 `notam_paste_import.py` の実バグを2件発見・修正
1. **Q)行の誤重複バグ**: `_NUMBER_TYPE`正規表現の「置換元NOTAM番号(ref)」を拾うオプション
   グループが`\s+`(改行含む)を使っていたため、`"A4696/26 NOTAMN\nQ)ZWUQ/QRDCA/...`のように
   1行目と2行目の間に改行がある場合、Q)行の先頭部分を誤ってrefとして拾ってしまい、
   `raw_text`(→`keyword_hit`の判定対象)にゴミが混入していた。`\s+`を`[ \t]+`に変更して
   改行をまたがないよう修正。
2. **Q)行の座標欄が空の場合に全体がスキップされる問題**: 実データ`A0799/26`(区間閉鎖NOTAM)で、
   Q)行が`Q) ZLHW/QARLC/IV/NBO/E/000/999/`のように末尾の座標・半径が空だった。従来は
   `_QLINE`正規表現がこれを必須としていたため丸ごとスキップされていた。座標欄を
   `(?:(\d{4}[NS]\d{5}[EW])(\d{2,3}))?`と省略可能にし、`None`のまま`notam_cn_collect.py`の
   `build_geometry()`(E項ポリゴンを優先するロジック)に委ねるよう修正。**座標を推測で
   埋めることはしていない**(一度そうしかけたが、データ捏造になるためすぐ修正)。
- 両修正とも既存テスト35件を壊さないことを確認済み。

### 15.4 表示条件・UIの再設計(v1.9.4→v1.9.6)
ユーザーから3段階の要求があり、都度実装・テスト:

1. **v1.9.4**: 表示対象の地域を拡張。中国・香港・マカオ・台湾に加えて、周辺国の特定FIR
   (`RPHI`マニラ/`KZAK`オークランド洋上管制/`RJJJ`福岡/`RKRR`仁川/`ZKKP`平壌)を追加。
   **プレフィックスではなく4文字完全一致のみ**(`AREA_BY_EXACT`という別枠)で拾う設計に
   した。理由: `K`や`RJ`のような2文字プレフィックスで拾うと米国全土・日本全国のFIRまで
   混入してしまうため。回帰テスト(`test_ref_fir_exact_match_not_prefix`)も追加し、
   同じ2文字だが対象FIRではない地点が正しく除外されることを確認。
   同時に、ビューア側の表示条件として「E項の実座標ポリゴンのみ」(`geometry_source:
   text-polygon`)を採用し、Q項由来の円・点は収集はするが表示しないことにした
   (`notamHasRealPolygon()`)。**収集自体はnotam_out系ファイルに全部残る**。
2. **v1.9.5**: 地域/分類(`focus_tag`)/語句一致(`keyword_hit`)のチェックボックス絞り込みと
   説明文を全部撤廃。「収集条件(ポリゴンありのCN/HK/MO/TW/REF)に合致するものは全部
   ビューワに出す」方針に単純化。地域の絞り込みは、航行警報側の「海事局」ドロップダウンと
   同じ方式でFIR単体選択のドロップダウンに置き換え(`notamFirFilter`/
   `updateNotamFirFilterOptions()`。複数FIRにまたがるNOTAMはトークン単位で判定)。
   `focus_tag`のバッジ表示自体は情報として残した(絞り込みの手段ではなく参考表示)。
3. **v1.9.6**: NOTAMパネル冒頭の見出し行(「NOTAM（中国・香港・マカオ・台湾）/ MAPに表示」)と、
   手動URL接続欄(`notam_cn.geojsonに接続`の入力欄+ボタン)を非表示化。ただし要素自体は
   DOMに残し、JS配線・自動接続(`autoStartNotamFromUrl`)はそのまま動作させた
   (「機能としては接続するが見た目はいらない」という要望への対応)。
   **この作業中、str_replaceで`</div>`を1つ多く残してしまうミスをした**（後述15.6）。

### 15.5 MSA本番リポジトリへの統合(v1.8.9→v1.9.0、大きな作業)
ユーザーから「これが最新版の航行警報海域GitHub投稿セット」として`geoplot-mil.html`(v1.8.9)・
`msa_scraper.py`・`commit_state.py`・`HANDOFF.md`・`scrape.yml`一式(zip)が提示され、
「この機能をそのままに、NOTAM収集機能も付与させたい」という依頼があった。

- **診断**: このv1.8.9は、これまで編集していた版(v1.9.x)とは**独立に進化した別系統**だった。
  v1.8.9側には、こちらが持っていなかった改良(地図クリック→左パネルの該当カードへ自動スクロール
  +ハイライト、手動モードの座標コピー機能を廃してShapefileエクスポートに一本化、
  `NOTAM-P`という新しい座標貼り付け書式、MSA接続パネルをDOMごと完全撤去して自動接続のみに
  簡略化)が入っていた。逆にこちらのv1.9.xにはNOTAM機能一式が入っていた。**両方を尊重し、
  v1.8.9のMSA側コードは一切変更せず、NOTAM関連コードだけを追加移植する**方針で作業した
  (diffで全差分を洗い出し、NOTAM由来のハンクだけを手作業で抽出・適用)。
- 適用した追加(CSS/HTML/JS、詳細はコード側のコメント参照):
  - CSS: `.app.mode-notam`グリッド定義、ヘッダーティッカーのnotamモード非表示、
    `.notam-filters`/`.notam-item`/`.notam-tag`一式、`.validity-badge.upcoming`、
    `.mode-tab .tab-n`、`[data-mode-section="notam"]`の表示制御、ダークモード上書き。
  - HTML: `modeTabs`に「NOTAM」ボタン追加、`#notamModePanel`一式(タブ・一括表示ボタン・
    Archive月選択・FIRドロップダウン・一覧・非表示化した手動接続欄)。
  - JS: `const notamLayer = L.layerGroup().addTo(map);`をmsaLayerの隣に追加、
    「8c. NOTAM」セクション一式(NOTAM_AREA_LABEL〜autoStartNotamFromUrl、v1.9.3〜v1.9.6の
    全機能込み)、`enterMode()`を2-way→3-way化(`mode-auto`/`mode-manual`/`mode-notam`の
    3つを排他表示、対象レイヤーも`msaLayer`/`manualLayerGroup`/`notamLayer`の3つを排他表示)。
  - **NOTAM側の接続パネルは、MSA側が既に確立していた「DOM上は残すがCSSで隠し、関数は
    null-safeにする」パターンに合わせて実装**(MSA側の`logLine()`/`updateConnectionUI()`が
    既にそうなっていたのに倣った)。
- `notam_commit_state.py`を新規作成: MSA側の`commit_state.py`(push失敗時にdictレベルで
  マージしてリトライする戦略)と同じ考え方だが、NOTAM側の出力構成(`state_active.json`の
  dict + 毎回re-buildする`notam_cn.geojson` + **追記のみで増え続ける月別archiveファイル**)に
  合わせて書き直した。archiveファイルは`properties.nms_id`をキーにローカル優先でマージする
  関数(`merge_archive_fc`)を追加。`commit_state.py`自体はMSA専用のまま変更していない。
- `.github/workflows/notam-collect.yml`を新規作成（旧`notam-collect.yml`のドラフト
  ―§5.4参照―を土台に、コミット部分だけ`notam_commit_state.py`呼び出しに差し替え）。
  `scrape.yml`とは別の`concurrency.group`("notam-collect")なので、互いを待たせず並行実行できる。
- **実地テスト**: ローカルにbareリポジトリ(`git init --bare`)を作り、2つのクローンから
  同時にNOTAM収集→コミットを実行して意図的にpush衝突を発生させ、以下を確認:
  - `state_active.json`の衝突マージ(2つの異なるNOTAMが両方とも失われずに残る)
  - 同じ月のarchiveファイルへの同時追記(2つの異なるNOTAMが両方ともarchiveに残る、
    重複もしない)
  いずれも正しくマージされ、2回目のpushが「push succeeded after merge」で成功することを確認。
- HTML全体の最終チェック: Node.jsでの`new Function(js)`によるJS構文チェックと、
  正規表現ベースのdivタグ対応チェック(開閉数が0で終わるか)を都度実施。

### 15.6 作業手順の教訓（今後のセッション向け）
- **HTMLをstr_replaceで編集する際は、old_strの範囲が「閉じタグを含むかどうか」を必ず
  意識すること**。v1.9.6の作業で、old_strが親要素の閉じタグの手前で切れていたのに
  new_str側で閉じタグを含めてしまい、`</div>`が1つ多くなるミスをした(表示は大きく
  崩れなかったが、div対応チェックで発覚)。編集直後は必ずdivバランスチェックを行うこと。
- **同じリポジトリが複数の独立した版に分岐しうる**(§15.1・§15.5)。「最新」と言われた
  ファイルが自分の知っている最新と食い違う場合は、まず差分を取って何が独自に進化したのかを
  把握してから、どちらのコードを正とするか(通常は「相手から提示された方」)を決めること。
  ブラウザで実際に動かしているファイルはユーザー側にしか分からないため、Claude Code等の
  別ツールが並行して触っている可能性を常に考慮する。
- **座標やAPIパラメータなど、実データに存在しない値を推測で埋めない**(§15.3-2)。パースで
  必須に見える項目でも、実データが本当に省略している場合は「省略可能」に仕様を直すのが正しい。
- **NOTAM番号は世界共通の一意キーではない**(§15.2)。各国のNOTAM発行局が独立に採番するため、
  同じ番号が複数国に存在しうる。地点(A項)の確認を省略しないこと。

### 15.7 現在の成果物一式
`geoplot-mil_msa+notam_2026-09-27.zip`(このチャットの最終出力)に集約済み:
- `geoplot-mil.html`(v1.9.0、MSA機能無変更+NOTAM機能統合済み)
- `msa_scraper.py` / `commit_state.py` / `.github/workflows/scrape.yml`(MSA側、無変更)
- `notam_cn_collect.py` / `notam_cn_probe.py` / `notam_paste_import.py` / `notam_commit_state.py`
- `.github/workflows/notam-collect.yml`(新規)
- `dev/test_notam_cn_collect.py`(36件、全パス)・`dev/mock_nms.py`
- `HANDOFF.md`(MSA側、末尾にNOTAM統合の要約を追記)・`HANDOFF_NOTAM.md`(本ファイル)

**未着手のまま残っていること**(§9参照): 実リポジトリへのpush、GitHub Secrets設定、
`notam-collect.yml`の実運用での初回実行確認、cron-job.orgへの登録、`pages-deploy.yml`と
リポジトリ公開範囲の整理。

---

## 16. 語句フィルタで採用する語句（2026-09-27決定、未適用）

ユーザー決定: **語句フィルタはまだ適用しない**。将来適用するときは **`TEMPORARY`・`SPECIAL`** を
採用する（これまでのサンプルで軍事関連に多用されているため）。

根拠にしたサンプル（ビューアで多角形として表示されるもの。DOMとA4957/26・A4958/26は除く）:
- 中国本土の29件（すべてFAA NOTAM Searchから手動で取り込んだArchive分）のうち28件が、E項の
  書き出しが `A TEMPORARY DANGER AREA ESTABLISHED BOUNDED BY: ...`（Qコードは全件`QRDCA`）。
  例外は A0799/26（`QARLC`、エリア内の航空路区間の閉鎖）だけ。
- B3231/26（RPHI、2026-07）は `SPECIAL OPS (AEROSPACE FLT ACT) WILL BE CONDUCTED BY CHINA.
  EST FALL AREA OF UNBURNED DEBRIS ...`。同じ期間の A2575/26（ZSHA ZYSH、TEMPORARY DANGER AREA）と
  同じ打ち上げに関するものと思われる。

注意:
- サンプルは手動で選んだものに偏っている。自動収集分で中国の多角形は A4957/26・A4958/26
  （臨時ウェイポイントの誤検出で除外済み）しか無く、自動収集データでの裏付けはまだ無い。
- 2026-09-27: ユーザー指示で、収集側の`KEYWORD_LIST`に`SPECIAL`を**追加**した（置き換えではない。
  DANGER/TEMPORARY/CLSD/FORBIDDEN/PROHIBITED/DNG/CLOSED/SPECIAL）。`keyword_hit`/`matched_keywords`は
  収集データに出力されるが、ビューアではまだ使っていない。Activeは収集のたびに再計算されるので自動で
  反映される。Archiveは再計算されないため、該当する B3231/26（2026-07）だけ手で更新した。

---

## 17. 日次レポート（2026-09-28追加）

- `notam_daily_report.py`: `notam_out/` の収集データ（state_active.json と archive）を集計し、
  `notam_out/reports/notam_report_YYYY-MM-DD.md`（日付はUTC）と `latest.md` に書き出す。
- `.github/workflows/notam-report.yml`: 毎日 23:52 UTC（08:52 JST）に実行（GitHub Actions の schedule。
  best-effort なので遅れることがある）。手動実行も可。`notam_out/reports/` だけをコミットする。
- 種別は Qコードの主題（2〜3文字目、ICAO Doc 8126 の定義を和訳した表 `Q_SUBJECT`）。軍事かどうかの判定はしない。
- 地図に表示されなかったもの（ビューアの表示条件で落ちたもの）も、理由付きで §7 に全件載せる
  （Q項の円・点のみ／位置情報なし／APIの点のみ／DOM／取消通知）。
- 既知: E項が「NNNNM RADIUS OF 座標」の円で定義されている NOTAM（例: A4697/26）は、多角形として読めないので
  地図に出ない（レポートでは「APIが返した点のみ」等に分類される）。

---

## 18. E項の座標の読み取りの追加対応（2026-09-28、主に韓国RKRR）

`parse_text_polygons()` に次を追加（回帰テスト3件、計44件）。既存データは `notam_backfill_text_polygon.py --apply` で描き直し済み。
- 座標の数字の途中の改行をつなぐ（`-36082⏎0N1293040E`、`N128⏎5338E`）。以前は頂点が抜けたり、1エリアが2つに割れたりしていた。
- 本文の円: `A CIRCLE RADIUS 7NM CENTERED ON <座標>`、`200NM RADIUS OF <座標>`、`0.5NM RAD OF <座標>`、
  `22NM RADIUS CENTERED ON <座標>`（単位 NM/KM/M、半径500NMまで）→ 円のリング（`geometry_source` は `text-polygon`）。
- 帯状エリア: `1NM EITHER SIDE OF (CENTER) LINE <座標>-<座標>-...` → 線の両側に幅を取った帯のリング（端は平ら）。
  以前は線の頂点を結んで閉じた、誤った多角形になっていた。
- 秒に小数が付く座標（`364322.287N 1273032.318E`）。


---

## 19. 航空路の区間閉鎖を線で描く（2026-09-28、中国AIPデータセット）

中国の航空路閉鎖NOTAM（`SEGMENT SADAN - MAGIV OF ATS RTE W187 CLSD` など）は座標を書かず、
地点名（ウェイポイント・VOR）だけで区間を示す。地点の座標と航空路のつながりを、中国民航局 航行情報服務センターが
eAIP（eaipchina.cn、要ログイン）で配布している **AIPデータセット（AIP-DS、AIXM 5.1.1）** から取る。

- **データセットそのものはリポジトリに置かない**（公開リポジトリのため）。データセットの利用条件（useLimitation）は
  「For evaluation and testing use only; not for operational purposes.」で、再配布については書かれていない。
  中国の地点座標は測絵成果として扱われうるため、平文の再配布は避ける。
- 必要な部分だけ（地点 `points`・航空路 `routes`・区間 `segments`・`meta`）を `aip_build_index.py` で小さな索引にし、
  **暗号化して** `aip/cn_aip_index.json.gz.enc` としてコミットする（openssl AES-256-CBC + PBKDF2）。
  鍵は GitHub の Secret **`AIPDS_KEY`**。
- `notam-collect.yml` が収集前に `$RUNNER_TEMP` に復号し、`AIPDS_INDEX` で `notam_cn_collect.py` に渡す。
  復号した索引はコミット対象（`notam_out/`）に書かない。Secret が無い・復号に失敗したときは、線を作らずに
  従来どおり（Q項の点など）で動く。
- `notam_route_segments.py`: E項を読み、区間を航空路の区間（RouteSegment）に沿ってたどって線にする
  （`geometry_source: "route-segment"`、LineString / MultiLineString。優先順位は E項の多角形の次）。
  - 対応する書き方: `SEGMENT A-B OF ATS RTE R`、`ATS ROUTE R SEGMENT A-B`、見出し `FLW SEGMENT OF ATS RTE CLSD:` の後の
    `1. R : A - B` / `R: A - B` の項目、`NAME VOR 'XXX'`（引用符内の識別符号を使う。`&apos;` も可）、
    `20KM WEST OF XXX`（XXX から**航空路に沿って**その方角へ進んだ地点）、座標の端点（航空路上に投影、30km以内）。
  - `ADJUST` / `REROUT` / `ISSUE FPL` / `FLIGHT PLANS` / `FLIGHTS ALONG|VIA|FM` / `SCHEDULED FLIGHTS` / `ALL AFFECTED`
    以降は迂回の指示なので読まない（迂回路を閉鎖区間と取り違えない）。迂回だけのNOTAM（A4968/26）は線にならない。
- ビューア: `route-segment` の線も表示対象（面の枠線より少し太い2.2、区間ごとにNOTAM番号のラベル、Shapefileは PolyLine）。
  日次レポートでは、区間閉鎖が読めるのに線にできなかったものを「航空路の区間閉鎖だが線にできなかった」と分類する。
- 既存レコードは `AIPDS_INDEX=... python notam_backfill_text_polygon.py --apply` で線に描き直し済み（2026-09-28、22件）。

### AIRAC更新（28日ごと）の手順

1. eAIP にログインし、新しい AIP-DS の ZIP（`CN_AIP-DS_EFF<発効日時>_AIRAC<番号>_V*.zip`）を手元に落とす。
2. `AIPDS_KEY=<Secretと同じ値> python aip_build_index.py <ZIP>` → `aip/cn_aip_index.json.gz.enc` を上書き。
   確認用に `--plain out.json` で平文も書けるが、**平文・ZIPはコミットしない**（`.gitignore` 済み）。
3. 暗号化した索引だけをコミット。鍵を変える場合は Secret も同時に更新する。
- 現在の索引: AIRAC 2611（発効 2026-10-28 16:00 UTC）。地点 1,843、航空路 624、区間 2,624。
  発効前のデータを先に使っているので、10/28 までの間に廃止・新設された区間があるとずれる可能性がある。

---

## 20. 収集対象から RJJJ・RKRR・ZKKP を外した（2026-09-28）

中国と関係の薄いNOTAMが大半（収集済みの約7割が RJJJ・RKRR）だったため、ユーザー判断で周辺国の参考FIRを
`RPHI`（マニラ）・`KZAK`（オークランド洋上）だけにした（`AREA_BY_EXACT`、§15の追加分を縮小）。
- 収集済みの分は削除した: state_active 348件（RJJJ 262・RKRR 86）、Archive 136件（2026-09、RJJJ 115・RKRR 21）。ZKKP は0件。
- `notam_cn_collect.py` は毎回、収集対象外になったレコードを state から消す（`drop_untargeted()`）。今後対象を狭めたときも同じ。
  Archive は自動では消さないので、そのときは手で消す。
- §18 の座標読み取りの改善（RKRRで見つけた書き方）はそのまま残す。他のFIRにも効くため。
- 2026-09-28 修正: 開始前に取消・置換されたNOTAM（取消の発行が元NOTAMの開始より前）が、開始時刻まで「予定」のまま
  残っていた（KZAK A4680/26・A4715/26・A4732/26、RPHI B5104/26）。`status_of()` で開始を待たずに「取消」にし、次の収集でArchiveへ移す。
- 2026-09-28 追加: ビューアのカードとポップアップに「撤回済み ・ A4723/26により撤回」を出す（航行警報側の revoked_by 表示と同じ）。
  取消（C）は「撤回」、置換（R）は「置換」。収集側は `ended_by_type` を新たに保存する（既存のArchive 52件にも補完済み）。
- 2026-09-29 追加: NOTAMモードでもヘッダー中央のティッカー（現在有効一覧）を表示。Activeタブと同じ対象（地図に出せる図形あり・DOM以外・重複は1件）を
  「FIR 番号」で、開始が新しい順に流す（`updateNotamHeaderTicker()`）。0件のときは「現在有効なNOTAMはありません」を流す（航行警報側も同じ）。航行警報側とは別の枠（`.header-ticker-pane[data-ticker]`）で、表示中のモードの分だけ出る。
- 2026-09-29 追加: 地図ツールバーの座標プロット欄（`DDMMSS[NS]DDDMMSS[EW]` → ズーム＆ピン、消すとピンも消える）。全モード共通。

---

## 21. ビューアの key 入力画面（2026-09-29）

`geoplot-mil.html` を開くと、最初にブラウザ標準の入力ダイアログ（`window.prompt`、「パスワードを入力してください」）で key を聞く。正しい key が入るまで、ビューア本体の起動（モード選択・データの自動接続）を行わない（`startApp()`）。
- key そのものはページに置かない。PBKDF2-SHA256（salt `geoplot-viewer-gate-v1`、300,000回）のハッシュ `KEY_GATE_HASH` だけを持って比べる。
- 違う key なら「key が違います。…」と聞き直す。キャンセルすると無地の画面のまま（聞き直すには再読み込み）。毎回（再読み込みのたびに）聞く。
- WebCrypto を使うので、https（GitHub Pages）か http://127.0.0.1 で開く必要がある（file:// では開けない）。
- **注意: まだ目隠しに過ぎない。** データ（`msa_out/`・`notam_out/`）は暗号化していないので、URLを直接開けば読める。
  データの暗号化（方式A。key は Secret `VIEWER_KEY`）は未実装。
- key を変えるとき: Secret `VIEWER_KEY` を更新し、Actions の **Viewer key sync**（`.github/workflows/viewer-key-sync.yml`）を手動実行する。
  Secret から `KEY_GATE_HASH` を作り直してコミットし、Pages に反映される。key そのものはログにも出ない。
  （手で置き換える場合は `python3 -c "import hashlib;print(hashlib.pbkdf2_hmac('sha256',b'<新しいkey>',b'geoplot-viewer-gate-v1',300000,32).hex())"`）
- 2026-09-29 修正: 日付変更線を越えて東へドラッグすると、地図が360°ぶん飛び（Leaflet の worldCopyJump）、米国西岸沖などの図形
  （西経を+360°して東経236°等に描いたもの）が画面から消えていた。worldCopyJump をやめ、東西に動かせる範囲を
  西経60°〜東経330°（東経135°中心の1周、`MAP_LON_MIN`/`MAP_LON_MAX`）に限った。カーソル位置の表示は従来どおり西経で出る。
- 2026-09-29 追加: ヘッダーの🔔（新着の通知）に NOTAM も出す。notam_cn.geojson を読み込む（10分おきの自動再読込を含む）たびに、
  ビューアに出るもの（地図に描ける図形あり・DOM以外）で Active/Upcoming のうち、まだ見ていないものを通知する（`registerNotamsForNotifications()`）。
  同じNOTAMは発行元+番号+開始時刻で1件。初めて読み込んだときはその時点の分を既読にする（localStorage `geoplotMil_seenNotamKeys`）。
  通知をクリックすると NOTAM モードに切り替え、その図形にズームして左の一覧でカードを示す。
- 2026-09-29 削除: ヘッダー中央のティッカー（現在有効一覧）は、航行警報・NOTAMとも不要になったので削除した。ヘッダー中央は空き領域（`.header-spacer`）。
- 2026-09-29 追加（§18の続き）: 円の中心が座標ではなく地点名で書かれたものを読む（`text_rings()` → `notam_route_segments.named_center_circles()`）。
  例: `CIRCLE CENTERED AT SHIQUANHE VOR 'SQH' WITH RADIUS OF 30KM`（ZWUQ A5003/26）、`SEGMENT WITHIN A CIRCLE CENTERED AT DUMIN WITH RADIUS OF 100KM CLSD`（ZLHW A4979〜4981/26、ZXXX A4978/26）。
  中心の座標はAIP索引から引き（同名なら VOR 等の航法施設を優先）、`geometry_source` は `text-polygon`。以前は Q項の円のみ、または図形なしだった。
- 2026-09-29 日次チェックで修正: (1) 区間が括弧で囲まれた航空路閉鎖 `SEGMENT (50KM WEST OF SADAN-100KM EAST OF PAMLI) OF ATS RTE W186`（ZWUQ A4996/26）、
  (2) 記号が先で秒に小数が付く座標 `N223141.6E1135759.5`（ZGZU G3522/26・G4364/26）、(3) `404NM RADIUS CENTERED AT <座標>`（KZAK A4740/26。以前は CENTERED ON のみ）。
- 2026-09-30 追加（v2.2.2）: レイヤー一覧の航空路の下に資料の時点（`資料: 中国AIP AIRAC 2611（2026-10-29発効）`）を出す。暗号化ファイルの外側（平文）に airac・effective を持たせ、パネルを開いたときに読む。
- 2026-09-29 変更（v2.2.1）: レイヤーボタンは地図右上の、重なった層のアイコンだけのボタン。一覧は「航空路（中国）」のチェックだけ（本数・説明は出さず、読み込めなかったときだけ理由を出す）。
- 2026-09-29 追加（v2.1.0）: 航空路閉鎖の線に、幅20km（中心線の左右10km）の帯を重ねて描く（`routeBandRing()`、地図・PDF出力とも）。
  根拠は中华人民共和国飞行基本规则 第十五条「航路的宽度为20公里，其中心线两侧各10公里；…可以减少宽度，但不得小于8公里」。区間ごとの実際の幅は AIP が優先なので目安。

---

## 22. ビューアのレイヤー（航空路）（2026-09-29、v2.2.0）

NOTAMモードの地図ツールバーに「レイヤー」ボタンを置き、地図に重ねる参考レイヤーを選べるようにした。1つ目は **航空路（中国）**。
- データは AIP 索引から作った全航空路（624本・2,624区間）の GeoJSON（`aip_build_routes_layer.py`）。
  データセットの中身を公開しない方針なので、**暗号化して** `aip/cn_routes_layer.enc` に置く（ユーザー判断で方式 C'）。
  - 暗号: gzip → AES-256-GCM、鍵は PBKDF2-SHA256(Secret `VIEWER_KEY`、salt、300,000回)。ブラウザの WebCrypto と DecompressionStream で復号する。
  - ビューアは起動時に入れた key をメモリにだけ持ち、チェックを入れたときに復号して表示する（NOTAMモードのときだけ）。
  - **key が短いと総当たりで破られる**ので、この用途には長い key（16文字以上）が望ましい。
- 作り直し: Actions の **Viewer key sync** が、照合用ハッシュと一緒にこのファイルも作り直す（`AIPDS_KEY` で索引を復号して使う）。
  key を変えたとき・AIRAC 更新で索引を差し替えたときに実行する。古い key で作ったままだと、ビューアに「復号できません」と出る。
- 表示: 灰色の1px線。NOTAMの図形の下に敷く（同じ canvas に先に描き、NOTAMの図形を描き直して上に重ねる。別の pane にすると上の canvas がマウスを取ってしまう）。
  線にマウスを乗せると航空路名。細い線でも反応するよう、地図の canvas renderer に `tolerance: 5` を付けた。
- 2026-09-30 追加（v2.3.0〜v2.4.0）: レイヤーを増やし、処理を `OVERLAYS` の一覧で共通化した。
  - **NOTAMモード**: 航行警報（All）／航空路（中国）／ウェイポイント（中国）。ウェイポイントは `aip/cn_points_layer.enc`
    （1,843点。ウェイポイントは小さな点、VOR/DME・NDB は白抜きの点、マウスを乗せると名前）。資料の時点は一覧の下に1行。
  - **NAVWARモード**: NOTAM（All）。レイヤーボタンは NAVWAR・NOTAM の両モードで地図右上に出る（手動モードでは出さない）。
  - 「（All）」は他モードの全件（Active/予告/Archive）を、読み込み済みのデータからその場で作る（暗号化データではない）。
    他モードの図形は下に敷き、そのモード自身の図形を上に描き直す。データが更新されたら作り直す（`refreshDynamicOverlay()`）。
  - Viewer key sync は `cn_routes_layer.enc` と `cn_points_layer.enc` の両方を作り直す。
- 2026-09-30 変更（v2.5.0）: 他モードのレイヤーは「NOTAM」「航行警報」という名前にし、それぞれに Active／Upcoming／Archive を選ぶチェックをぶら下げた
  （既定は Active と Upcoming。`overlaySubSelection()`）。航行警報はエリアごとの状態（`getGroupStatus()`、期間不明は Active 扱い）で選ぶ。
- 2026-09-30 変更（v2.5.1）: ビューアの航行警報（military.geojson）の自動再読込を30分→10分に（収集の cron-job.org 10分おきに合わせ、NOTAMと同じ）。
- 2026-09-30 変更（v2.5.2）: 航空路閉鎖の線の20kmの帯（v2.1.0）はやめ、線だけに戻した（地図・PDF出力とも。ユーザー判断）。
- 2026-09-30 修正: 中心の座標が先で半径が後の円 `A CIRCLE CENTERED AT <座標> WITH RADIUS OF 45KM`（ZSHA A5020〜A5022/26、厦門付近の飛行検査区域）を読む（`_CENTER_BEFORE`/`_RADIUS_AFTER`）。

## 23. ビューアのレイヤー（FIR）（2026-09-30、v2.6.0）

- NOTAMモードのレイヤーに「FIR（ICAO）」を追加。境界は細い灰色の実線、ラベルも灰色（v2.6.1で青の破線から変更、ユーザー指示）、ラベルは ICAOCODE（ズーム3以上で表示、`syncFirLabels()`）、線にマウスを乗せると `ZBPE  FIR BEIJING` のように FIR名。
- 元データ: ArcGIS Online のアイテム「ICAO Flight Information Region」（`4b70cff99cf14565b6671a314c8ea6e8`、
  FeatureServer `services5.arcgis.com/62o2qANhRqripAuB/.../ICAO_Flight_Information_Region/FeatureServer/12`、344件、ICAO 2020-12-18版）。
  アイテムの説明は「公式・航法用ではない、デモ用」、ライセンス欄は空。参考表示として使う。
- 公開データなので暗号化しない（`layers/fir.geojson`、OVERLAYS の `plain: true` は暗号化なしで fetch する）。
- 作り方: `fir_build_layer.py <ArcGISから取得したGeoJSON>`（取得URLは docstring）。この環境からは arcgis.com に直接つながらず、TinyFish の fetch で取得した。
  - 西経30度より西にある FIR は +360度（地図の範囲 東経-60〜330度、NOTAMと同じ）。西経30度をまたぐ大西洋の FIR はそのまま。
  - 日付変更線で東西2件に分かれている FIR（KZAK・NZZO・PAZN・UHMM・NFFF など）は、経度±180の辺を線にしない。ラベルはそれぞれに出る（KZAK はグアム側・ハワイ側の2つ）。
  - centlong/centlat が入っていない（0,0）もの（OBBB・OEJD・SBAO など6件）は、いちばん大きいリングの重心にラベルを置く。
  - 線は 0.02度で間引き（約3.4万点、590KB）。

## 24. AIPの図を参照する区域を線で描く（2026-09-30、v2.7.0）

- 例: ZWUQ W1338/26 `REF AIP CHINA ZWWW-3P-5 AND ZWWW-3P-6 ,THE RESTRICTION AREA(WEST AND NORTH OF G-H-J-K)ACTIVE`。本文に座標が無い。
- `aip_chart_areas.py` に図から読み取った点を持ち（`CHART_POINTS`、図ごと・点の名前→緯度経度）、NOTAMが挙げた点の順に結んだ LineString にする（geometry_source `aip-chart-line`）。
  区域の外側の境界は図にも無い（「G-H-J-K の西と北」）ので、面ではなく境界線だけ。ビューア・日次レポート・backfill は route-segment と同じく線として扱う。
- 登録済み: ZWWW-3P-5（SID RNAV RWY26L/R(NIXER)、EFF2507091600）の G N43°52.5′ E087°13.1′／H N43°52.2′ E087°18.9′／J N43°53.5′ E087°20.8′／K N44°00.3′ E087°21.5′。
  ZWWW-3P-6（SID RNAV RWY26L/R(VARMI)、同版、ファイル `389e61709e81e6dac061254734c93e46.pdf`）も同じ注記・同じ4点（照合済み）なので同じ点で登録。
- 図を足すとき: eAIP の `Data/EAIP.../Terminal/` の PDF はファイル名が内容の MD5（例 3P-5 = `d59fcb6db7d904cd2d0ff4d21c2e3b5e.pdf`）。PDF のタイトルに `AIP-ZWWW-3P-5_...` と入っている。
- 2026-09-30 適用: archive の W1338/26 を qline-circle → aip-chart-line に描き直した。
- 2026-09-30 追加: eAIP の Terminal フォルダ全体（PDF 3194件）を `aip_chart_extract.py` で走査した（ユーザーのPCで実行、結果の JSON だけ受領）。
  「点の名前:座標」の書き方があったのは3件だけ: ZWWW-3P-5・3P-6（登録済み）と、図名の無い空港本文PDF `363e96ce5e136fcb4cdbe74b5b086b39.pdf`
  （B N31°39.8′ E117°59.8′ 〜 E N31°32.0′ E119°02.0′ の東西の制限線、「6.1 All aircraft flying across south of restriction line ... forbidden」。ユーザーから PDF を受領し ZSNJ AD 2.20「6. Warning 6.1」（AIRAC AMDT 09/26、EFF2609021600）と確定、
  `NAMED_LINES` に登録。本文に ZSNJ/NANJING/LUKOU のどれかと RESTRICTION LINE/CONTROL LINE/B-C-D-E のどれかがあれば線にする）。
- 日次レポート §7 の理由に「AIPの図（名前）を参照しているが点が未登録」を追加（`aip_chart_areas.chart_refs_missing()`）。図を送ってもらえば登録できる。
- 2026-10-01 日次チェックで修正: 秒がちょうど60の誤記 `80NM RADIUS OF 342460N1293000W`（KZAK A4776/26 ALTRV KELLY THREE、34°24′60″=34°25′）を繰り上げて読む（`_dms`、61秒以上は従来どおり読まない）。api-point → text-polygon（80NMの円）。
- 2026-10-01 追加: `notam_backfill_text_polygon.py --apply --rebuild-geojson` で、変更が無くても `notam_cn.geojson` を state_active.json から作り直す（main との衝突で notam_cn.geojson だけ main 側を採ったとき、修正が地図用データから落ちるのを防ぐ）。
- 2026-10-02 変更（v2.7.1）: 開始前に取消・置換されたNOTAM（終了が開始以前）は「撤回済み・A3636/26により、開始前に撤回」と表示（有効期間は B項 〜 打ち切り時刻のまま。ユーザー指定の表現）。
- 2026-10-04 修正: `chart_refs_missing()` が手順の図を挙げただけのNOTAM（ZGZU G4414/G4415 の SID/STAR U/S）を「点が未登録の図」と誤って拾っていたので、点の並び（OF A-B-…）がある本文だけに限った。
