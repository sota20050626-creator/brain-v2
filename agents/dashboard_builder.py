import json
import re
from datetime import datetime, timedelta
from pathlib import Path

DAILY_DIR = Path("knowledge/daily")
PROPOSALS_DIR = Path("knowledge/proposals")
DRAFTS_DIR = Path("knowledge/drafts")
KNOWLEDGE_FILE = Path("knowledge/knowledge_base.json")
COST_FILE = Path("knowledge/cost_log.json")
OUTPUT = Path("dashboard/index.html")
OUTPUT.parent.mkdir(exist_ok=True)

GITHUB_REPO = "sota20050626-creator/brain"


def load_recent(days=7):
    result = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        fp = DAILY_DIR / (date + ".json")
        if fp.exists():
            with open(fp, encoding="utf-8") as f:
                result.append(json.load(f))
    return result


def load_knowledge_base():
    if not KNOWLEDGE_FILE.exists():
        return {"entries": [], "days_covered": 0, "total_articles": 0}
    with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_pending_proposals():
    proposals = []
    if PROPOSALS_DIR.exists():
        for fp in sorted(PROPOSALS_DIR.glob("proposal_*.md"), reverse=True)[:3]:
            proposals.append(fp.name)
    return proposals


def load_x_drafts():
    today = datetime.now().strftime("%Y-%m-%d")
    fp = DRAFTS_DIR / ("x_" + today + ".md")
    if not fp.exists():
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        fp = DRAFTS_DIR / ("x_" + yesterday + ".md")
    if not fp.exists():
        return [], ""
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    date_str = fp.stem.replace("x_", "")

    # ★ 引用元URLも一緒にパース
    # "投稿N:\n本文\n引用元: URL" の形式を想定
    blocks = re.findall(
        r"投稿\d+:\n(.*?)(?=\n投稿\d+:|\Z)",
        content, re.DOTALL
    )
    posts = []
    for block in blocks:
        block = block.strip()
        url_match = re.search(r"引用元[:：]\s*(https?://\S+)", block)
        source_url = url_match.group(1) if url_match else ""
        # 引用元行を本文から除去
        body = re.sub(r"\n?引用元[:：]\s*https?://\S+", "", block).strip()
        posts.append({"body": body, "source_url": source_url})

    return posts, date_str


def load_note_draft():
    if not DRAFTS_DIR.exists():
        return "", ""
    files = sorted(DRAFTS_DIR.glob("note_*.md"), reverse=True)
    if not files:
        return "", ""
    fp = files[0]
    date_str = fp.stem.replace("note_", "")
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    return content[:2000], date_str


def load_cost_data():
    if not COST_FILE.exists():
        return 0, 0, 0, 0
    with open(COST_FILE, encoding="utf-8") as f:
        log = json.load(f)
    month = datetime.now().strftime("%Y-%m")
    month_data = log.get("monthly", {}).get(month, {})
    month_usd = round(month_data.get("usd", 0), 3)
    month_jpy = round(month_usd * 150)
    total_usd = round(log.get("total_usd", 0), 3)
    month_calls = len(month_data.get("calls", []))
    return month_usd, month_jpy, total_usd, month_calls


def build_html(days_data, proposals, knowledge):
    today_data = days_data[0] if days_data else {}
    today = today_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    digest = today_data.get("digest", "データなし")
    items = today_data.get("summarized_items", [])
    top_tags = today_data.get("top_tags", {})

    tag_html = ""
    for tag, count in list(top_tags.items())[:8]:
        tag_html += '<span class="tag">' + tag + ' <span class="tag-count">' + str(count) + '</span></span>'

    cards_html = ""
    for item in items[:20]:
        importance = item.get("importance", 5)
        bar_color = "#00ff88" if importance >= 8 else "#ffaa00" if importance >= 6 else "#444"
        tags_html = ""
        for t in item.get("tags", [])[:3]:
            tags_html += '<span class="item-tag">' + t + '</span>'
        url = item.get("url", "").replace("'", "")
        title = item.get("title_ja", item.get("title", ""))
        summary = item.get("summary_ja", "")
        source = item.get("source", "")
        cards_html += (
            '<div class="card" onclick="window.open(\'' + url + '\',\'_blank\')">'
            + '<div class="card-bar" style="background:' + bar_color + '"></div>'
            + '<div class="card-source">' + source + '</div>'
            + '<div class="card-title">' + title + '</div>'
            + '<div class="card-summary">' + summary + '</div>'
            + '<div class="card-tags">' + tags_html + '</div>'
            + '<div class="card-score">重要度 ' + str(importance) + '/10</div>'
            + '</div>'
        )

    history_html = ""
    for d in days_data:
        count = len(d.get("summarized_items", []))
        height = min(count * 3, 60)
        date_str = d.get("date", "")
        history_html += '<div class="hist-bar" style="height:' + str(height) + 'px" title="' + date_str + ' (' + str(count) + '件)"></div>'

    proposals_html = ""
    for p in proposals:
        proposals_html += '<div class="proposal-item">📋 ' + p + '</div>'
    if not proposals_html:
        proposals_html = '<div class="proposal-item muted">承認待ちの提案はありません</div>'

    # ★ X下書き（引用元URL付き）
    x_posts, x_date = load_x_drafts()
    x_drafts_html = ""
    if x_posts:
        for i, post in enumerate(x_posts, 1):
            body = post["body"]
            source_url = post["source_url"]
            escaped = body.replace("`", "&#96;").replace("\\", "\\\\").replace("\n", "\\n")
            source_html = ""
            if source_url:
                source_html = (
                    '<a class="draft-source-link" href="' + source_url + '" target="_blank" onclick="event.stopPropagation()">🔗 引用元を見る</a>'
                )
            x_drafts_html += (
                '<div class="draft-card">'
                + '<div class="draft-num">投稿 ' + str(i) + '</div>'
                + '<div class="draft-text">' + body.replace("\n", "<br>") + '</div>'
                + source_html
                + '<button class="copy-btn" onclick="copyText(`' + escaped + '`, this)">コピー</button>'
                + '</div>'
            )
    else:
        x_drafts_html = '<div class="muted-msg">今日のX下書きはまだ生成されていません</div>'

    # note下書き
    note_content, note_date = load_note_draft()
    if note_content:
        note_html = (
            '<div class="note-date">📅 ' + note_date + ' 生成</div>'
            + '<div class="note-preview">' + note_content.replace("\n", "<br>") + '...</div>'
            + '<a class="note-link" href="https://github.com/' + GITHUB_REPO + '/blob/main/knowledge/drafts/note_' + note_date + '.md" target="_blank">GitHubで全文を見る →</a>'
        )
    else:
        note_html = '<div class="muted-msg">note下書きはまだ生成されていません（毎週月曜に自動生成）</div>'

    # コストデータ
    month_usd, month_jpy, total_usd, month_calls = load_cost_data()

    kb_days = knowledge.get("days_covered", 0)
    kb_articles = knowledge.get("total_articles", 0)
    total_items = len(items)
    active_days = len(days_data)
    kb_entries_json = json.dumps(knowledge.get("entries", []), ensure_ascii=False)

    with open("agents/template.html", encoding="utf-8") as f:
        template = f.read()

    html = (template
        .replace("{{TODAY}}", today)
        .replace("{{DIGEST}}", digest)
        .replace("{{TAG_HTML}}", tag_html)
        .replace("{{CARDS_HTML}}", cards_html)
        .replace("{{TOTAL_ITEMS}}", str(total_items))
        .replace("{{ACTIVE_DAYS}}", str(active_days))
        .replace("{{KB_DAYS}}", str(kb_days))
        .replace("{{KB_ARTICLES}}", str(kb_articles))
        .replace("{{HISTORY_HTML}}", history_html)
        .replace("{{PROPOSALS_HTML}}", proposals_html)
        .replace("{{KB_ENTRIES_JSON}}", kb_entries_json)
        .replace("{{X_DRAFTS_HTML}}", x_drafts_html)
        .replace("{{NOTE_HTML}}", note_html)
        .replace("{{GITHUB_REPO}}", GITHUB_REPO)
        .replace("{{MONTH_USD}}", str(month_usd))
        .replace("{{MONTH_JPY}}", str(month_jpy))
        .replace("{{TOTAL_USD}}", str(total_usd))
        .replace("{{MONTH_CALLS}}", str(month_calls))
    )

    return html


def main():
    print("Brain Dashboard Builder starting...")
    days_data = load_recent(days=7)
    proposals = load_pending_proposals()
    knowledge = load_knowledge_base()
    html = build_html(days_data, proposals, knowledge)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("Dashboard updated -> " + str(OUTPUT))


if __name__ == "__main__":
    main()
