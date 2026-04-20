import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans
from sklearn.metrics.pairwise import euclidean_distances

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_FILE = Path("knowledge/daily/" + TODAY + ".json")
COST_FILE = Path("knowledge/cost_log.json")

# Claude API料金
SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# Qwen3料金（OpenRouter経由）
QWEN_INPUT_PRICE = 0.1 / 1_000_000
QWEN_OUTPUT_PRICE = 0.3 / 1_000_000


class GeometricScorer:
    """重要度スコアリングの幾何学的正則化"""
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
        self.manifold = TSNE(n_components=2, random_state=42)
        
    def calculate_importance_scores(self, articles):
        """記事リストから重要度スコアを計算"""
        try:
            if len(articles) < 2:
                return [1.0] * len(articles)  # 記事が少ない場合はすべて重要とする
            
            # テキストを抽出
            texts = []
            for article in articles:
                text = ""
                if isinstance(article, dict):
                    text = str(article.get('title', '')) + " " + str(article.get('content', ''))
                else:
                    text = str(article)
                texts.append(text)
            
            # TF-IDFベクトル化
            tfidf_matrix = self.vectorizer.fit_transform(texts)
            
            # 低次元多様体にマッピング
            if tfidf_matrix.shape[0] > 1:
                coords_2d = self.manifold.fit_transform(tfidf_matrix.toarray())
            else:
                coords_2d = np.array([[0, 0]])
            
            # 距離ベースでクラスタリング
            n_clusters = min(3, len(articles))  # 最大3クラスタ
            if n_clusters > 1:
                kmeans = KMeans(n_clusters=n_clusters, random_state=42)
                clusters = kmeans.fit_predict(coords_2d)
                cluster_centers = kmeans.cluster_centers_
            else:
                clusters = np.array([0])
                cluster_centers = coords_2d
            
            # 重要度スコア計算
            scores = []
            for i, (coord, cluster) in enumerate(zip(coords_2d, clusters)):
                # クラスタ中心からの距離（特異性）
                center_distance = np.linalg.norm(coord - cluster_centers[cluster])
                
                # 他の記事からの平均距離（独自性）
                distances = euclidean_distances([coord], coords_2d)[0]
                avg_distance = np.mean(distances)
                
                # 重要度スコア = 独自性 + 特異性の逆数（中心に近いほど代表性が高い）
                score = avg_distance + (1.0 / (1.0 + center_distance))
                scores.append(score)
            
            # 正規化（0-1の範囲）
            if len(scores) > 1:
                min_score, max_score = min(scores), max(scores)
                if max_score > min_score:
                    scores = [(s - min_score) / (max_score - min_score) for s in scores]
                else:
                    scores = [0.5] * len(scores)
            
            return scores
            
        except Exception as e:
            print(f"  幾何学的スコアリングエラー: {e}")
            # フォールバック: 均等スコア
            return [1.0] * len(articles)


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
        print("  Qwen3エラー: " + str(e) + " Claudeにフォールバック")
        return call_claude(prompt, max_tokens, label)


def call_claude(prompt, max_tokens=8000, label="claude_call"):
    """Claude 3.5 Sonnetを呼び出す（複雑な処理用）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise Exception("ANTHROPIC_API_KEY未設定")
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
            "content-type": "application/json",
            "anthropic-version": "2023-06-01"
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


def get_articles_with_scores():
    """記事を重要度スコア付きで取得"""
    if not DATA_FILE.exists():
        return []
    
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    
    articles = data.get("articles", [])
    if not articles:
        return []
    
    # 幾何学的重要度スコアリング
    try:
        scorer = GeometricScorer()
        scores = scorer.calculate_importance_scores(articles)
        print(f"  幾何学的スコアリング完了: {len(articles)}記事")
    except Exception as e:
        print(f"  幾何学的スコアリング失敗、均等スコアを使用: {e}")
        scores = [1.0] * len(articles)
    
    # スコアを記事に付加
    for article, score in zip(articles, scores):
        article['importance_score'] = score
    
    # スコア順にソート
    articles.sort(key=lambda x: x.get('importance_score', 0), reverse=True)
    
    return articles


def summarize_daily():
    """日次サマリー生成（重要度スコアリング統合版）"""
    articles = get_articles_with_scores()
    if not articles:
        print("  記事がありません")
        return
    
    # 上位の重要記事を選択（最大20記事）
    top_articles = articles[:20]
    
    # プロンプト作成
    article_texts = []
    for i, article in enumerate(top_articles):
        score = article.get('importance_score', 0)
        text = f"[記事{i+1}] (重要度: {score:.3f})\n"
        text += f"タイトル: {article.get('title', 'なし')}\n"
        text += f"内容: {article.get('content', 'なし')}\n\n"
        article_texts.append(text)
    
    prompt = f"""以下の{len(top_articles)}記事を重要度順に並べました。幾何学的解析による重要度スコアも参考に、簡潔で読みやすい日次サマリーを作成してください。

{chr(10).join(article_texts)}

要求:
1. 最も重要なトピック3-5個に絞る
2. 各トピック200文字以内
3. 重要度の高い記事を優先的に含める
4. 読みやすい日本語で"""
    
    summary = call_claude(prompt, 4000, "daily_summary")
    
    print("\n=== 日次サマリー（重要度スコアリング版） ===")
    print(summary)
    print(f"\n処理記事数: {len(articles)} (上位{len(top_articles)}記事を分析)")
    
    return summary


if __name__ == "__main__":
    summarize_daily()
