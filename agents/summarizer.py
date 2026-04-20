import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_FILE = Path("knowledge/daily/" + TODAY + ".json")
COST_FILE = Path("knowledge/cost_log.json")

# Claude API料金
SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# Qwen3料金（OpenRouter経由）
QWEN_INPUT_PRICE = 0.1 / 1_000_000
QWEN_OUTPUT_PRICE = 0.3 / 1_000_000


def load_cost_log():
    if not COST_FILE.exists():
        return {"monthly": {}, "total_usd": 0}
    try:
        with open(COST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  コストログ読み込みエラー: {e}")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label, model="claude"):
    try:
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
        
        # ディレクトリ作成
        COST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        
        print("  コスト記録: " + label + " $" + str(round(cost, 6)) + " [" + model + "]")
        return cost
    except Exception as e:
        print(f"  コスト記録エラー: {e}")
        return 0


def call_qwen(prompt, max_tokens=2000, label="qwen_call"):
    """Qwen3をOpenRouter経由で呼び出す（軽い処理用・激安）"""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  OPENROUTER_API_KEY未設定、Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    
    payload = json.dumps({
        "model": "qwen/qwen3-8b",
        "max_tokens": max_tokens,
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
    
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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
        print("  Qwen3エラー: " + str(e) + " → Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude API呼び出し（フォールバック用）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise Exception("ANTHROPIC_API_KEY未設定")
    
    payload = json.dumps({
        "model": "claude-3-sonnet-20240229",
        "max_tokens": max_tokens,
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
    
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
    
    usage = result.get("usage", {})
    save_cost(
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        label, model="claude"
    )
    return result["content"][0]["text"]


def calculate_importance_score(text):
    """重要度スコアを計算（改善版）"""
    score = 0
    text_lower = text.lower()
    
    # 重要キーワード（重み付き）
    high_importance = ["緊急", "重要", "危険", "注意", "警告", "課題", "問題", "決定", "変更"]
    medium_importance = ["会議", "締切", "予定", "報告", "連絡", "確認", "検討"]
    low_importance = ["参考", "情報", "メモ", "記録", "補足"]
    
    for word in high_importance:
        score += text_lower.count(word) * 3
    for word in medium_importance:
        score += text_lower.count(word) * 2
    for word in low_importance:
        score += text_lower.count(word) * 1
    
    # 文章の長さによる調整
    if len(text) > 1000:
        score += 2
    elif len(text) > 500:
        score += 1
    
    # 数字や日付の存在
    if re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', text):  # 日付
        score += 1
    if re.search(r'[0-9]+%', text):  # パーセント
        score += 1
    
    return min(score, 10)  # 最大10点


def improved_summarize(text, max_length=200):
    """改善された要約関数"""
    if len(text) <= max_length:
        return text
    
    # 重要度スコア計算
    importance = calculate_importance_score(text)
    
    # 高精度要約プロンプト
    prompt = f"""以下のテキストを{max_length}文字以内で要約してください。

重要度スコア: {importance}/10

要約時の指針:
- 重要な数字、日付、固有名詞は保持
- 結論や決定事項を優先
- アクションアイテムがあれば含める
- 簡潔で分かりやすい日本語で

テキスト:
{text}

要約:"""
    
    try:
        # 重要度が高い場合はClaudeを使用
        if importance >= 7:
            summary = call_claude(prompt, 300, f"高重要度要約(score:{importance})")
        else:
            summary = call_qwen(prompt, 300, f"要約(score:{importance})")
        
        # 長すぎる場合は切り詰め
        if len(summary) > max_length:
            summary = summary[:max_length-3] + "..."
        
        return summary.strip()
    
    except Exception as e:
        print(f"  要約エラー: {e}")
        # フォールバック: 単純な切り詰め
        return text[:max_length-3] + "..." if len(text) > max_length else text


def process_daily_data():
    """日次データの処理メイン関数"""
    if not DATA_FILE.exists():
        print(f"  データファイルが見つかりません: {DATA_FILE}")
        return
    
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        
        processed_count = 0
        for item in data.get("items", []):
            if "original_text" in item and "summary" not in item:
                item["summary"] = improved_summarize(item["original_text"])
                item["importance_score"] = calculate_importance_score(item["original_text"])
                processed_count += 1
        
        # 結果保存
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"  処理完了: {processed_count}件の要約を生成")
    
    except Exception as e:
        print(f"  データ処理エラー: {e}")


if __name__ == "__main__":
    process_daily_data()
