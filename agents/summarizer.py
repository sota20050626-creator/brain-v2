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
    """コストログを読み込む（エラーハンドリング強化）"""
    if not COST_FILE.exists():
        return {"monthly": {}, "total_usd": 0}
    try:
        with open(COST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # データ整合性チェック
        if not isinstance(data, dict) or "monthly" not in data:
            print("  コストログ破損、初期化します")
            return {"monthly": {}, "total_usd": 0}
        return data
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"  コストログ読み込みエラー: {e}, 初期化します")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label, model="claude"):
    """コスト保存（エラーハンドリング強化）"""
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
            print(f"  Qwen3 APIエラー: {result['error']} → Claudeにフォールバック")
            return call_claude(prompt, max_tokens, label)
        
        usage = result.get("usage", {})
        save_cost(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            label, model="qwen"
        )
        return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        print(f"  Qwen3 HTTPエラー: {e.code} {e.reason} → Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    except urllib.error.URLError as e:
        print(f"  Qwen3 接続エラー: {e} → Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    except json.JSONDecodeError as e:
        print(f"  Qwen3 レスポンス解析エラー: {e} → Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)
    except Exception as e:
        print(f"  Qwen3 予期せぬエラー: {e} → Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude APIを呼び出す（エラーハンドリング強化）"""
    api_key = os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        print("  CLAUDE_API_KEY未設定")
        return "API呼び出しに失敗しました"
    
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
    
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())
        
        if "error" in result:
            print(f"  Claude APIエラー: {result['error']}")
            return "API呼び出しに失敗しました"
        
        usage = result.get("usage", {})
        save_cost(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            label, model="claude"
        )
        return result["content"][0]["text"]
    except Exception as e:
        print(f"  Claude呼び出しエラー: {e}")
        return "API呼び出しに失敗しました"


def calculate_importance_score(text):
    """重要度スコアを計算（改良版）"""
    score = 0
    text_lower = text.lower()
    
    # 重要キーワードによる加点（重み付け強化）
    high_priority_keywords = ['緊急', '重要', '問題', 'エラー', '障害', '失敗', '危険', 'バグ', 'セキュリティ']
    medium_priority_keywords = ['改善', '最適化', '更新', '変更', '新機能', 'リリース', '完了', 'タスク']
    low_priority_keywords = ['メモ', '確認', '参考', 'ログ', '記録']
    
    for keyword in high_priority_keywords:
        score += text_lower.count(keyword) * 10
    for keyword in medium_priority_keywords:
        score += text_lower.count(keyword) * 5
    for keyword in low_priority_keywords:
        score += text_lower.count(keyword) * 2
    
    # 文章の長さによる加点（情報量を考慮）
    score += min(len(text) // 50, 10)
    
    # 数字や日付の存在（具体性の指標）
    if re.search(r'\d{4}-\d{2}-\d{2}|\d+%|\d+件|\d+個', text):
        score += 3
    
    # 感嘆符や疑問符（緊急度の指標）
    score += min(text.count('!') + text.count('？') + text.count('?'), 5)
    
    return min(score, 100)  # 最大値を100に制限


def create_enhanced_summary(entries):
    """強化された要約を作成"""
    if not entries:
        return "今日の記録はありません。"
    
    # 重要度でソート
    sorted_entries = sorted(entries, key=lambda x: calculate_importance_score(x.get('content', '')), reverse=True)
    
    # 重要な項目を特定
    high_importance = [e for e in sorted_entries if calculate_importance_score(e.get('content', '')) >= 20]
    medium_importance = [e for e in sorted_entries if 10 <= calculate_importance_score(e.get('content', '')) < 20]
    
    summary_prompt = f"""
以下の記録を分析し、構造化された要約を作成してください：

【高重要度項目】({len(high_importance)}件)
{chr(10).join([f"- {e.get('content', '')[:200]}..." for e in high_importance[:5]])}

【中重要度項目】({len(medium_importance)}件)
{chr(10).join([f"- {e.get('content', '')[:100]}..." for e in medium_importance[:3]])}

要約形式：
1. 今日の重要な出来事（3-5項目）
2. 注目すべき変化や問題点
3. 次回への引き継ぎ事項
4. 全体的な傾向分析

簡潔で実用的な要約を日本語で作成してください。
"""
    
    return call_qwen(summary_prompt, max_tokens=1500, label="enhanced_summary")


def load_daily_data():
    """日次データを読み込む（エラーハンドリング強化）"""
    try:
        if not DATA_FILE.exists():
            print(f"  データファイルが存在しません: {DATA_FILE}")
            return []
        
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        
        if not isinstance(data, list):
            print("  データ形式が不正です")
            return []
        
        return data
    except Exception as e:
        print(f"  データ読み込みエラー: {e}")
        return []


def main():
    """メイン処理"""
    print(f"=== 日次要約生成 ({TODAY}) ===")
    
    entries = load_daily_data()
    if not entries:
        print("処理対象のデータがありません")
        return
    
    print(f"対象エントリ数: {len(entries)}件")
    
    # 強化された要約を生成
    summary = create_enhanced_summary(entries)
    
    print("\n=== 生成された要約 ===")
    print(summary)
    
    # 要約をファイルに保存
    summary_file = Path(f"knowledge/summaries/{TODAY}_summary.txt")
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(f"# 日次要約 - {TODAY}\n\n")
            f.write(summary)
            f.write(f"\n\n---\n生成時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"\n要約をファイルに保存しました: {summary_file}")
    except Exception as e:
        print(f"要約ファイル保存エラー: {e}")


if __name__ == "__main__":
    main()
