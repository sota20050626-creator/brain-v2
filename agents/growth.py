"""
growth.py - 成長エージェント
毎日: X投稿文の下書き生成（当日+前日の最新データ使用）
週1(月曜): トレンド分析 + ビジネスアイデア + note記事下書き + GitHub Issue起票 + 自動PR作成 + 新技術自己搭載
"""

import json
import os
import re
import base64
import urllib.request
import urllib.parse
import time
from datetime import datetime, timedelta
from pathlib import Path

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
WEEKDAY = datetime.now().weekday()
KNOWLEDGE_DIR = Path("knowledge")
PROPOSALS_DIR = KNOWLEDGE_DIR / "proposals"
DRAFTS_DIR = KNOWLEDGE_DIR / "drafts"
COST_FILE = KNOWLEDGE_DIR / "cost_log.json"
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000


def load_cost_log():
    """コストログを読み込み"""
    try:
        if not COST_FILE.exists():
            return {"monthly": {}, "total_usd": 0}
        with open(COST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"コストログ読み込みエラー: {e}")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label):
    """コスト計算と保存"""
    try:
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
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usd": round(cost, 6)
        })
        log["total_usd"] = round(sum(v["usd"] for v in log["monthly"].values()), 6)
        with open(COST_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        return cost
    except Exception as e:
        print(f"コスト保存エラー: {e}")
        return 0.0


def call_claude(prompt, max_tokens=2000, label="api_call", retries=3):
    """Claude APIを呼び出し（リトライ機能付き）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEYが設定されていません")
    
    for attempt in range(retries):
        try:
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
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
                
            if "error" in result:
                raise Exception(f"API エラー: {result['error']}")
                
            usage = result.get("usage", {})
            save_cost(usage.get("input_tokens", 0), usage.get("output_tokens", 0), label)
            return result["content"][0]["text"]
            
        except Exception as e:
            print(f"Claude API呼び出しエラー (試行 {attempt + 1}): {e}")
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)  # 指数バックオフ


def load_recent_data(days=30):
    """最近のデータを読み込み"""
    all_items, all_digests, all_tags = [], [], {}
    
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = KNOWLEDGE_DIR / "daily" / (date + ".json")
        if not filepath.exists():
            continue
            
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            
            # データの検証と処理
            if "items" in data and isinstance(data["items"], list):
                all_items.extend(data["items"])
            if "digest" in data and data["digest"]:
                all_digests.append(f"{date}: {data['digest']}")
            if "tags" in data and isinstance(data["tags"], dict):
                for tag, count in data["tags"].items():
                    all_tags[tag] = all_tags.get(tag, 0) + count
                    
        except (json.JSONDecodeError, KeyError) as e:
            print(f"データ読み込みエラー ({date}): {e}")
            continue
    
    return all_items, all_digests, all_tags


def generate_x_posts():
    """X投稿文の下書きを生成（品質向上版）"""
    try:
        # 最新2日分のデータを重点的に使用
        recent_items, recent_digests, _ = load_recent_data(days=2)
        
        if not recent_items and not recent_digests:
            print("投稿用のデータが不足しています")
            return
        
        # データを整理してプロンプトに含める
        items_summary = "\n".join([
            f"- {item.get('title', 'タイトル不明')}: {item.get('summary', '')[:100]}"
            for item in recent_items[:10]  # 上位10件
        ])
        
        digests_summary = "\n".join(recent_digests[:2])  # 最新2日分
        
        prompt = f"""
あなたはテックトレンドに詳しいソーシャルメディア専門家です。
以下の最新情報から、エンジニア向けの魅力的なX投稿（ツイート）を3つ作成してください。

【最新の注目アイテム】
{items_summary}

【最新の要約】
{digests_summary}

【投稿作成のルール】
1. 各投稿は280文字以内
2. エンジニアの興味を引く内容
3. 実用的な価値や学びがある
4. ハッシュタグを1-2個含める
5. 読みやすく、シェアしたくなる文体
6. 技術的な正確性を保つ
7. トレンドを踏まえた内容

投稿1〜3を、それぞれ「【投稿1】」「【投稿2】」「【投稿3】」で区切って出力してください。
"""
        
        response = call_claude(prompt, max_tokens=1500, label="x_posts_generation")
        
        # 投稿文を解析して保存
        posts = parse_posts_from_response(response)
        
        output_file = DRAFTS_DIR / f"x_posts_{TODAY}.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# X投稿下書き - {TODAY}\n\n")
            for i, post in enumerate(posts, 1):
                f.write(f"## 投稿案 {i}\n\n")
                f.write(f"