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
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
import logging

# ロギング設定
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


def load_cost_log():
    """コストログを読み込む"""
    try:
        if not COST_FILE.exists():
            return {"monthly": {}, "total_usd": 0}
        with open(COST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"コストログの読み込みに失敗: {e}")
        return {"monthly": {}, "total_usd": 0}


def save_cost(input_tokens, output_tokens, label):
    """API使用コストを記録"""
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
        logger.error(f"コスト記録に失敗: {e}")
        return 0


def call_claude(prompt, max_tokens=2000, label="api_call", retry_count=3):
    """Claude APIを呼び出し（リトライ機能付き）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEYが設定されていません")
    
    for attempt in range(retry_count):
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
            
            with urllib.request.urlopen(req, timeout=60) as r:
                result = json.loads(r.read())
            
            usage = result.get("usage", {})
            save_cost(usage.get("input_tokens", 0), usage.get("output_tokens", 0), label)
            return result["content"][0]["text"]
            
        except urllib.error.HTTPError as e:
            logger.warning(f"API呼び出し失敗 (試行{attempt+1}/{retry_count}): HTTP {e.code}")
            if attempt == retry_count - 1:
                raise
        except Exception as e:
            logger.warning(f"API呼び出し失敗 (試行{attempt+1}/{retry_count}): {e}")
            if attempt == retry_count - 1:
                raise


def load_recent_data(days=30):
    """最近のデータを読み込む"""
    all_items, all_digests, all_tags = [], [], {}
    
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = KNOWLEDGE_DIR / "daily" / (date + ".json")
        if not filepath.exists():
            continue
            
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            
            # データ品質チェック
            items = data.get("items", [])
            if not items:
                continue
                
            all_items.extend(items)
            
            # ダイジェストの統合
            if "digest" in data:
                all_digests.append(data["digest"])
            
            # タグの統合（出現頻度付き）
            for item in items:
                for tag in item.get("tags", []):
                    all_tags[tag] = all_tags.get(tag, 0) + 1
                    
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"ファイル読み込みエラー {filepath}: {e}")
            continue
    
    # 人気タグの抽出（上位20個）
    popular_tags = sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:20]
    
    return all_items, all_digests, popular_tags


def generate_quality_posts(recent_items, popular_tags):
    """高品質な投稿を生成"""
    # 最新で関連性の高い項目を選別
    high_value_items = []
    for item in recent_items[:50]:  # 最新50件から選別
        # スコアリング（いいね数、コメント数、タグの人気度等）
        score = 0
        score += item.get("likes", 0) * 2
        score += item.get("comments", 0) * 3
        score += len(item.get("content", "")) * 0.01
        
        # 人気タグボーナス
        for tag in item.get("tags", []):
            for pop_tag, count in popular_tags:
                if tag == pop_tag:
                    score += count * 0.5
                    break
        
        if score > 10:  # 閾値以上のアイテムを選択
            high_value_items.append(item)
    
    if not high_value_items:
        high_value_items = recent_items[:10]  # フォールバック
    
    tag_summary = ", ".join([f"{tag}({count})" for tag, count in popular_tags[:10]])
    
    prompt = f"""あなたは優秀なコンテンツクリエイターです。以下の最新データを基に、エンゲージメントの高いX投稿を3つ生成してください。

【最新の高価値コンテンツ】
{json.dumps(high_value_items[:10], ensure_ascii=False, indent=2)}

【人気トレンドタグ】
{tag_summary}

【投稿品質要件】
1. 280文字以内の簡潔な文章
2. 具体的な数値や事例を含める
3. 読者の行動を促すCTAを含める
4. ハッシュタグは2-3個に絞る
5. 話題性と実用性のバランス

【出力形式】
投稿1: [内容]
投稿2: [内容] 
投稿3: [内容]

各投稿は実際にエンゲージメントが期待できる高品質な内容にしてください。"""

    return call_claude(prompt, max_tokens=1500, label="high_quality_posts")


def generate_business_ideas(recent_items, popular_tags):
    """データドリブンなビジネスアイデアを生成"""
    # マーケットトレンドの分析
    market_signals = []
    for item in recent_items[:30]:
        if any(keyword in item.get("content", "").lower() 
               for keyword in ["市場", "需要", "課題", "問題", "ニーズ", "トレンド"]):
            market_signals.append(item)
    
    prompt = f"""あなたは経験豊富なビジネスアナリストです。以下のマーケットシグナルと人気トレンドから、実現可能性の高いビジネスアイデアを3つ提案してください。

【マーケットシグナル】
{json.dumps(market_signals[:10], ensure_ascii=False, indent=2)}

【人気トレンド】
{", ".join([tag for tag, count in popular_tags[:15]])}

【提案要件】
1. 具体的なターゲット市場の特定
2. 収益モデルの明確化
3. 競合優位性の説明
4. 実装ステップの概略
5. 予想される課題とリスク

【出力形式】
## ビジネスアイデア1: [タイトル]
- ターゲット: [詳細]
- 収益モデル: [説明]
- 競合優位性: [説明]
- 実装ステップ: [概要]
- リスク: [主要リスク]

同様の形式でアイデア2、3も出力"""

    return call_claude(prompt, max_tokens=2500, label="business_ideas")


def create_github_issue(title, content, repo_owner="your_repo", repo_name="your_repo"):
    """GitHub Issueを作成（エラーハンドリング強化）"""
    try:
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            logger.warning("GITHUB_TOKENが設定されていません")
            return None
        
        issue_data = {
            "title": title,
            "body": content,
            "labels": ["auto-generated", "enhancement"]
        }
        
        payload = json.dumps(issue_data).encode()
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues",
            data=payload,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            }
        )
        
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            logger.info(f"GitHub Issue作成成功: #{result['number']}")
            return result
            
    except Exception as e:
        logger.error(f"GitHub Issue作成失敗: {e}")
        return None


def main():
    """メイン処理"""
    try:
        logger.info(f"成長エージェント開始 - {TODAY} (曜日: {WEEKDAY})")
        
        # データ読み込み
        recent_items, digests, popular_tags = load_recent_data()
        if not recent_items:
            logger.warning("処理対象のデータがありません")
            return
        
        logger.info(f"データ読み込み完了: {len(recent_items)}件のアイテム、{len(popular_tags)}個のタグ")
        
        # 毎日の投稿生成（品質向上版）
        posts = generate_quality_posts(recent_items, popular_tags)
        posts_file = DRAFTS_DIR / f"{TODAY}_posts.md"
        with open(posts_file, "w", encoding="utf-8") as f:
            f.write(f"# X投稿下書き - {TODAY}\n\n{posts}")
        logger.info(f"投稿下書き生成完了: {posts_file}")
        
        # 週次処理（月曜日）
        if WEEKDAY == 0:
            logger.info("週次処理を開始")
            
            # ビジネスアイデア生成
            ideas = generate_business_ideas(recent_items, popular_tags)
            ideas_file = PROPOSALS_DIR / f"{TODAY}_business_ideas.md"
            with open(ideas_file, "w", encoding="utf-8") as f:
                f.write(f"# ビジネスアイデア提案 - {TODAY}\n\n{ideas}")
            
            # GitHub Issue作成
            issue_title = f"週次改善提案 - {TODAY}"
            create_github_issue(issue_title, ideas)
            
            logger.info("週次処理完了")
        
        # コスト情報の表示
        cost_log = load_cost_log()
        current_month = TODAY[:7]
        if current_month in cost_log["monthly"]:
            monthly_cost = cost_log["monthly"][current_month]["usd"]
            logger.info(f"今月のAPI使用料: ${monthly_cost:.4f}")
        
        logger.info("成長エージェント処理完了")
        
    except Exception as e:
        logger.error(f"メイン処理でエラー発生: {e}")
        raise


if __name__ == "__main__":
    main()
