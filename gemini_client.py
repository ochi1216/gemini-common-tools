"""
gemini_client.py

会社PCの複数スクリプトから共通で使うGemini API呼び出しモジュール。

【使い方】
既存スクリプトで直接 google-generativeai や requests で Gemini を呼んでいた箇所を、
以下のように置き換えるだけでOKです。

    from gemini_client import summarize_text, generate_content

    result = generate_content("これを要約して: ...")
    # または
    result = summarize_text("本文テキスト", instruction="3行で要約して")

【動作】
1. まず直接 Gemini API (Google) を試す
2. 失敗した場合（IT側の遮断でタイムアウト/接続エラー等）、
   自動的に自宅PC経由（ngrok経由のFlaskサーバー）にフォールバックする
3. IT側でGemini APIが復活すれば、次回呼び出しから自動的に直接呼び出しに戻る
   （スクリプト側の変更は一切不要）

【設定】
環境変数で以下を設定してください（会社PC側）：
- GEMINI_API_KEY       : Gemini APIキー（直接呼び出し用）
- GEMINI_PROXY_URL     : 自宅PCのngrok URL（例: https://xxxx.ngrok-free.dev）
                          ※ngrok URLは再起動のたびに変わるので、都度更新が必要です

環境変数を設定せずコード内で直接指定したい場合は、下の DEFAULT_* を書き換えてください。
"""

import os
import time
import requests

# ===== 設定 =====
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
GEMINI_DIRECT_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'

# 自宅PCのngrok URL。ngrokを再起動したらここ(環境変数)を更新してください。
GEMINI_PROXY_URL = os.environ.get('GEMINI_PROXY_URL', '')  # 例: https://xxxx.ngrok-free.dev

DIRECT_TIMEOUT = 15   # 直接呼び出しのタイムアウト（秒）。遮断されている場合は早めに見切りをつける
PROXY_TIMEOUT = 60    # プロキシ経由（自宅PC→Gemini）のタイムアウト（秒）

# ===== フォールバック状態管理 =====
# 一度直接呼び出しが失敗したら、以降の呼び出しをすべてプロキシ固定にする
# （無駄な失敗リトライを避けるため）。
#
# 同一プロセス内ではメモリ上のフラグで管理しつつ、
# タスクスケジューラ等で毎回新規プロセスとして起動されるスクリプトのために、
# 状態をファイル(_direct_disabled_until)にも保存する。
# ファイルに記録がある場合、一定時間（デフォルト30分）は直接呼び出しをスキップし、
# 時間が経過したら自動的にまた直接呼び出しから試す（IT復活を検知するため）。
_direct_disabled = False
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.gemini_direct_disabled_until')
_RETRY_DIRECT_AFTER_SECONDS = int(os.environ.get('GEMINI_RETRY_DIRECT_AFTER_SECONDS', 1800))  # デフォルト30分


def _is_direct_disabled() -> bool:
    """直接呼び出しが現在無効化されているかどうかを判定（メモリ＋ファイルの両方を見る）"""
    global _direct_disabled
    if _direct_disabled:
        return True
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, 'r') as f:
                disabled_until = float(f.read().strip())
            if time.time() < disabled_until:
                return True
            else:
                # 猶予期間が過ぎたので、また直接呼び出しから試す
                os.remove(_STATE_FILE)
        except (ValueError, OSError):
            pass
    return False


def _disable_direct():
    """直接呼び出しを無効化し、猶予期間をファイルに記録する"""
    global _direct_disabled
    _direct_disabled = True
    try:
        with open(_STATE_FILE, 'w') as f:
            f.write(str(time.time() + _RETRY_DIRECT_AFTER_SECONDS))
    except OSError:
        pass


class GeminiClientError(Exception):
    """直接・プロキシ両方とも失敗した場合に投げる例外"""
    pass


def _call_direct(prompt: str) -> str:
    """Gemini APIを直接呼び出す"""
    if not GEMINI_API_KEY:
        raise RuntimeError('GEMINI_API_KEY が設定されていません')

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(
        f'{GEMINI_DIRECT_URL}?key={GEMINI_API_KEY}',
        json=payload,
        timeout=DIRECT_TIMEOUT
    )
    resp.raise_for_status()
    result = resp.json()
    return result['candidates'][0]['content']['parts'][0]['text']


def _call_proxy(text: str, instruction: str) -> str:
    """自宅PC経由（ngrok）でGemini APIを呼び出す"""
    if not GEMINI_PROXY_URL:
        raise RuntimeError('GEMINI_PROXY_URL が設定されていません（自宅PCのngrok URLを設定してください）')

    resp = requests.post(
        f'{GEMINI_PROXY_URL.rstrip("/")}/summarize',
        json={'text': text, 'instruction': instruction},
        timeout=PROXY_TIMEOUT
    )
    resp.raise_for_status()
    result = resp.json()
    if 'error' in result:
        raise RuntimeError(f'プロキシ側エラー: {result["error"]}')
    return result['summary']


def _call_direct_advanced(payload: dict) -> dict:
    """Gemini APIを直接呼び出す（リクエストペイロード全体を渡し、レスポンスJSON全体を返す）"""
    if not GEMINI_API_KEY:
        raise RuntimeError('GEMINI_API_KEY が設定されていません')

    resp = requests.post(
        f'{GEMINI_DIRECT_URL}?key={GEMINI_API_KEY}',
        json=payload,
        timeout=DIRECT_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def _call_proxy_advanced(payload: dict) -> dict:
    """自宅PC経由（ngrok）でGemini APIを呼び出す（/generate 透過エンドポイント経由）"""
    if not GEMINI_PROXY_URL:
        raise RuntimeError('GEMINI_PROXY_URL が設定されていません（自宅PCのngrok URLを設定してください）')

    resp = requests.post(
        f'{GEMINI_PROXY_URL.rstrip("/")}/generate',
        json=payload,
        timeout=PROXY_TIMEOUT
    )
    resp.raise_for_status()
    result = resp.json()
    if 'error' in result:
        raise RuntimeError(f'プロキシ側エラー: {result["error"]}')
    return result


def generate_advanced(payload: dict, verbose: bool = True) -> dict:
    """
    Gemini API への generateContent リクエストペイロード全体（contents / tools / generationConfig 等）
    をそのまま渡し、レスポンスJSON全体（dict）をそのまま返す汎用関数。

    Google Search Grounding や JSONモード（responseSchema指定）など、
    summarize_text / generate_content では扱えない高度な機能を使うツール
    （例: rtocs_organizer, analog_ic_se_strategy_organizer）向け。

    呼び出し側は、既存のペイロード組み立てロジック・レスポンス解析ロジック
    （groundingMetadataの取得、JSON文字列のパース等）をそのまま流用できる。
    このプロセスがどちらの経路（直接／プロキシ）で呼び出したかを意識する必要はない。

    使用例:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        }
        result = generate_advanced(payload)
        text = result['candidates'][0]['content']['parts'][0]['text']
        grounding = result['candidates'][0].get('groundingMetadata')
    """
    global _direct_disabled

    if not _is_direct_disabled():
        try:
            result = _call_direct_advanced(payload)
            if verbose:
                print('[gemini_client] 直接Gemini APIで成功（advanced）')
            return result
        except Exception as e:
            _disable_direct()
            if verbose:
                print(f'[gemini_client] 直接呼び出し失敗（{e}）→ 以降{_RETRY_DIRECT_AFTER_SECONDS // 60}分間は自宅PC経由（プロキシ）に固定します')
    elif verbose:
        print('[gemini_client] 直接呼び出しは無効化中（前回失敗の猶予期間内）→ プロキシ経由で呼び出します')

    return _call_proxy_advanced(payload)


def generate_content(prompt: str, verbose: bool = True) -> str:
    """
    直接Gemini APIを試し、失敗したら自宅PC経由にフォールバックする。
    プロンプトをそのまま渡す用途（要約に限らず汎用的に使う場合）。

    一度直接呼び出しが失敗すると、以降このプロセスが起動している間は
    直接呼び出しを試さず、常にプロキシ経由になる。
    """
    if not _is_direct_disabled():
        try:
            result = _call_direct(prompt)
            if verbose:
                print('[gemini_client] 直接Gemini APIで成功')
            return result
        except Exception as e:
            _disable_direct()
            if verbose:
                print(f'[gemini_client] 直接呼び出し失敗（{e}）→ 以降{_RETRY_DIRECT_AFTER_SECONDS // 60}分間は自宅PC経由（プロキシ）に固定します')
    elif verbose:
        print('[gemini_client] 直接呼び出しは無効化中（前回失敗の猶予期間内）→ プロキシ経由で呼び出します')

    # プロキシ側は summarize エンドポイント形式なので、
    # プロンプト全体を text として渡し、instruction は空にする
    return _call_proxy(text=prompt, instruction='')


def summarize_text(text: str, instruction: str = '以下の内容を簡潔に日本語で要約してください。', verbose: bool = True) -> str:
    """
    テキストを要約する。直接Gemini APIを試し、失敗したら自宅PC経由にフォールバックする。

    一度直接呼び出しが失敗すると、以降このプロセスが起動している間は
    直接呼び出しを試さず、常にプロキシ経由になる。
    """
    prompt = f"{instruction}\n\n---\n{text}"

    if not _is_direct_disabled():
        try:
            result = _call_direct(prompt)
            if verbose:
                print('[gemini_client] 直接Gemini APIで成功')
            return result
        except Exception as e:
            _disable_direct()
            if verbose:
                print(f'[gemini_client] 直接呼び出し失敗（{e}）→ 以降{_RETRY_DIRECT_AFTER_SECONDS // 60}分間は自宅PC経由（プロキシ）に固定します')
    elif verbose:
        print('[gemini_client] 直接呼び出しは無効化中（前回失敗の猶予期間内）→ プロキシ経由で呼び出します')

    return _call_proxy(text=text, instruction=instruction)


if __name__ == '__main__':
    # 簡単な動作確認
    sample_text = "本日の会議では、新製品の開発スケジュールについて議論しました。第一四半期中にプロトタイプを完成させ、第二四半期に量産体制を整える方針で合意しました。"
    print(summarize_text(sample_text))
