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

# 丁寧語レベル適応型テキスト生成
POLITENESS_RULES = {
    "to_polite": {
        # である調からです・ます調への変換
        r"である$": "です",
        r"である([。、])": r"です\1",
        r"だ$": "です",
        r"だ([。、])": r"です\1",
        r"する$": "します",
        r"する([。、])": r"します\1",
        r"できる$": "できます",
        r"できる([。、])": r"できます\1",
        r"ない$": "ません",
        r"ない([。、])": r"ません\1",
        r"いる$": "います",
        r"いる([。、])": r"います\1",
        r"なる$": "なります",
        r"なる([。、])": r"なります\1",
        r"考える$": "考えます",
        r"考える([。、])": r"考えます\1",
        r"思う$": "思います",
        r"思う([。、])": r"思います\1"
    },
    "to_casual": {
        # です・ます調からである調への変換
        r"です$": "である",
        r"です([。、])": r"である\1",
        r"します$": "する",
        r"します([。、])": r"する\1",
        r"できます$": "できる",
        r"できます([。、])": r"できる\1",
        r"ません$": "ない",
        r"ません([。、])": r"ない\1",
        r"います$": "いる",
        r"います([。、])": r"いる\1",
        r"なります$": "なる",
        r"なります([。、])": r"なる\1",
        r"考えます$": "考える",
        r"考えます([。、])": r"考える\1",
        r"思います$": "思う",
        r"思います([。、])": r"思う\1"
    }
}

PLATFORM_POLITENESS = {
    "x": "casual",      # X(Twitter)はカジュアル
    "note": "polite",   # noteは丁寧語
    "default": "polite"
}


def apply_politeness_level(text, platform="default"):
    """プラットフォームに応じて敬語レベルを調整"""
    try:
        target_level = PLATFORM_POLITENESS.get(platform, "polite")
        
        if target_level == "polite":
            # 丁寧語に変換
            for pattern, replacement in POLITENESS_RULES["to_polite"].items():
                text = re.sub(pattern, replacement, text)
        elif target_level == "casual":
            # カジュアル調に変換
            for pattern, replacement in POLITENESS_RULES["to_casual"].items():
                text = re.sub(pattern, replacement, text)
        
        return text
    except Exception as e:
        print(f"敬語レベル調整でエラー: {e}")
        return text


def load_cost_log():
    if not COST_FILE.exists():
        return {"monthly": {}, "total_usd": 0}
    with open(COST_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cost(input_tokens, output_tokens, label):
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


def call_claude(prompt, max_tokens=2000, label="api_call"):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
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
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())
    usage = result.get("usage", {})
    save_cost(usage.get("input_tokens", 0), usage.get("output_tokens", 0), label)
    return result["content"][0]["text"]


def load_recent_data(days=30):
    all_items, all_digests, all_tags = [], [], {}
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = KNOWLEDGE_DIR / "daily" / (date + ".json")
        if not filepath.exists():
            continue
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        all_items.extend(data.get("items", []))
        all_digests.extend(data.get("digest", []))
        for tag, count in data.get("tags", {}).items():
            all_tags[tag] = all_tags.get(tag, 0) + count
    return all_items, all_digests, all_tags


def generate_x_post():
    """X投稿文の下書き生成（カジュアル調）"""
    try:
        recent_items, _, tags = load_recent_data(2)  # 当日+前日
        top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:5]
        
        prompt = f"""
        最新2日間の学習データから、エンジニアのX投稿文を作成してください。
        
        主要トレンド: {[tag for tag, _ in top_tags]}
        
        要件:
        - 140文字以内
        - 技術的洞察を含む
        - エンジニア向け
        - カジュアルな文体で
        """
        
        post = call_claude(prompt, max_tokens=200, label="x_post_generation")
        
        # X用にカジュアル調に調整
        post = apply_politeness_level(post, platform="x")
        
        output_file = DRAFTS_DIR / f"x_post_{TODAY}.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# X投稿下書き ({TODAY})\n\n")
            f.write(f"## 投稿文\n\n{post}\n\n")
            f.write(f"## トレンドタグ\n{dict(top_tags)}\n")
        
        print(f"X投稿下書き生成完了: {output_file}")
        return post
    except Exception as e:
        print(f"X投稿生成でエラー: {e}")
        return None


def generate_note_article():
    """note記事の下書き生成（丁寧語調）"""
    try:
        recent_items, digests, tags = load_recent_data(7)  # 週間データ
        top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:10]
        
        prompt = f"""
        過去1週間の学習データから、技術系note記事を作成してください。
        
        主要トピック: {[tag for tag, _ in top_tags]}
        
        要件:
        - 2000-3000文字程度
        - 技術的な深い洞察
        - 読者に価値を提供
        - 丁寧語で記述
        - マークダウン形式
        """
        
        article = call_claude(prompt, max_tokens=4000, label="note_article_generation")
        
        # note用に丁寧語調に調整
        article = apply_politeness_level(article, platform="note")
        
        output_file = DRAFTS_DIR / f"note_article_{TODAY}.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# note記事下書き ({TODAY})\n\n")
            f.write(article)
        
        print(f"note記事下書き生成完了: {output_file}")
        return article
    except Exception as e:
        print(f"note記事生成でエラー: {e}")
        return None


def analyze_trends():
    """トレンド分析"""
    try:
        _, _, tags = load_recent_data(30)
        top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:20]
        
        prompt = f"""
        過去30日間のデータから技術トレンドを分析してください。
        
        タグ頻度: {dict(top_tags)}
        
        以下の観点で分析:
        - 急上昇技術
        - 長期トレンド
        - 今後の予測
        - ビジネス機会
        """
        
        analysis = call_claude(prompt, max_tokens=3000, label="trend_analysis")
        
        output_file = PROPOSALS_DIR / f"trend_analysis_{TODAY}.md"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# トレンド分析 ({TODAY})\n\n")
            f.write(analysis)
        
        print(f"トレンド分析完了: {output_file}")
        return analysis
    except Exception as e:
        print(f"トレンド分析でエラー: {e}")
        return None


def main():
    """メイン処理"""
    print(f"成長エージェント開始 ({TODAY})")
    
    # 毎日実行: X投稿下書き生成
    print("=== X投稿下書き生成 ===")
    generate_x_post()
    
    # 月曜日のみ実行: 週次処理
    if WEEKDAY == 0:  # 月曜日
        print("\n=== 週次処理開始 ===")
        
        print("=== トレンド分析 ===")
        analyze_trends()
        
        print("=== note記事下書き生成 ===")
        generate_note_article()
    
    # コスト状況表示
    try:
        cost_log = load_cost_log()
        month = TODAY[:7]
        if month in cost_log["monthly"]:
            month_cost = cost_log["monthly"][month]["usd"]
            print(f"\n今月のAPI使用料: ${month_cost:.6f}")
    except Exception as e:
        print(f"コスト表示でエラー: {e}")
    
    print("成長エージェント完了")


if __name__ == "__main__":
    main()
