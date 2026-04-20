"""
knowledge_builder.py - 毎日のJSONデータをナレッジベースに変換
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

DAILY_DIR = Path("knowledge/daily")
KNOWLEDGE_FILE = Path("knowledge/knowledge_base.json")


def build_knowledge_base(days=30):
    knowledge = []

    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        fp = DAILY_DIR / f"{date}.json"
        if not fp.exists():
            continue

        with open(fp, encoding="utf-8") as f:
            data = json.load(f)

        items = data.get("summarized_items", [])
        digest = data.get("digest", "")

        if not items and not digest:
            continue

        entry = {
            "date": date,
            "digest": digest,
            "articles": []
        }

        for item in items:
            entry["articles"].append({
                "title": item.get("title_ja", item.get("title", "")),
                "summary": item.get("summary_ja", ""),
                "importance": item.get("importance", 5),
                "tags": item.get("tags", []),
                "category": item.get("category", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
            })

        knowledge.append(entry)

    result = {
        "built_at": datetime.now().isoformat(),
        "days_covered": len(knowledge),
        "total_articles": sum(len(e["articles"]) for e in knowledge),
        "entries": knowledge
    }

    with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Knowledge base built: {len(knowledge)} days, {result['total_articles']} articles")


if __name__ == "__main__":
    build_knowledge_base()
