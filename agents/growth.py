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
from datetime import datetime, timedelta
from pathlib import Path
import logging
import time
import random

# ログ設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

# リトライ設定
MAX_RETRIES = 3
RETRY_DELAY = 2


def load_cost_log():
    """コストログの読み込み"""
    try:
        if not COST_FILE.exists():
            return {"monthly": {}, "total_usd": 0}
        with open(COST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"コストログ読み込みエラー: {e}")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label):
    """コスト保存とログ出力の改善"""
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
        
        COST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        
        logger.info(f"API呼び出し完了 - {label}: ${cost:.6f} (入力:{input_tokens}, 出力:{output_tokens})")
        return cost
    except Exception as e:
        logger.error(f"コスト保存エラー: {e}")
        return 0


def call_claude(prompt, max_tokens=2000, label="api_call"):
    """Claude API呼び出しの改善（リトライ機能、エラーハンドリング）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY環境変数が設定されていません")
    
    for attempt in range(MAX_RETRIES):
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
                raise Exception(f"API Error: {result['error']}")
            
            usage = result.get("usage", {})
            save_cost(usage.get("input_tokens", 0), usage.get("output_tokens", 0), label)
            return result["content"][0]["text"]
            
        except Exception as e:
            logger.warning(f"API呼び出し失敗 (試行 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt) + random.uniform(0, 1))
            else:
                logger.error(f"API呼び出しが最大試行回数で失敗: {e}")
                raise


def load_recent_data(days=30):
    """最近のデータ読み込みとエラーハンドリング強化"""
    all_items, all_digests, all_tags = [], [], {}
    loaded_days = 0
    
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = KNOWLEDGE_DIR / "daily" / (date + ".json")
        if not filepath.exists():
            continue
            
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            all_items.extend(data.get("items", []))
            all_digests.append(data.get("daily_digest", ""))
            for k, v in data.get("tags", {}).items():
                all_tags[k] = all_tags.get(k, 0) + v
            loaded_days += 1
        except Exception as e:
            logger.warning(f"データファイル読み込みエラー {date}: {e}")
    
    logger.info(f"{loaded_days}日分のデータを読み込み完了")
    return all_items, all_digests, all_tags


def generate_quality_posts():
    """投稿文生成の品質向上"""
    try:
        all_items, all_digests, all_tags = load_recent_data(days=2)
        
        if not all_items:
            logger.warning("投稿生成用のデータがありません")
            return
        
        # より具体的で詳細なプロンプト
        prompt = f"""あなたは優秀なソーシャルメディア戦略家です。以下のデータから魅力的なX投稿を3つ作成してください。

【データ分析】
- 収集アイテム数: {len(all_items)}
- トップタグ: {dict(sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:5])}
- 最新の要約: {all_digests[0] if all_digests else "なし"}

【投稿作成ルール】
1. 各投稿は140文字以内
2. ハッシュタグを適切に使用
3. エンゲージメントを促す要素を含める
4. 技術的洞察と実用性のバランス
5. フォロワーにとって価値のある情報

【出力形式】
