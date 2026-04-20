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
    """コストログを読み込む"""
    if not COST_FILE.exists():
        return {"monthly": {}, "total_usd": 0}
    try:
        with open(COST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  コストログ読み込みエラー: {e}")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label, model="claude"):
    """コスト情報を保存する"""
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
        
        # ディレクトリが存在しない場合は作成
        COST_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        with open(COST_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        print("  コスト記録: " + label + " $" + str(round(cost, 6)) + " [" + model + "]")
        return cost
    except Exception as e:
        print(f"  コスト保存エラー: {e}")
        return 0.0


def call_qwen(prompt, max_tokens=2000, label="qwen_call"):
    """Qwen3をOpenRouter経由で呼び出す（軽い処理用・激安）"""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  OPENROUTER_API_KEY未設定、Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    
    payload = json.dumps({
        "model": "qwen/qwen3.6-plus-preview:free",
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
            print(f"  Qwen3 APIエラー: {result['error']}")
            return call_claude(prompt, max_tokens, label)
        
        usage = result.get("usage", {})
        save_cost(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            label, model="qwen"
        )
        return result["choices"][0]["message"]["content"]
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        print(f"  Qwen3エラー: {e} -> Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    except Exception as e:
        print(f"  Qwen3予期しないエラー: {e} -> Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude APIを呼び出す"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ANTHROPIC_API_KEY未設定")
        return "API設定エラー"
    
    payload = json.dumps({
        "model": "claude-3-5-sonnet-20241022",
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
    
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())
        
        if "error" in result:
            print(f"  Claude APIエラー: {result['error']}")
            return "Claude APIエラー"
        
        usage = result.get("usage", {})
        save_cost(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            label, model="claude"
        )
        return result["content"][0]["text"]
    except Exception as e:
        print(f"  Claudeエラー: {e}")
        return "Claude呼び出しエラー"


def calculate_importance_score(entry):
    """エントリの重要度スコアを計算（改良版）"""
    score = 0
    text = entry.get("text", "").lower()
    
    # 基本的なキーワード
    high_keywords = ["重要", "緊急", "警告", "エラー", "失敗", "問題", "critical", "error", "urgent", "important"]
    medium_keywords = ["注意", "改善", "更新", "変更", "warning", "update", "change", "notice"]
    low_keywords = ["情報", "通知", "完了", "info", "notification", "completed"]
    
    # キーワードベースのスコア
    for keyword in high_keywords:
        if keyword in text:
            score += 3
    for keyword in medium_keywords:
        if keyword in text:
            score += 2
    for keyword in low_keywords:
        if keyword in text:
            score += 1
    
    # 文字数による調整（長い内容は重要度が高い可能性）
    text_length = len(text)
    if text_length > 500:
        score += 2
    elif text_length > 200:
        score += 1
    
    # タイムスタンプの新しさ（今日の情報は重要度が高い）
    if entry.get("timestamp", "").startswith(TODAY):
        score += 1
    
    return min(score, 10)  # 最大10点


def enhanced_summarize(text, importance_score=0):
    """重要度を考慮した要約（改良版）"""
    if importance_score >= 7:
        # 高重要度：詳細な要約
        prompt = f"""以下の重要な情報を詳細に要約してください。キーポイントと具体的な詳細を含めてください：

{text}

要約は以下の形式で：
・重要ポイント：
・詳細：
・影響・注意点："""
        max_tokens = 800
    elif importance_score >= 4:
        # 中重要度：標準的な要約
        prompt = f"""以下の情報を要約してください。主要なポイントと必要な詳細を含めてください：

{text}

簡潔で分かりやすい要約をお願いします。"""
        max_tokens = 400
    else:
        # 低重要度：簡潔な要約
        prompt = f"""以下の情報を1-2文で簡潔に要約してください：

{text}"""
        max_tokens = 200
    
    return call_qwen(prompt, max_tokens, f"要約_重要度{importance_score}")


def process_daily_data():
    """本日のデータを処理・要約する"""
    if not DATA_FILE.exists():
        print(f"  データファイルが見つかりません: {DATA_FILE}")
        return
    
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  データファイル読み込みエラー: {e}")
        return
    
    if not data:
        print("  処理するデータがありません")
        return
    
    print(f"  {len(data)}件のデータを処理中...")
    
    # 重要度スコア計算と要約
    processed_data = []
    for i, entry in enumerate(data):
        try:
            importance = calculate_importance_score(entry)
            summary = enhanced_summarize(entry.get("text", ""), importance)
            
            processed_entry = {
                "original": entry,
                "importance_score": importance,
                "summary": summary,
                "processed_at": datetime.now().isoformat()
            }
            processed_data.append(processed_entry)
            print(f"    処理完了: {i+1}/{len(data)} (重要度: {importance})")
        except Exception as e:
            print(f"    エントリ処理エラー {i+1}: {e}")
            continue
    
    # 処理結果を保存
    output_file = DATA_FILE.parent / f"{TODAY}_processed.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(processed_data, f, ensure_ascii=False, indent=2)
        print(f"  処理完了: {output_file}")
    except IOError as e:
        print(f"  出力ファイル保存エラー: {e}")


if __name__ == "__main__":
    print(f"データ要約処理開始: {TODAY}")
    process_daily_data()
    print("処理完了")
