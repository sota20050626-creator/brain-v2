import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path
import time

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_FILE = Path("knowledge/daily/" + TODAY + ".json")
COST_FILE = Path("knowledge/cost_log.json")

# Claude API料金
SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# Qwen3料金（OpenRouter経由）
QWEN_INPUT_PRICE = 0.1 / 1_000_000
QWEN_OUTPUT_PRICE = 0.3 / 1_000_000

# リトライ設定
MAX_RETRIES = 3
RETRY_DELAY = 2


def load_cost_log():
    if not COST_FILE.exists():
        return {"monthly": {}, "total_usd": 0}
    try:
        with open(COST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"  コストログ読み込みエラー: {e}, 新規作成します")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label, model="claude"):
    if model == "qwen":
        cost = input_tokens * QWEN_INPUT_PRICE + output_tokens * QWEN_OUTPUT_PRICE
    else:
        cost = input_tokens * SONNET_INPUT_PRICE + output_tokens * SONNET_OUTPUT_PRICE
    
    log = load_cost_log()
    month = TODAY[:7]
    if month not in log["monthly"]:
        log["monthly"][month] = {"usd": 0, "calls": [], "input_tokens": 0, "output_tokens": 0}
    
    log["monthly"][month]["usd"] = round(log["monthly"][month]["usd"] + cost, 6)
    log["monthly"][month]["input_tokens"] += input_tokens
    log["monthly"][month]["output_tokens"] += output_tokens
    log["monthly"][month]["calls"].append({
        "date": TODAY,
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": round(cost, 6)
    })
    log["total_usd"] = round(sum(v["usd"] for v in log["monthly"].values()), 6)
    
    try:
        with open(COST_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  コストログ保存エラー: {e}")
    
    print("  コスト記録: " + label + " $" + str(round(cost, 6)) + " [" + model + "]")
    return cost


def call_qwen(prompt, max_tokens=2000, label="qwen_call"):
    """Qwen3をOpenRouter経由で呼び出す（軽い処理用・激安）"""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  OPENROUTER_API_KEY未設定、Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    
    payload = json.dumps({
        "model": "qwen/qwen-2.5-72b-instruct",
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sota20050626-creator/brain-v2",
        }
    )
    
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                result = json.loads(r.read())
            
            if "error" in result:
                raise Exception(f"API Error: {result['error']}")
            
            usage = result.get("usage", {})
            save_cost(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                label, model="qwen"
            )
            return result["choices"][0]["message"]["content"]
            
        except Exception as e:
            print(f"  Qwen3エラー (試行{attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                print("  → Claudeにフォールバック")
                return call_claude(prompt, max_tokens, label)


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude API呼び出し（フォールバック用）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ANTHROPIC_API_KEY未設定、エラーです")
        return "APIキーが設定されていません"
    
    payload = json.dumps({
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
    )
    
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                result = json.loads(r.read())
            
            if "error" in result:
                raise Exception(f"Claude Error: {result['error']}")
            
            usage = result.get("usage", {})
            save_cost(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                label, model="claude"
            )
            return result["content"][0]["text"]
            
        except Exception as e:
            print(f"  Claude API エラー (試行{attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                return f"API呼び出しに失敗しました: {e}"


def create_summary_prompt(content, summary_type="general"):
    """要約プロンプトを動的生成（重要度スコアリング付き）"""
    if summary_type == "technical":
        return f"""以下の技術的なコンテンツを分析し、重要度スコア（1-10）付きで要約してください。

【分析観点】
- 新技術・手法の革新性
- 実装の具体性・実用性
- 業界への影響度
- 学習・応用価値

コンテンツ:
{content}

【出力形式】
重要度: [1-10]
カテゴリ: [技術分野]
要約: [200文字以内の簡潔な要約]
キーポイント: [3つの要点を箇条書き]"""
    
    elif summary_type == "news":
        return f"""以下のニュースを分析し、重要度スコア（1-10）付きで要約してください。

【分析観点】
- 社会的影響度
- 緊急性・時事性
- 信頼性・情報源
- 長期的な意義

コンテンツ:
{content}

【出力形式】
重要度: [1-10]
カテゴリ: [ニュース分野]
要約: [200文字以内の簡潔な要約]
影響: [想定される影響や意義]"""
    
    else:
        return f"""以下のコンテンツを分析し、重要度スコア（1-10）付きで要約してください。

【分析観点】
- 情報の新規性
- 実用性・応用可能性
- 学習価値
- 将来への関連性

コンテンツ:
{content}

【出力形式】
重要度: [1-10]
カテゴリ: [内容分野]
要約: [200文字以内の簡潔な要約]
ポイント: [重要な要点を2-3個]"""


def extract_importance_score(summary_text):
    """要約テキストから重要度スコアを抽出"""
    match = re.search(r'重要度[:\s]*(\d+)', summary_text)
    if match:
        return min(10, max(1, int(match.group(1))))
    return 5  # デフォルトスコア


def smart_summarize(content, content_type="general", max_tokens=1500):
    """改善された要約機能（重要度判定・適応的処理）"""
    if not content or len(content.strip()) < 50:
        return "要約するには内容が不十分です"
    
    # コンテンツタイプに応じたプロンプト生成
    prompt = create_summary_prompt(content, content_type)
    
    # 軽量モデル（Qwen）で初回処理
    result = call_qwen(prompt, max_tokens, f"summary_{content_type}")
    
    # 重要度スコア抽出
    importance = extract_importance_score(result)
    
    # 高重要度（8以上）の場合はClaude再処理
    if importance >= 8:
        print(f"  高重要度コンテンツ({importance}/10)をCluade再処理")
        enhanced_prompt = prompt + "\n\n【追加指示】高重要度コンテンツとして詳細分析し、より精密な要約を作成してください。"
        result = call_claude(enhanced_prompt, max_tokens, f"enhanced_summary_{content_type}")
    
    return result
