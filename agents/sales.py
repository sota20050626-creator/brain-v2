"""
sales.py - 完全自動営業リストアップエージェント
Wantedly・RSS・HackerNews・Redditから「AI導入/業務効率化」に課題を持つ企業を自動検出
→ なぜBrainが必要か・押せるポイントを自動生成
出力: knowledge/sales/sales_list_YYYY-MM-DD.md
"""

import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

TODAY = datetime.now().strftime("%Y-%m-%d")
KNOWLEDGE_DIR = Path("knowledge")
SALES_DIR = KNOWLEDGE_DIR / "sales"
COST_FILE = KNOWLEDGE_DIR / "cost_log.json"
SALES_DIR.mkdir(parents=True, exist_ok=True)

SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# 営業ターゲットになりうるキーワード
SALES_KEYWORDS = [
    # 課題系
    "情報収集 課題", "情報収集 大変", "情報収集 効率",
    "業務効率化 AI", "AI導入 検討", "AI活用 したい",
    "自動化 したい", "コンテンツ 自動", "発信 自動化",
    "マーケ 工数", "営業 自動化", "リサーチ 大変",
    # 英語
    "ai automation", "content automation", "ai research tool",
    "information gathering", "ai marketing", "workflow automation",
]

# WantedlyのRSSエンドポイント
WANTEDLY_RSS_FEEDS = [
    "https://www.wantedly.com/stories/feed?tag=AI",
    "https://www.wantedly.com/stories/feed?tag=DX",
    "https://www.wantedly.com/stories/feed?tag=%E8%87%AA%E5%8B%95%E5%8C%96",
]

# 追加RSSソース
EXTRA_RSS_FEEDS = [
    {"url": "https://zenn.dev/topics/ai/feed", "source": "zenn"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "source": "techcrunch"},
]


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
        "date": TODAY, "label": label,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "usd": round(cost, 6)
    })
    log["total_usd"] = round(sum(v["usd"] for v in log["monthly"].values()), 6)
    with open(COST_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return cost


def call_claude(prompt, max_tokens=1500, label="sales_api"):
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


def fetch_wantedly_rss():
    """WantedlyのRSSからAI/DX関連の企業記事を収集"""
    items = []
    for feed_url in WANTEDLY_RSS_FEEDS:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Brain/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read().decode("utf-8", errors="ignore")

            entries = re.findall(r"<item>(.*?)</item>", content, re.DOTALL)
            for entry in entries[:10]:
                title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", entry, re.DOTALL)
                link_m = re.search(r"<link>(.*?)</link>", entry, re.DOTALL)
                desc_m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>", entry, re.DOTALL)

                title = ""
                if title_m:
                    title = (title_m.group(1) or title_m.group(2) or "").strip()
                    title = re.sub(r"<[^>]+>", "", title).strip()

                link = (link_m.group(1) if link_m else "").strip()
                desc = ""
                if desc_m:
                    desc = (desc_m.group(1) or desc_m.group(2) or "").strip()
                    desc = re.sub(r"<[^>]+>", "", desc).strip()[:300]

                if title and link:
                    items.append({
                        "title": title,
                        "url": link,
                        "text": desc,
                        "source": "wantedly",
                    })

            time.sleep(1)
        except Exception as e:
            print("  Wantedly RSS error: " + str(e))

    print("  Wantedly: " + str(len(items)) + " items")
    return items


def fetch_extra_rss():
    """追加RSSソースから記事収集"""
    items = []
    for feed in EXTRA_RSS_FEEDS:
        try:
            req = urllib.request.Request(feed["url"], headers={"User-Agent": "Brain/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                content = r.read().decode("utf-8", errors="ignore")

            entries = re.findall(r"<item>(.*?)</item>", content, re.DOTALL)
            for entry in entries[:8]:
                title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", entry, re.DOTALL)
                link_m = re.search(r"<link>(.*?)</link>", entry, re.DOTALL)
                desc_m = re.search(r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>", entry, re.DOTALL)

                title = ""
                if title_m:
                    title = (title_m.group(1) or title_m.group(2) or "").strip()
                    title = re.sub(r"<[^>]+>", "", title).strip()

                link = (link_m.group(1) if link_m else "").strip()
                desc = ""
                if desc_m:
                    desc = (desc_m.group(1) or desc_m.group(2) or "").strip()
                    desc = re.sub(r"<[^>]+>", "", desc).strip()[:300]

                if title and link:
                    items.append({
                        "title": title,
                        "url": link,
                        "text": desc,
                        "source": feed["source"],
                    })

            time.sleep(1)
        except Exception as e:
            print("  RSS error (" + feed["source"] + "): " + str(e))

    print("  Extra RSS: " + str(len(items)) + " items")
    return items


def fetch_hackernews_sales():
    """HackerNewsからAI自動化・効率化関連の投稿を収集"""
    sales_kw = ["automation", "ai tool", "workflow", "productivity", "saas", "startup"]
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "Brain/1.0"}
        )
        with urllib.request.urlopen(req) as r:
            story_ids = json.loads(r.read())[:80]

        items = []
        for sid in story_ids:
            if len(items) >= 15:
                break
            try:
                with urllib.request.urlopen(
                    "https://hacker-news.firebaseio.com/v0/item/" + str(sid) + ".json"
                ) as r:
                    item = json.loads(r.read())
                title_lower = item.get("title", "").lower()
                if any(kw in title_lower for kw in sales_kw):
                    items.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", "https://news.ycombinator.com/item?id=" + str(sid)),
                        "text": "",
                        "source": "hackernews",
                    })
                time.sleep(0.05)
            except Exception:
                continue

        print("  HackerNews: " + str(len(items)) + " items")
        return items
    except Exception as e:
        print("  HackerNews error: " + str(e))
        return []


def filter_sales_targets(all_items):
    """収集した記事からBrainの営業ターゲットになりうる記事をフィルタリング"""
    targets = []
    keywords = [
        "ai", "自動化", "automation", "効率化", "情報収集",
        "コンテンツ", "マーケ", "発信", "dx", "スタートアップ",
        "saas", "tool", "workflow", "productivity",
    ]
    for item in all_items:
        text = (item["title"] + " " + item["text"]).lower()
        if any(kw in text for kw in keywords):
            targets.append(item)

    # 重複除去
    seen = set()
    unique = []
    for item in targets:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    return unique[:20]


def analyze_targets_batch(targets):
    """フィルタリングしたターゲットをまとめてClaudeで分析"""
    targets_text = "\n".join([
        str(i+1) + ". タイトル: " + t["title"] + "\n   URL: " + t["url"] + "\n   内容: " + t["text"][:150]
        for i, t in enumerate(targets)
    ])

    prompt = """あなたはAI自動化ツール「Brain」の営業担当です。
以下の記事・投稿を見て、Brainの営業ターゲットになりうる企業・個人を分析してください。

【Brainとは】
毎日自動でAI情報収集・要約・X投稿文生成・営業リスト作成を行う自律型エージェント。
メニュー: スポット受託¥49,800〜・月次サポート¥30,000/月・note記事¥980

【収集した記事一覧】
""" + targets_text + """

各記事について以下をJSON配列で返してください。
ターゲットにならないと判断した場合はスキップしてOKです。
上位10件のみ選んでください。

[
  {
    "rank": 優先順位(1〜10),
    "name": "企業名または発信者名（記事から推測）",
    "source": "記事のソース",
    "url": "記事のURL",
    "estimated_issue": "推定される課題（1文）",
    "why_brain": "なぜBrainが必要か（具体的に1〜2文）",
    "push_point": "営業で押せるポイント（1文・数字や効果を含める）",
    "first_message": "最初のDM・メッセージの書き出し（40字以内）",
    "plan": "おすすめプラン名",
    "price": "想定単価",
    "probability": "高/中/低"
  }
]

JSONのみを返してください。余計な説明不要。"""

    response = call_claude(prompt, max_tokens=2000, label="sales_analyze_batch")
    try:
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if not match:
            return []
        return json.loads(match.group())
    except Exception as e:
        print("  分析パースエラー: " + str(e))
        return []


def save_sales_list(analyzed_targets, total_scanned):
    path = SALES_DIR / ("sales_list_" + TODAY + ".md")

    with open(path, "w", encoding="utf-8") as f:
        f.write("# 🎯 Brain 自動営業リスト - " + TODAY + "\n\n")
        f.write("> Brain自動生成 | スキャン件数: " + str(total_scanned) + "件 → 営業候補: " + str(len(analyzed_targets)) + "件\n\n")
        f.write("---\n\n")

        if not analyzed_targets:
            f.write("本日は営業ターゲットが見つかりませんでした。\n")
            return path

        # 優先度TOP3をハイライト
        f.write("## 🔥 今日のTOP3ターゲット\n\n")
        for t in analyzed_targets[:3]:
            f.write("### " + str(t.get("rank", "?")) + "位 " + t.get("name", "不明") + "\n")
            f.write("- **ソース**: [" + t.get("source", "") + "](" + t.get("url", "") + ")\n")
            f.write("- **推定課題**: " + t.get("estimated_issue", "") + "\n")
            f.write("- **なぜBrainか**: " + t.get("why_brain", "") + "\n")
            f.write("- **押せるポイント**: " + t.get("push_point", "") + "\n")
            f.write("- **最初の一言**: " + t.get("first_message", "") + "\n")
            f.write("- **おすすめプラン**: " + t.get("plan", "") + " / " + t.get("price", "") + "\n")
            f.write("- **成約確度**: " + t.get("probability", "") + "\n\n")

        # 残りのリスト
        if len(analyzed_targets) > 3:
            f.write("---\n\n## 📋 その他の候補\n\n")
            f.write("| 順位 | 名前 | 課題 | プラン | 確度 | リンク |\n")
            f.write("|------|------|------|--------|------|--------|\n")
            for t in analyzed_targets[3:]:
                f.write(
                    "| " + str(t.get("rank", "?")) +
                    " | " + t.get("name", "不明") +
                    " | " + t.get("estimated_issue", "")[:30] +
                    " | " + t.get("price", "") +
                    " | " + t.get("probability", "") +
                    " | [リンク](" + t.get("url", "") + ") |\n"
                )

        f.write("\n\n---\n\n")
        f.write("## 📌 今日のアクション\n\n")
        f.write("1. TOP3のリンクを確認してアカウントをチェック\n")
        f.write("2. 「最初の一言」をベースにDMを送る\n")
        f.write("3. 反応があったらClaudeに「提案書作って」と依頼\n")

    print("  営業リスト保存完了: " + str(path))
    return path


def main():
    print("Brain Sales Agent 起動... [" + TODAY + "]")

    all_items = []

    print("  Wantedly収集中...")
    all_items.extend(fetch_wantedly_rss())

    print("  RSS収集中...")
    all_items.extend(fetch_extra_rss())

    print("  HackerNews収集中...")
    all_items.extend(fetch_hackernews_sales())

    total_scanned = len(all_items)
    print("  合計スキャン: " + str(total_scanned) + "件")

    print("  営業ターゲットをフィルタリング中...")
    targets = filter_sales_targets(all_items)
    print("  候補: " + str(len(targets)) + "件")

    if not targets:
        print("  ターゲットが見つかりませんでした")
        save_sales_list([], total_scanned)
        return

    print("  Claude で分析中...")
    analyzed = analyze_targets_batch(targets)
    print("  分析完了: " + str(len(analyzed)) + "件")

    save_sales_list(analyzed, total_scanned)
    print("営業リスト生成完了!")


if __name__ == "__main__":
    main()
    
