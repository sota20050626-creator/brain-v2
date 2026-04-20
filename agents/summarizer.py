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
    except (json.JSONDecodeError, IOError) as e:
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
        
        COST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        
        print(f"  コスト記録: {label} ${round(cost, 6)} [{model}]")
        return cost
    except Exception as e:
        print(f"  コスト保存エラー: {e}")
        return 0


def call_qwen(prompt, max_tokens=2000, label="qwen_call", retry_count=0):
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
            raise Exception(f"API Error: {result['error']}")
            
        usage = result.get("usage", {})
        save_cost(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            label, model="qwen"
        )
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  Qwen3エラー: {e}")
        if retry_count < 2:
            print(f"  リトライ中... ({retry_count + 1}/2)")
            return call_qwen(prompt, max_tokens, label, retry_count + 1)
        print("  Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)


def call_claude(prompt, max_tokens=2000, label="claude_call", retry_count=0):
    """Claude API呼び出し"""
    api_key = os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        print("  CLAUDE_API_KEY未設定")
        return "API未設定のため処理できませんでした"
    
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
            raise Exception(f"API Error: {result['error']}")
        
        usage = result.get("usage", {})
        save_cost(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            label, model="claude"
        )
        return result["content"][0]["text"]
    except Exception as e:
        print(f"  Claudeエラー: {e}")
        if retry_count < 2:
            print(f"  リトライ中... ({retry_count + 1}/2)")
            return call_claude(prompt, max_tokens, label, retry_count + 1)
        return "API呼び出しに失敗しました"


def summarize_with_scoring(text, title="", max_tokens=1500):
    """要約と重要度スコアリングを改善"""
    enhanced_prompt = f"""以下のテキストを分析し、高品質な要約と重要度スコアリングを行ってください：

【タイトル】{title}
【本文】{text[:8000]}  # トークン制限対策

要求事項：
1. **要約** (200-400文字)：
   - 核心となる論点を3つ以内に整理
   - 具体的な数値・事実・固有名詞を保持
   - 時系列や因果関係を明確に

2. **重要度スコア** (1-10点、小数点1桁)：
   - 情報の新規性・希少性
   - 社会的・技術的インパクト
   - 実用性・応用可能性
   - 情報源の信頼性
   を総合的に評価

3. **重要なキーワード** (5個以内)：
   - 検索性を高める専門用語・固有名詞

以下の形式で出力：
要約: [要約文]
重要度: [スコア]
キーワード: [keyword1, keyword2, ...]
"""
    
    return call_claude(enhanced_prompt, max_tokens, "高精度要約")


def load_daily_data():
    """日次データの読み込み（エラーハンドリング強化）"""
    if not DATA_FILE.exists():
        return {"entries": []}
    
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
            # データ構造の検証
            if not isinstance(data, dict) or "entries" not in data:
                print("  データ構造が不正、初期化します")
                return {"entries": []}
            return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"  データファイル読み込みエラー: {e}")
        return {"entries": []}


def save_daily_data(data):
    """日次データの保存（安全性向上）"""
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        # バックアップ作成
        if DATA_FILE.exists():
            backup_file = DATA_FILE.with_suffix('.json.backup')
            DATA_FILE.rename(backup_file)
        
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # バックアップ削除（正常保存時）
        backup_file = DATA_FILE.with_suffix('.json.backup')
        if backup_file.exists():
            backup_file.unlink()
            
    except Exception as e:
        print(f"  データ保存エラー: {e}")
        # バックアップから復元
        backup_file = DATA_FILE.with_suffix('.json.backup')
        if backup_file.exists():
            backup_file.rename(DATA_FILE)
            print("  バックアップから復元しました")


def extract_importance_score(summary_text):
    """要約テキストから重要度スコアを抽出（改善版）"""
    patterns = [
        r'重要度[:\s]*([0-9]+\.?[0-9]*)',
        r'スコア[:\s]*([0-9]+\.?[0-9]*)',
        r'([0-9]+\.?[0-9]*)[点/10]'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, summary_text)
        if match:
            try:
                score = float(match.group(1))
                return min(max(score, 1.0), 10.0)  # 1-10の範囲に制限
            except ValueError:
                continue
    
    # スコアが見つからない場合はキーワード密度で推定
    keywords = ['重要', '画期的', '革新', '初', '新', '大きな', '重大']
    keyword_count = sum(1 for kw in keywords if kw in summary_text)
    return min(5.0 + keyword_count * 0.5, 8.0)


def add_entry(title, content, source_url="", tags=None):
    """エントリー追加（改善版）"""
    if not title.strip() or not content.strip():
        print("  エラー: タイトルまたは内容が空です")
        return False
    
    try:
        # 高精度要約の実行
        print("  高精度要約を生成中...")
        summary = summarize_with_scoring(content, title, 1500)
        
        importance = extract_importance_score(summary)
        
        data = load_daily_data()
        
        entry = {
            "id": len(data["entries"]) + 1,
            "timestamp": datetime.now().isoformat(),
            "title": title.strip(),
            "content": content.strip(),
            "summary": summary,
            "importance": importance,
            "source_url": source_url,
            "tags": tags or []
        }
        
        data["entries"].append(entry)
        save_daily_data(data)
        
        print(f"  エントリー追加完了 (重要度: {importance})")
        return True
        
    except Exception as e:
        print(f"  エントリー追加エラー: {e}")
        return False
