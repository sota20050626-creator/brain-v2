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
        all_items.extend(data.get("summarized_items", []))
        if data.get("digest"):
            all_digests.append("[" + date + "] " + data["digest"])
        for tag, count in data.get("top_tags", {}).items():
            all_tags[tag] = all_tags.get(tag, 0) + count
    return all_items, all_digests, all_tags


def load_latest_items():
    results = []
    for date in [TODAY, YESTERDAY]:
        filepath = KNOWLEDGE_DIR / "daily" / (date + ".json")
        if not filepath.exists():
            continue
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("summarized_items", [])
        results.extend(items)
        print("  " + date + " のデータ " + str(len(items)) + " 件を読み込みました")
    return results


def generate_x_drafts(items):
    top_items = sorted(items, key=lambda x: x.get("importance", 0), reverse=True)[:10]
    # ★ URLを含めてClaudeに渡す
    items_text = "\n".join([
        "- " + item.get("title_ja", item.get("title", "")) + ": " + item.get("summary_ja", "")[:80]
        + " [URL: " + item.get("url", "") + "]"
        for item in top_items
    ])
    prompt = """あなたはAI情報を発信するXアカウントの中の人です。
今日・昨日のAIニュースの中から最もホットなものを選んで、X投稿文を3本作成してください。

【最新AIニュース（今日+昨日）】
""" + items_text + """

【選び方のルール】
- 最もインパクトが大きいBIGNEWSを優先する
- 「え、マジで？」「知らなかった」と思わせるものを選ぶ
- 業界の転換点になりそうなものを優先する
- 3本は全て異なるトピックにする

【投稿文のルール】
- 各投稿は140文字以内
- 専門用語を使いすぎず、一般人にも刺さる表現
- 体言止め・断言系で書く
- 「速報」「今」「たった今」などの鮮度を感じさせる言葉を入れる
- 末尾に関連ハッシュタグを2〜3個

【出力形式】
投稿1:
（本文）
引用元: （元記事のURL）

投稿2:
（本文）
引用元: （元記事のURL）

投稿3:
（本文）
引用元: （元記事のURL）"""
    return call_claude(prompt, max_tokens=900, label="x_drafts")


def save_x_drafts(drafts_text):
    path = DRAFTS_DIR / ("x_" + TODAY + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# X投稿下書き - " + TODAY + "\n\n")
        f.write("> 今日+昨日(" + YESTERDAY + ")の最新AIニュースをもとに生成\n")
        f.write("> 確認して気に入ったものをそのままXに投稿してください\n")
        f.write("> 引用元URLはリプライまたは投稿末尾に添付推奨\n\n")
        f.write(drafts_text)
    print("  X下書き保存完了: " + str(path))
    return path


def analyze_trends(items, tags):
    top_items = sorted(items, key=lambda x: x.get("importance", 0), reverse=True)[:20]
    items_text = "\n".join([
        "- [" + str(item.get("importance", 5)) + "/10] " + item.get("title_ja", item.get("title", ""))
        for item in top_items
    ])
    tags_text = ", ".join([
        k + "(" + str(v) + "回)" for k, v in
        sorted(tags.items(), key=lambda x: x[1], reverse=True)[:10]
    ])
    prompt = """あなたはAI技術のストラテジストです。
過去30日間のAIトレンドデータを分析してください。

【重要度上位の記事】
""" + items_text + """

【頻出タグ】
""" + tags_text + """

以下を分析してください：
1. 主要トレンド3つ（それぞれ2〜3文）
2. 次の1ヶ月で注目すべき技術・動向
3. 見落とされているが重要なシグナル

日本語で、具体的かつ鋭い分析をしてください。"""
    return call_claude(prompt, max_tokens=1000, label="analyze_trends")


def generate_business_ideas(trend_analysis):
    prompt = """以下のAIトレンド分析を基に、具体的なビジネスアイデアを3つ提案してください。

【トレンド分析】
""" + trend_analysis + """

各アイデアについて：
- アイデア名
- 一言説明
- ターゲット顧客
- 収益モデル（具体的な金額感も含む）
- 最初の1ヶ月でできる最小限の実装
- リスクと対策

実現可能性が高く、$30/月以下のコストで始められるものを優先してください。
日本語で具体的に書いてください。"""
    return call_claude(prompt, max_tokens=1500, label="business_ideas")


def generate_note_draft(trend_analysis, items):
    top_items = sorted(items, key=lambda x: x.get("importance", 0), reverse=True)[:7]
    items_text = "\n".join([
        "- " + item.get("title_ja", "") + ": " + item.get("summary_ja", "")[:120]
        for item in top_items
    ])
    prompt = """あなたはフォロワー数万人のAI情報発信者です。
今週のAIトレンドをもとに、noteで売れる有料記事の下書きを作成してください。

【今週のトレンド分析】
""" + trend_analysis + """

【注目ニュース TOP7】
""" + items_text + """

【記事の要件】
- 読者層：AI初心者〜中級者、副業・ビジネスに興味がある20〜40代
- 価格設定：980円
- 文字数：全体で3000〜4000字
- 無料部分と有料部分を明確に分ける

【構成】

タイトル（クリックしたくなる、数字・感情・具体性を含むタイトル）

はじめに（無料・200字）

今週起きた3大ニュース（無料・各150字）

ここから有料（980円）

なぜこれが重要なのか（有料・400字）

あなたのビジネス・仕事への影響（有料・400字）

来週の注目ポイント（有料・300字）

編集後記（有料・200字）

【文体ルール】
- です・ます調だが堅すぎない
- 専門用語は必ず一言で説明を添える
- 数字・固有名詞を積極的に使う
- 日本語で書く"""
    return call_claude(prompt, max_tokens=3000, label="note_draft")


def save_note_draft(note_text):
    path = DRAFTS_DIR / ("note_" + TODAY + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# note記事下書き - " + TODAY + "\n\n")
        f.write("> 無料部分はそのまま公開、有料部分は980円で設定推奨\n")
        f.write("> 固有名詞・数字を確認して投稿してください\n\n")
        f.write("---\n\n")
        f.write(note_text)
    print("  note下書き保存完了: " + str(path))
    return path


def generate_agent_improvements():
    agent_files = {}
    for agent in ["collector.py", "summarizer.py", "growth.py"]:
        path = Path("agents") / agent
        if path.exists():
            with open(path, encoding="utf-8") as f:
                agent_files[agent] = f.read()[:1000]
    agents_text = "\n\n".join(["=== " + k + " ===\n" + v for k, v in agent_files.items()])
    prompt = """あなたは自律型AIシステムのアーキテクトです。
以下のエージェントシステムの改善提案をしてください。

【現在のエージェント概要】
""" + agents_text + """

改善提案（優先度順に3つ）：
1. 新しいデータソースの追加
2. 処理精度・効率の改善
3. 新機能の追加

各提案について「実装コスト」「期待効果」「リスク」を明記してください。
これらは提案です。実装はオーナーの承認後に行います。"""
    return call_claude(prompt, max_tokens=1000, label="agent_improvements")


def fetch_latest_ai_papers():
    query = urllib.parse.quote("cat:cs.AI OR cat:cs.LG OR cat:cs.CL")
    url = (
        "https://export.arxiv.org/api/query?search_query=" + query
        + "&sortBy=submittedDate&sortOrder=descending&max_results=20"
    )
    try:
        with urllib.request.urlopen(url) as r:
            content = r.read().decode()
        entries = re.findall(r"<entry>(.*?)</entry>", content, re.DOTALL)
        papers = []
        for entry in entries:
            title = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            summary = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            link = re.search(r"<id>(.*?)</id>", entry)
            if title and summary:
                papers.append({
                    "title": title.group(1).strip().replace("\n", " "),
                    "url": link.group(1).strip() if link else "",
                    "summary": summary.group(1).strip()[:300].replace("\n", " "),
                })
        return papers
    except Exception as e:
        print("  ArXiv取得エラー: " + str(e))
        return []


def discover_applicable_technologies(papers, current_code_summary):
    papers_text = "\n".join([
        "- " + p["title"] + ": " + p["summary"][:150]
        for p in papers[:10]
    ])
    prompt = """あなたはAIシステムのアーキテクトです。
以下の最新AI論文の中から、このシステムに搭載できる技術を見つけてください。

【最新AI論文】
""" + papers_text + """

【現在のシステム概要】
""" + current_code_summary + """

【評価基準】
- Pythonで実装可能か
- 外部APIなしで動作するか
- 既存機能を壊さないか
- 実装工数が小さいか（100行以内）

搭載可能な技術を最大3つ、以下のJSON形式で返してください：
[
  {
    "title": "技術名",
    "paper_url": "論文URL",
    "description": "何をするか（1文）",
    "target_file": "collector.py or summarizer.py or growth.py",
    "benefit": "搭載するとどう良くなるか",
    "risk": "リスク",
    "implementation_hint": "実装のヒント（具体的に）"
  }
]

JSONのみを返してください。"""
    response = call_claude(prompt, max_tokens=2000, label="discover_tech")
    try:
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if not match:
            return []
        return json.loads(match.group())
    except Exception as e:
        print("  技術発見パースエラー: " + str(e))
        return []


def generate_tech_integration_code(tech, current_code, filename):
    prompt = """あなたは優秀なPythonエンジニアです。
以下の技術を既存のコードに統合してください。

【統合する技術】
名前: """ + tech["title"] + """
説明: """ + tech["description"] + """
実装のヒント: """ + tech["implementation_hint"] + """

【現在の """ + filename + """ コード】
""" + current_code[:3000] + """

【ルール】
- 既存の機能は必ず維持する
- 新技術の追加は最小限にとどめる
- 失敗しても既存機能が動くようにtry/exceptを使う
- 必ず ```python から始まり ``` で終わる形式で返す
- 必ず動作するコードを返す"""
    return call_claude(prompt, max_tokens=4000, label="tech_integration_" + filename)


def get_file_content(repo, filepath, token):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/contents/" + filepath,
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github.v3+json",
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
            content = base64.b64decode(data["content"]).decode("utf-8")
            sha = data["sha"]
            return content, sha
    except Exception as e:
        print("  ファイル取得エラー: " + str(e))
        return None, None


def generate_improved_code(current_code, filename, trend_analysis):
    if filename == "collector.py":
        direction = "新しいAI情報ソースの追加、収集精度の向上、エラーハンドリングの強化"
    elif filename == "summarizer.py":
        direction = "要約の精度向上、重要度スコアリングの改善、エラーハンドリングの強化"
    else:
        direction = "投稿文の品質向上、提案精度の改善、エラーハンドリングの強化"

    prompt = """あなたは優秀なPythonエンジニアです。
以下の """ + filename + """ を改善してください。

【現在のコード】
""" + current_code[:3000] + """

【改善の方針】
""" + direction + """

【ルール】
- 既存の機能は必ず維持する
- 変更は最小限にとどめる
- 必ず ```python から始まり ``` で終わる形式で返す
- 必ず動作するコードを返す"""
    return call_claude(prompt, max_tokens=4000, label="improve_" + filename)


def create_branch(repo, branch_name, token):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/git/refs/heads/main",
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github.v3+json",
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
            sha = data["object"]["sha"]
    except Exception as e:
        print("  ブランチSHA取得エラー: " + str(e))
        return False

    payload = json.dumps({
        "ref": "refs/heads/" + branch_name,
        "sha": sha
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/git/refs",
        data=payload,
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            print("  ブランチ作成完了: " + branch_name)
            return True
    except Exception as e:
        print("  ブランチ作成エラー: " + str(e))
        return False


def commit_file(repo, filepath, content, sha, branch_name, commit_message, token):
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = json.dumps({
        "message": commit_message,
        "content": encoded,
        "sha": sha,
        "branch": branch_name,
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/contents/" + filepath,
        data=payload,
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }
    )
    req.get_method = lambda: "PUT"
    try:
        with urllib.request.urlopen(req) as r:
            print("  コミット完了: " + filepath)
            return True
    except Exception as e:
        print("  コミットエラー: " + str(e))
        return False


def create_pull_request(repo, branch_name, title, body, token):
    payload = json.dumps({
        "title": title,
        "body": body,
        "head": branch_name,
        "base": "main",
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/pulls",
        data=payload,
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
            print("  PR作成完了: " + result["html_url"])
            return result["html_url"]
    except Exception as e:
        print("  PR作成エラー: " + str(e))
        return None


def auto_improve_and_pr(trend_analysis):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("  GITHUB_TOKEN または GITHUB_REPOSITORY が未設定のためスキップ")
        return

    targets = [
        ("agents/collector.py", "collector.py"),
        ("agents/summarizer.py", "summarizer.py"),
        ("agents/growth.py", "growth.py"),
    ]

    branch_name = "brain-auto-improve-" + datetime.now().strftime("%Y-%m-%d-%H%M")
    if not create_branch(repo, branch_name, token):
        return

    improved_files = []
    for filepath, filename in targets:
        print("  " + filename + " を改善中...")
        current_code, sha = get_file_content(repo, filepath, token)
        if not current_code:
            continue
        improved_response = generate_improved_code(current_code, filename, trend_analysis)
        match = re.search(r"```python\n(.*?)```", improved_response, re.DOTALL)
        if not match:
            print("  " + filename + " のコードブロックが見つかりませんでした、スキップ")
            continue
        improved_code = match.group(1)
        commit_msg = "Brain 自動改善: " + filename + " [" + TODAY + "]"
        if commit_file(repo, filepath, improved_code, sha, branch_name, commit_msg, token):
            improved_files.append(filename)

    if not improved_files:
        print("  改善されたファイルがないためPRをスキップ")
        return

    pr_body = "## Brain 自動改善PR - " + TODAY + "\n\n"
    pr_body += "### 改善されたファイル\n"
    for f in improved_files:
        pr_body += "- " + f + "\n"
    pr_body += "\n### 改善の根拠\n"
    pr_body += trend_analysis[:300] + "\n\n"
    pr_body += "### 確認方法\n"
    pr_body += "1. Files changed タブで差分を確認\n"
    pr_body += "2. 問題なければ Merge pull request を押すだけ\n"
    pr_body += "3. 問題があれば Close pull request で却下\n\n"
    pr_body += "> このPRはBrain Growth Agentが自動生成しました\n"

    create_pull_request(
        repo, branch_name,
        "Brain 自動改善: " + ", ".join(improved_files) + " [" + TODAY + "]",
        pr_body, token
    )


def auto_integrate_new_tech(trend_analysis):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("  GITHUB_TOKEN または GITHUB_REPOSITORY が未設定のためスキップ")
        return

    print("  最新AI論文を取得中...")
    papers = fetch_latest_ai_papers()
    if not papers:
        print("  論文取得失敗、スキップ")
        return

    current_code_summary = """
- collector.py: HackerNews/ArXiv/GitHubからAI情報を収集
- summarizer.py: Claude APIで日本語要約・重要度スコアリング
- growth.py: X投稿文・note下書き・自動PR生成
"""

    print("  搭載可能な新技術を分析中...")
    technologies = discover_applicable_technologies(papers, current_code_summary)
    if not technologies:
        print("  搭載可能な技術が見つかりませんでした")
        return

    print("  " + str(len(technologies)) + " 件の搭載可能技術を発見")

    branch_name = "brain-new-tech-" + datetime.now().strftime("%Y-%m-%d-%H%M")
    if not create_branch(repo, branch_name, token):
        return

    integrated = []
    for tech in technologies[:2]:
        filename = tech.get("target_file", "")
        if filename not in ["collector.py", "summarizer.py", "growth.py"]:
            continue
        filepath = "agents/" + filename
        print("  " + filename + " に " + tech["title"] + " を統合中...")
        current_code, sha = get_file_content(repo, filepath, token)
        if not current_code:
            continue
        improved_response = generate_tech_integration_code(tech, current_code, filename)
        match = re.search(r"```python\n(.*?)```", improved_response, re.DOTALL)
        if not match:
            print("  コードブロックが見つかりませんでした、スキップ")
            continue
        improved_code = match.group(1)
        commit_msg = "Brain 新技術搭載: " + tech["title"] + " -> " + filename + " [" + TODAY + "]"
        if commit_file(repo, filepath, improved_code, sha, branch_name, commit_msg, token):
            integrated.append(tech)

    if not integrated:
        print("  統合できた技術がないためPRをスキップ")
        return

    pr_body = "## Brain 新技術自動搭載PR - " + TODAY + "\n\n"
    pr_body += "### 搭載された技術\n"
    for tech in integrated:
        pr_body += "#### " + tech["title"] + "\n"
        pr_body += "- 論文: " + tech.get("paper_url", "不明") + "\n"
        pr_body += "- 効果: " + tech.get("benefit", "") + "\n"
        pr_body += "- リスク: " + tech.get("risk", "") + "\n"
        pr_body += "- 対象: " + tech.get("target_file", "") + "\n\n"
    pr_body += "### 確認方法\n"
    pr_body += "1. Files changed タブで差分を確認\n"
    pr_body += "2. 問題なければ Merge pull request を押すだけ\n"
    pr_body += "3. 問題があれば Close pull request で却下\n\n"
    pr_body += "> このPRはBrain Growth Agentが最新論文を解析して自動生成しました\n"

    titles = [t["title"] for t in integrated]
    create_pull_request(
        repo, branch_name,
        "Brain 新技術搭載: " + ", ".join(titles) + " [" + TODAY + "]",
        pr_body, token
    )


def create_github_issue(title, body):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("  GITHUB_TOKEN または GITHUB_REPOSITORY が未設定のためスキップ")
        return
    payload = json.dumps({
        "title": title,
        "body": body,
        "labels": ["brain-proposal", "pending-approval"]
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/issues",
        data=payload,
        headers={
            "Authorization": "token " + token,
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
            print("  Issue作成完了: #" + str(result["number"]) + " " + result["html_url"])
    except Exception as e:
        print("  Issue作成エラー: " + str(e))


def create_approval_request(trend_analysis, business_ideas, agent_improvements, note_path):
    proposal = {
        "date": TODAY,
        "status": "pending_approval",
        "trend_analysis": trend_analysis,
        "business_ideas": business_ideas,
        "agent_improvements": agent_improvements,
        "note_draft_path": str(note_path),
        "approval_notes": "",
    }
    filepath = PROPOSALS_DIR / ("proposal_" + TODAY + ".json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(proposal, f, ensure_ascii=False, indent=2)

    md_path = PROPOSALS_DIR / ("proposal_" + TODAY + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Brain 週次レポート - " + TODAY + "\n\n")
        f.write("ステータス: 承認待ち\n\n---\n\n")
        f.write("## トレンド分析\n\n" + trend_analysis + "\n\n---\n\n")
        f.write("## ビジネスアイデア\n\n" + business_ideas + "\n\n---\n\n")
        f.write("## エージェント改善提案\n\n" + agent_improvements + "\n\n---\n\n")
        f.write("## note下書き\n\n" + str(note_path) + " を確認してください\n\n---\n\n")
        f.write("## 承認方法\n\nPRをMergeするだけ\n")

    issue_body = "## Brain 週次レポート - " + TODAY + "\n\n"
    issue_body += "### トレンド分析\n" + trend_analysis[:500] + "...\n\n"
    issue_body += "### ビジネスアイデア\n" + business_ideas[:500] + "...\n\n"
    issue_body += "### エージェント改善提案\n" + agent_improvements[:300] + "...\n\n"
    issue_body += "---\n"
    issue_body += "詳細: knowledge/proposals/proposal_" + TODAY + ".md\n"
    issue_body += "note下書き: knowledge/drafts/note_" + TODAY + ".md\n"
    issue_body += "X下書き: knowledge/drafts/x_" + TODAY + ".md\n\n"
    issue_body += "### 承認方法\n"
    issue_body += "- 承認: PRをMerge\n"
    issue_body += "- 却下: PRをClose\n"

    create_github_issue("Brain 週次レポート " + TODAY, issue_body)
    return filepath, md_path


def main():
    print("Brain Growth Agent 起動... [" + TODAY + "] (weekday=" + str(WEEKDAY) + ")")

    items, digests, tags = load_recent_data(days=30)
    print("  過去30日分 " + str(len(items)) + " 件のデータを読み込みました")

    latest_items = load_latest_items()
    if len(latest_items) >= 3:
        print("  今日+昨日のデータでX下書きを生成中...")
        x_drafts = generate_x_drafts(latest_items)
        save_x_drafts(x_drafts)
    elif len(items) >= 3:
        print("  最新データなし、過去30日のデータでX下書きを生成中...")
        x_drafts = generate_x_drafts(items)
        save_x_drafts(x_drafts)
    else:
        print("  データが不足しているためX下書きをスキップ")

    if WEEKDAY == 0:
        if len(items) < 5:
            print("  データが不足しているため週次分析をスキップ")
            return

        print("  トレンド分析中...")
        trend_analysis = analyze_trends(items, tags)

        print("  ビジネスアイデア生成中...")
        business_ideas = generate_business_ideas(trend_analysis)

        print("  note下書き生成中...")
        note_draft_text = generate_note_draft(trend_analysis, items)
        note_path = save_note_draft(note_draft_text)

        print("  エージェント改善提案生成中...")
        agent_improvements = generate_agent_improvements()

        print("  承認リクエスト + Issue作成中...")
        create_approval_request(trend_analysis, business_ideas, agent_improvements, note_path)

        print("  自動改善PR作成中...")
        auto_improve_and_pr(trend_analysis)

        print("  新技術自動搭載PR作成中...")
        auto_integrate_new_tech(trend_analysis)

        print("週次分析完了!")
    else:
        print("週次分析は月曜のみ実行 (今日は weekday=" + str(WEEKDAY) + ")")
        print("X下書き生成完了!")


if __name__ == "__main__":
    main()
