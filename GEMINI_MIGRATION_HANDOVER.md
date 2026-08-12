# Gemini API 呼び出し統一化 移行方針（Claude Code セッション引継ぎ用）

このドキュメントは、会社PCのスクリプトにGemini API呼び出しの共通クライアント（自宅PCプロキシへの
自動フォールバック機構）を導入する一連の作業の「決定版リファレンス」。次にどのスクリプトを移行する場合も、
まずこのドキュメントを読めば、背景・全体構成・手順・ハマりどころがすべて分かる状態を目指す。

**実績（2026-08-12時点）**: `rtocs_organizer`・`analog_ic_se_strategy_organizer`の2ツールで移行済み。
`analog_ic_se_strategy_organizer`はステージ0（会社PC直接失敗→自宅PCプロキシ経由フォールバック→
実際のGemini API呼び出し成功）を実機検証済み。この構成は実際に機能することが確認されている。

---

## 1. 背景・目的

会社PC上で Gemini API への直接アクセスが遮断される事象が発生した（発生日: 2026-08-10〜11頃、原因未確定）。
IT部門からの復旧見込みは不明。業務停止を避けるため、**自宅PC経由のプロキシ機構**を構築し、
すべてのGemini API利用スクリプトを、直接呼び出し失敗時に自動でプロキシへフォールバックする方式に統一する。

---

## 2. 全体構成（3リポジトリ構成）

この移行に関わるコードは、役割ごとに3つの独立したGitHubリポジトリに分かれている。
**「会社PCで動くもの」と「自宅PCで動くもの」を混同しないこと**が全体を理解する上で最重要。

| リポジトリ | 動く場所 | 役割 |
|---|---|---|
| `my-claude-code` | **会社PC** | 各ツール本体（`analog_ic_se_strategy_organizer`・`rtocs_organizer`等）。`common/`という名前でサブモジュールとして`gemini-common-tools`を含む |
| `gemini-common-tools` | **会社PC** | 複数の会社PCツールから共通で使うGemini呼び出しクライアント（`gemini_client.py`）。プロキシ機構の「送り手」側 |
| `home-pc-tools` | **自宅PC** | Gemini APIへの中継プロキシサーバー（`home_pc_server_v2.py`）と起動用バッチファイル。プロキシ機構の「受け手」側 |

`gemini_client.py`（会社PC側）と`home_pc_server_v2.py`（自宅PC側）はコード上の依存関係が無い
（`home_pc_server_v2.py`は`gemini_client.py`をimportしていない、単なるHTTP経由の別プログラム）。
両者は「同じプロキシ機構を構成する部品」だが、動く場所もリポジトリも別なので、片方を触るときに
もう片方まで一緒に更新する必要はない。

### データの流れ

```
[会社PC: 各スクリプト（analog_ic_se_strategy_organizer, rtocs_organizer 等）]
     ↓ import
[gemini_client.py]  ← gemini-common-toolsリポジトリ。会社PC側で動く共通クライアント
     ↓
  1. まず直接 Gemini API を試す（タイムアウト15秒）
     ↓ 失敗したら
  2. 自宅PCへのプロキシ経由に自動フォールバック
     [ngrok URL] → [自宅PC: home_pc_server_v2.py（home-pc-toolsリポジトリ）] → Gemini API
     ↓
  3. 失敗記録をファイル(.gemini_direct_disabled_until)に保存
     → 以降30分間（デフォルト）は直接呼び出しをスキップし、プロキシ固定
     → 30分経過後は自動的にまた直接呼び出しから再試行（IT復旧の自動検知）
```

### 自宅PC側のセットアップ

自宅PC側（`home_pc_server_v2.py`・ngrokの起動手順、`start_gemini_proxy.bat`の使い方）は
[`home-pc-tools`リポジトリのREADME](https://github.com/ochi1216/home-pc-tools)に集約した。
このドキュメントでは重複させないので、自宅PC側の作業が必要な場合はそちらを参照すること。

---

## 3. 環境変数

### 会社PC側（各ツールを実行するPC）

```
GEMINI_API_KEY                     = 既存のGemini APIキー（直接呼び出し用）
GEMINI_PROXY_URL                   = 自宅PCのngrok公開URL（例: https://xxxx.ngrok-free.dev）※ngrok再起動の都度変わるため要更新
GEMINI_MODEL                       = 任意。省略時は gemini-2.5-flash
GEMINI_RETRY_DIRECT_AFTER_SECONDS  = 任意。省略時は1800（30分）。直接呼び出し再試行までの待機秒数
GEMINI_COMMON_DIR                  = gemini_client.pyの配置フォルダを明示指定（下記4節参照）
```

### 自宅PC側（プロキシサーバーを動かすPC）

```
GEMINI_API_KEY = 会社PCと同じGemini APIキー
```

**会社PC側と自宅PC側は別々のPC・別々の環境変数空間**なので、**両方のPCで個別に`GEMINI_API_KEY`を
設定する必要がある**。「会社PCで設定したから自宅PCも設定済みのはず」という思い込みで見落としがちな点。
`home_pc_server_v2.py`起動時のログに `Gemini APIキー設定状況: 設定済み` と出るかで確認できる。

---

## 4. `GEMINI_COMMON_DIR`（会社PC側、gemini_client.pyの配置場所指定）

`rtocs_organizer`/`analog_ic_se_strategy_organizer`のようにgitリポジトリのsubmoduleとして
`common/`を使う場合は、各スクリプトの1つ上の階層に`common`フォルダがある前提で自動的に見つかる
（`GEMINI_COMMON_DIR`は省略可）。しかし**対象スクリプトをgitリポジトリと異なるローカルフォルダ構成で
管理している場合**（越智さんの実際の環境がこれに該当：`rtocs_organizer`は`bbt\RTOCS_organizer`、
`analog_ic_se_strategy_organizer`は`SE_Strategy\analog_ic_se_strategy_organizer`と、
管理フォルダがツールごとにバラバラ）は、相対パスでは`gemini_client.py`を見つけられず
`ModuleNotFoundError`になる。この場合は`gemini_client.py`をどこか1箇所（例:
`C:\Users\nx023836\Documents\PythonScripts\common\gemini_client.py`）に配置し、
全スクリプトから`GEMINI_COMMON_DIR`でその場所を指すよう設定すること。
**新しいスクリプトを移行するときも、まずこのパターンに該当するか確認する**（該当する可能性が高い）。

---

## 5. `gemini_client.py` の公開インターフェース

対象スクリプトの修正では、以下の関数だけを使う。内部実装（直接/プロキシ切替ロジック）には触れない。

```python
from gemini_client import summarize_text, generate_content, generate_advanced

# パターン1: テキスト要約（指示文を分離できる場合）
result = summarize_text(text=本文, instruction="3行で要約して")  # instruction省略可

# パターン2: 汎用（既存コードが1つの完成したプロンプト文字列を作っている場合）
result = generate_content(prompt=完成済みプロンプト文字列)

# パターン3: 高度な機能を使う場合（Google Search Grounding / JSONモード等）
# Gemini API の generateContent リクエストペイロード全体をそのまま渡す。
# レスポンスもGemini APIの生JSON（dict）がそのまま返る。
# モデルを明示的に切り替えたい場合（例: 通常モード/ディープモードの切替）は model引数を指定する。
# 省略時は環境変数 GEMINI_MODEL（デフォルト gemini-2.5-flash）が使われる。
payload = {
    "contents": [{"parts": [{"text": prompt}]}],
    "tools": [{"google_search": {}}],  # grounding使用時
    "generationConfig": {"responseMimeType": "application/json", "responseSchema": {...}},  # JSONモード使用時
}
result = generate_advanced(payload, model="gemini-2.5-pro")  # ディープモード時は明示的に指定すること
text = result['candidates'][0]['content']['parts'][0]['text']
grounding = result['candidates'][0].get('groundingMetadata')  # grounding使用時
```

戻り値：`summarize_text` / `generate_content` は `str`。`generate_advanced` は Gemini API の生レスポンス（`dict`）。

**どの関数を使うか**：
- 単純なテキスト要約・単発プロンプトのみ → `summarize_text` / `generate_content`
- Google Search Grounding、JSONモード（`responseSchema`指定）など高度な機能を使っている
  （例: `rtocs_organizer`, `analog_ic_se_strategy_organizer`） → `generate_advanced`
  既存のペイロード組み立てロジック・レスポンス解析ロジックは変更せず、Gemini API呼び出し部分だけを
  `generate_advanced(payload, model=...)`に差し替えれば良い

**重要：モデル指定について**
「通常モード（flash）」「ディープモード（pro）」をユーザーが選択できるツールでは、
`generate_advanced(payload, model=...)` の `model` 引数を**必ず明示的に指定**すること。
省略するとデフォルトモデル（`gemini-2.5-flash`）が使われ、画面上でディープモードを選んでいても
実際にはflashが呼ばれるサイレントバグになる（コスト表示との不整合も発生する）。
モデル指定は内部的に、直接呼び出し時はURLに反映され、プロキシ経由時はペイロードに`_gemini_model`という
専用フィールドとして付与されて自宅PC側に伝わる（Gemini API本体には存在しないフィールドなので転送前に除去される）。

**仕組み**：自宅PC側のプロキシ（`/generate`エンドポイント）は、受け取ったペイロードをそのままGemini APIに
透過転送するだけの単純な中継なので、grounding・JSONモードを含めどんなリクエストでも直接呼び出しと
同じ結果が得られる。フォールバック時に機能が失われることはない（2026-08-12に実機で確認済み）。

---

## 6. 移行手順（対象スクリプト1本ごと）

1. スクリプト内で Gemini API を呼んでいる箇所を特定する
   - `google.generativeai` (`genai.GenerativeModel(...).generate_content(...)`) を使っているケース
   - `google.genai`（統合SDK）を使っているケース
   - `requests` で `generativelanguage.googleapis.com` に直接POSTしているケース
   - 複数パターンが混在している場合もあるので、ファイル全体を確認すること
2. スクリプトの先頭付近に、`GEMINI_COMMON_DIR`対応のsys.path追加コードを入れる（既存2ツールの
   `ic_engine.py`・`strategy_engine.py`冒頭を参照。以下がテンプレート）
   ```python
   import os, sys
   _COMMON_DIR = os.environ.get("GEMINI_COMMON_DIR") or os.path.join(
       os.path.dirname(os.path.abspath(__file__)), "..", "common")
   if _COMMON_DIR not in sys.path:
       sys.path.insert(0, _COMMON_DIR)
   from gemini_client import generate_advanced  # または summarize_text, generate_content
   ```
3. 既存の呼び出しコードを `summarize_text(...)` / `generate_content(...)` / `generate_advanced(...)` に置き換える
   - プロンプトの組み立てロジック（プロンプトテンプレート文言など）は極力変更しない
   - grounding・JSONモードを使っている場合は`generate_advanced`＋`model`引数明示（5節参照）
4. 既存コードにあった Gemini APIキーのハードコーディングや個別の `genai.configure(api_key=...)` 等の
   初期化コードは削除する（`gemini_client.py` 側で環境変数から読むため不要）
5. エラーハンドリング：`gemini_client` 側で直接/プロキシ両方失敗した場合は例外を送出する。
   呼び出し元の既存の `try/except` はそのまま活かしてよい
6. `requirements.txt`から`google-generativeai`/`google-genai`を削除し、`requests`が入っていることを確認する
   （他の用途でSDKを直接使っている別スクリプトが同じフォルダにある場合は、そちらのrequirementsは残す）
7. 修正後、**必ず対象スクリプト自身のエントリポイントを経由して**動作確認する（8節「よくあるハマりどころ」参照）
8. 修正済みスクリプトと未修正スクリプトの一覧を、本ドキュメント9節に追記する

---

## 7. やってはいけないこと

- `gemini_client.py` の内部ロジック（フォールバック判定・タイムアウト値・状態ファイル）を、対象スクリプト側の都合で個別に変更しない。修正が必要な場合は `gemini_client.py` 自体を1箇所直し、全スクリプトに反映する
- APIキーやngrok URLをスクリプト内にハードコーディングしない（必ず環境変数経由）
- 対象スクリプトごとに独自のフォールバック処理を再実装しない（重複ロジックを避けるため）
- `home_pc_server_v2.py`（自宅PC側）を、会社PC側のリポジトリ（`my-claude-code`/`gemini-common-tools`）にコミットしない（`home-pc-tools`リポジトリ専用）

---

## 8. よくあるハマりどころ（実機作業で判明した注意点）

今後の移行作業でも同じ箇所でつまずく可能性が高いので、先に一通り目を通しておくこと。

### 8-1. `setx`は実行した同じウィンドウには反映されない

Windowsで環境変数を設定する`setx`コマンドは、レジストリに永続保存するだけで、**実行した
そのコマンドプロンプト/PowerShellウィンドウ自体には反映されない**。設定後は必ず新しいウィンドウを
開いて確認・実行すること。ダッシュボードやサーバーを起動し直す場合も同様（プロセスは起動時の
環境変数を引き継ぐため、既存プロセスを再起動せずブラウザの再読み込みだけしても反映されない）。

### 8-2. PowerShellと`cmd.exe`で環境変数の確認コマンドが違う

- `cmd.exe`: `echo %GEMINI_API_KEY%`
- PowerShell: `$env:GEMINI_API_KEY`（`echo %GEMINI_API_KEY%`は文字列としてそのまま表示されるだけで確認にならない）

プロンプトが`PS C:\...`ならPowerShell、`C:\...>`ならcmd.exe。どちらのシェルを使っているか確認してから
コマンドを選ぶこと。

### 8-3. 動作確認は対象スクリプトのエントリポイント経由で行う（直接importでは不十分）

```
python -c "from gemini_client import summarize_text; ..."
```
このコマンドは`gemini_client.py`が**カレントディレクトリまたは標準の`sys.path`上に見える場合のみ**成功する。
`GEMINI_COMMON_DIR`によるパス解決は対象スクリプト（`ic_engine.py`等）の**モジュール読み込み時**に
実行されるコードなので、このコマンドではその処理を一切経由せず、`GEMINI_COMMON_DIR`が正しく設定されていても
`ModuleNotFoundError`になる（正常な動作。バグではない）。

正しい確認方法は、対象スクリプト自身をimportすること：
```
python -c "import ic_engine; print('import OK')"
```

### 8-4. 会社PC側の直接呼び出し失敗は「正常」

会社PCから直接Gemini APIを呼んで`SSLError`等で失敗するのは、まさに今回の障害そのものなので想定通りの動作。
これが出たらプロキシへのフォールバックが正しくトリガーされているか（ログに
「直接呼び出し失敗→以降30分間はプロキシに固定」と出るか）を確認する。

### 8-5. `GEMINI_PROXY_URL`が設定されているのに`502 Bad Gateway`が出る

これは「ngrokのトンネル自体は生きているが、その先の`home_pc_server_v2.py`が応答していない」ことを意味する。
`GEMINI_PROXY_URL`未設定時のエラーメッセージ（「GEMINI_PROXY_URLが設定されていません」）とは別の問題なので
混同しないこと。自宅PC側で`home_pc_server_v2.py`が起動中か、クラッシュしていないか確認する。

### 8-6. Flaskの自動リローダーが不要な再起動を繰り返すことがある

`debug=True`のFlaskは、監視対象フォルダ内のファイル変更を検知して自動再起動する。エディタの自動保存や、
無関係なファイルの変更でも反応することがあり、再起動中のタイミングでリクエストが来ると502エラーになる。
`home-pc-tools`の`home_pc_server_v2.py`は`use_reloader=False`にしてこれを防いでいる。

### 8-7. 会社PC・自宅PCそれぞれで`GEMINI_API_KEY`が必要（3節参照）

自宅PC側で「Gemini APIキー設定状況: 未設定」と出ている場合、会社PC側の設定とは別に、
自宅PC側でも`setx GEMINI_API_KEY`が必要（8-1の注意点と合わせて、新しいウィンドウでの確認を忘れずに）。

### 8-8. バッチファイルは日本語コメント＋`chcp`を避ける

Windows版バッチファイル（`.bat`）で日本語コメントを入れ、`chcp 65001`でUTF-8に切り替える方式は、
環境によってcmd.exeが文字化けし、コマンドとして誤解釈されて動かなくなることがあった
（`analog_ic_se_strategy_organizer`の`run_dashboard.bat`で発生）。新しいバッチファイルを作る際は
英語コメントのみのASCII構成にすること（`home-pc-tools`の`start_gemini_proxy.bat`を参考にする）。

---

## 9. 対象スクリプト一覧

このセクションは各セッションで作業対象を追記・更新すること。

| スクリプト名 | パス | 呼び出しパターン | 使用関数 | 移行状況 |
|---|---|---|---|---|
| `rtocs_organizer` | `my-claude-code`リポジトリ内 | Google Search Grounding使用 | `generate_advanced` | 完了（2026-08-11、`strategy_engine.py`。モック検証済み・実機未検証。`rtocs_organizer_20260711_01.py`旧スクレイパーは対象外） |
| `analog_ic_se_strategy_organizer` | `my-claude-code`リポジトリ内 | Google Search Grounding + JSONモード使用 | `generate_advanced` | 完了（2026-08-11、`ic_engine.py`。2026-08-12にステージ0で実機検証済み：会社PC直接失敗→自宅PCプロキシ経由フォールバック→実際のGemini API呼び出し成功。5ステージ全体・JSONモード・ディープモードは実機未検証） |
| `onenote_report_generator` | `my-claude-code`外・別リポジトリ（要確認） | シンプルな要約用途 | `summarize_text` | 未着手（対象リポジトリ未特定） |
| （他、Gemini APIを使う個人開発ツール群） | | | | 未着手 |

---

## 10. 関連の背景メモ

- 発端：会社PCでGemini APIが2026-08-10頃から使用不可に。IT部門からの明示的な通知は現時点で未確認
- 暫定回避策として、自宅PC（Tailscale/ngrok併用環境、DeskIn常用）にFlaskサーバーを立て、ngrok（無料プラン）でトンネル公開し、会社PCから中継アクセスする構成を採用
- 当初は自宅PC側コード（`home_pc_server_v2.py`）をどこにもGit管理せず、チャット経由でファイルをやり取りしていたが、「会社PC用コードと自宅PC用コードが1つのリポジトリに混在しているように見える」という懸念から、2026-08-12に自宅PC専用の`home-pc-tools`リポジトリへ切り出した（2節の3リポジトリ構成を参照）
- 将来的な安定運用のための検討候補：ngrok有料プランでの固定URL化、または安価なVPSへの移行（自宅PC常時起動への依存を減らすため）
