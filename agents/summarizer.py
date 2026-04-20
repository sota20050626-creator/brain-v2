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
    with open(COST_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cost(input_tokens, output_tokens, label, model="claude"):
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
    with open(COST_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print("  コスト記録: " + label + " $" + str(round(cost, 6)) + " [" + model + "]")
    return cost


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
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
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


def call_claude(prompt, max_tokens=2000, label="api_call"):
    """Claude APIを呼び出す（重要な処理用・高品質）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")
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
    save_cost(
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        label, model="claude"
    )
    return result["content"][0]["text"]


def summarize_items(items):
    """要約はQwen3で処理（コスト削減）"""
    top_items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:5]
    items_text = "\n\n".join([
        "[" + str(i+1) + "] SOURCE: " + item["source"] + "\nTITLE: " + item["title"] + "\nTEXT: " + item.get("text","")[:200]
        for i, item in enumerate(top_items)
    ])
    prompt = """あなたはAI技術のエキスパートアナリストです。
以下の""" + str(len(top_items)) + """件のAI関連情報を分析してください。

""" + items_text + """

各アイテムについて以下のJSONフォーマットで回答してください。
必ずJSON配列のみを返し、余分なテキストは含めないこと。

[
  {
    "id": 1,
    "title_ja": "日本語タイトル",
    "summary_ja": "2から3文の日本語要約",
    "importance": 8,
    "tags": ["LLM", "ビジネス"],
    "category": "技術"
  }
]

importanceは1から10で評価。
tagsはLLM/Agent/ビジネス/画像生成/音声/コード/論文/中国AI/オープンソースから選択。
categoryは技術/ビジネス/ツール/論文/その他から選択。"""

    # 要約はQwen3で処理（激安）
    response = call_qwen(prompt, max_tokens=3000, label="summarize_items")
    try:
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if not match:
            return []
        summaries = json.loads(match.group())
    except json.JSONDecodeError:
        print("JSON parse error, skipping batch")
        return []

    results = []
    for s in summaries:
        idx = s["id"] - 1
        if 0 <= idx < len(top_items):
            item = top_items[idx].copy()
            item.update({
                "title_ja": s.get("title_ja", item["title"]),
                "summary_ja": s.get("summary_ja", ""),
                "importance": s.get("importance", 5),
                "tags": s.get("tags", []),
                "category": s.get("category", "その他"),
            })
            results.append(item)
    return sorted(results, key=lambda x: x.get("importance", 0), reverse=True)


def generate_daily_digest(items):
    """digestはClaudeで処理（品質重視）"""
    top5 = items[:5]
    top5_text = "\n".join([
        "- " + item["title_ja"] + ": " + item["summary_ja"]
        for item in top5
    ])
    prompt = """今日のAIトレンドトップ5:
""" + top5_text + """

これらを踏まえて、以下を日本語で書いてください：
1. 今日の最重要トレンド（3行以内）
2. ビジネスへの示唆（2行以内）
3. 注目すべき技術動向（2行以内）

簡潔にまとめてください。"""
    # digestはClaude（高品質）
    return call_claude(prompt, max_tokens=500, label="daily_digest")


def _count_tags(items):
    from collections import Counter
    tags = []
    for item in items:
        tags.extend(item.get("tags", []))
    return dict(Counter(tags).most_common(10))


def main():
    print("Brain-v2 Summarizer starting... [" + TODAY + "]")
    print("  モード: 要約=Qwen3（激安）/ Digest=Claude（高品質）")
    if not DATA_FILE.exists():
        print("No data file found: " + str(DATA_FILE))
        return
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("raw_items", [])
    if not items:
        print("No items to summarize")
        return
    print("Summarizing " + str(len(items)) + " items...")
    summarized = summarize_items(items)
    print("Summarized " + str(len(summarized)) + " items")
    print("Generating daily digest...")
    digest = generate_daily_digest(summarized) if summarized else "本日はデータなし"
    data["summarized_items"] = summarized
    data["digest"] = digest
    data["top_tags"] = _count_tags(summarized)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Done! -> " + str(DATA_FILE))


if __name__ == "__main__":
    main()
