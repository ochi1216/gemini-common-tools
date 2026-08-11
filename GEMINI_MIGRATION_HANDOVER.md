# Gemini API 呼び出し統一化 移行方針（Claude Code セッション引継ぎ用）

## 背景・目的

会社PC上で Gemini API への直接アクセスが遮断される事象が発生した（発生日: 2026-08-11頃、原因未確定・地政学的リスク関連の制限の可能性）。
IT部門からの復旧見込みは不明。業務停止を避けるため、**自宅PC経由のプロキシ機構**を構築し、
すべてのGemini API利用スクリプトを、直接呼び出し失敗時に自動でプロキシへフォールバックする方式に統一する。

このドキュメントをアップロードしたセッションでは、以下の方針に従って対象スクリプトを修正すること。

---

## 全体構成

```
[会社PC: 各スクリプト]
     ↓ import
[gemini_client.py]  ← 共通クライアントモジュール（本ドキュメントで配布）
     ↓
  1. まず直接 Gemini API を試す（タイムアウト15秒）
     ↓ 失敗したら
  2. 自宅PCへのプロキシ経由に自動フォールバック
     [ngrok URL] → [自宅PC: Flask (home_pc_server_v2.py)] → Gemini API
     ↓
  3. 失敗記録をファイル(.gemini_direct_disabled_until)に保存
     → 以降30分間（デフォルト）は直接呼び出しをスキップし、プロキシ固定
     → 30分経過後は自動的にまた直接呼び出しから再試行（IT復旧の自動検知）
```

## 構成要素と配置場所（想定）

| コンポーネント | 役割 | 配置場所（会社PC） |
|---|---|---|
| `gemini_client.py` | 直接/プロキシ自動切替クライアント | `C:\Users\nx023836\Documents\PythonScripts\common\gemini_client.py`（共通フォルダ。実際のパスは要確認） |
| `home_pc_server_v2.py` | 自宅PC側Flaskサーバー（Gemini API呼び出し窓口） | 自宅PC側のみ。会社PC側スクリプトには影響なし |
| ngrok | 自宅PCをインターネットに公開するトンネル | 自宅PC側のみ |

---

## 環境変数（会社PC側で設定が必要）

```
GEMINI_API_KEY                  = 既存のGemini APIキー（直接呼び出し用、既存のものを流用）
GEMINI_PROXY_URL                = 自宅PCのngrok公開URL（例: https://xxxx.ngrok-free.dev）※再起動の都度変わるため要更新
GEMINI_MODEL                    = 任意。省略時は gemini-2.5-flash
GEMINI_RETRY_DIRECT_AFTER_SECONDS = 任意。省略時は1800（30分）。直接呼び出し再試行までの待機秒数
```

**注意**：`GEMINI_PROXY_URL` はngrok無料プランのため、自宅PC側でngrokを再起動すると値が変わる。
最新URLは越智さんに都度確認すること（固定化には有料プラン移行 or VPS移行が今後の検討候補）。

---

## `gemini_client.py` の公開インターフェース

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

**重要：モデル指定について**
`rtocs_organizer` / `analog_ic_se_strategy_organizer` のように「通常モード（flash）」「ディープモード（pro）」を
ユーザーが選択できるツールでは、`generate_advanced(payload, model=...)` の `model` 引数を**必ず明示的に指定**すること。
省略するとデフォルトモデル（`gemini-2.5-flash`）が使われ、画面上でディープモードを選んでいても
実際にはflashが呼ばれるサイレントバグになる（コスト表示との不整合も発生する）。
モデル指定は内部的に、直接呼び出し時はURLに反映され、プロキシ経由時はペイロードに`_gemini_model`という
専用フィールドとして付与されて自宅PC側に伝わる（Gemini API本体には存在しないフィールドなので転送前に除去される）。

**`generate_advanced` を使うべきケース**：Google Search Grounding、JSONモード（responseSchema指定）など、
単純なテキスト要約を超える機能を使っているツール（例: `rtocs_organizer`, `analog_ic_se_strategy_organizer`）はこちらを使う。
既存のペイロード組み立てロジック・レスポンス解析ロジックは変更せず、Gemini API呼び出し部分だけを
`generate_advanced(payload)`に差し替えれば良い。

**仕組み**：自宅PC側のプロキシ（`/generate`エンドポイント）は、受け取ったペイロードをそのままGemini APIに
透過転送するだけの単純な中継なので、grounding・JSONモードを含めどんなリクエストでも直接呼び出しと
同じ結果が得られる。フォールバック時に機能が失われることはない。

---

## 移行手順（対象スクリプト1本ごと）

1. スクリプト内で Gemini API を呼んでいる箇所を特定する
   - `google.generativeai` (`genai.GenerativeModel(...).generate_content(...)`) を使っているケース
   - `requests` で `generativelanguage.googleapis.com` に直接POSTしているケース
   - の2パターンがあり得るので、両方確認すること
2. 呼び出し元の直前に `from gemini_client import summarize_text, generate_content` を追加
3. 既存の呼び出しコードを `summarize_text(...)` または `generate_content(...)` に置き換える
   - プロンプトの組み立てロジック（プロンプトテンプレート文言など）は極力変更しない。文言はそのまま `instruction` または `prompt` に渡す
4. 既存コードにあった Gemini APIキーのハードコーディングや個別の `genai.configure(api_key=...)` 等の初期化コードは削除する（`gemini_client.py` 側で環境変数から読むため不要）
5. エラーハンドリング：`gemini_client` 側で直接/プロキシ両方失敗した場合は例外を送出する。呼び出し元の既存の `try/except` はそのまま活かしてよい
6. 修正後、可能であれば以下で単体動作確認する
   ```
   python -c "from gemini_client import summarize_text; print(summarize_text('テスト用の短い文章です。'))"
   ```
7. 修正済みスクリプトと未修正スクリプトの一覧を、このセッション終了時に `NEXT_TASK.md` 等に記録し、次セッションに引き継ぐ

---

## やってはいけないこと

- `gemini_client.py` の内部ロジック（フォールバック判定・タイムアウト値・状態ファイル）を、対象スクリプト側の都合で個別に変更しない。修正が必要な場合は `gemini_client.py` 自体を1箇所直し、全スクリプトに反映する
- APIキーやngrok URLをスクリプト内にハードコーディングしない（必ず環境変数経由）
- 対象スクリプトごとに独自のフォールバック処理を再実装しない（重複ロジックを避けるため）

---

## 対象スクリプト一覧（要更新）

このセクションは各セッションで作業対象を追記・更新すること。

| スクリプト名 | パス | 呼び出しパターン | 使用関数 | 移行状況 |
|---|---|---|---|---|
| `rtocs_organizer` | `my-claude-code`リポジトリ内 | Google Search Grounding使用 | `generate_advanced` | 完了（2026-08-11、`strategy_engine.py`。モック検証済み・実機未検証。`rtocs_organizer_20260711_01.py`旧スクレイパーは対象外） |
| `analog_ic_se_strategy_organizer` | `my-claude-code`リポジトリ内 | Google Search Grounding + JSONモード使用 | `generate_advanced` | 完了（2026-08-11、`ic_engine.py`。モック検証済み・実機未検証） |
| `onenote_report_generator` | `my-claude-code`外・別リポジトリ（要確認） | シンプルな要約用途 | `summarize_text` | 未着手（対象リポジトリ未特定） |
| （他、Gemini APIを使う個人開発ツール群） | | | | 未着手 |

---

## 関連の背景メモ

- 発端：会社PCでGemini APIが2026-08-10頃から使用不可に。IT部門からの明示的な通知は現時点で未確認
- 暫定回避策として、自宅PC（Tailscale/ngrok併用環境、DeskIn常用）にFlaskサーバーを立て、ngrok（無料プラン）でトンネル公開し、会社PCから中継アクセスする構成を採用
- 将来的な安定運用のための検討候補：ngrok有料プランでの固定URL化、または安価なVPSへの移行（自宅PC常時起動への依存を減らすため）
