import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_FILE = Path("knowledge/daily/" + TODAY + ".json")
COST_FILE = Path("knowledge/cost_log.json")

# Claude API料金
SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# Qwen3料金（OpenRouter経由）
QWEN_INPUT_PRICE = 0.1 / 1_000_000
QWEN_OUTPUT_PRICE = 0.3 / 1_000_000


class GeometricAutoencoder:
    """論文テキストの幾何学的正則化による重要度抽出"""
    
    def __init__(self, n_components_pca=50, n_components_tsne=2):
        self.n_components_pca = n_components_pca
        self.n_components_tsne = n_components_tsne
        self.vectorizer = TfidfVectorizer(
            max_features=1000, 
            stop_words='english',
            ngram_range=(1, 2)
        )
        self.pca = PCA(n_components=n_components_pca)
        self.tsne = TSNE(n_components=n_components_tsne, random_state=42)
        self.is_fitted = False
        
    def extract_importance_scores(self, texts):
        """テキストリストから重要度スコアを抽出"""
        try:
            if len(texts) < 3:
                # データが少ない場合は均等スコアを返す
                return [1.0] * len(texts)
            
            # TF-IDFベクトル化
            tfidf_matrix = self.vectorizer.fit_transform(texts)
            
            # PCAで次元削減（高次元→中次元）
            if tfidf_matrix.shape[1] > self.n_components_pca:
                pca_features = self.pca.fit_transform(tfidf_matrix.toarray())
            else:
                pca_features = tfidf_matrix.toarray()
            
            # 各文書の多様体上での分散を計算
            importance_scores = []
            for i, features in enumerate(pca_features):
                # 特徴ベクトルのL2ノルムを基本スコアとする
                base_score = np.linalg.norm(features)
                
                # 他の文書との距離の分散（多様性指標）
                distances = []
                for j, other_features in enumerate(pca_features):
                    if i != j:
                        dist = np.linalg.norm(features - other_features)
                        distances.append(dist)
                
                # 距離の分散が大きい = より独特 = より重要
                diversity_score = np.var(distances) if distances else 0
                
                # 統合スコア
                final_score = base_score * (1 + diversity_score * 0.1)
                importance_scores.append(float(final_score))
            
            # スコアを0-1に正規化
            if max(importance_scores) > min(importance_scores):
                min_score, max_score = min(importance_scores), max(importance_scores)
                importance_scores = [
                    (score - min_score) / (max_score - min_score) 
                    for score in importance_scores
                ]
            
            self.is_fitted = True
            return importance_scores
            
        except Exception as e:
            print(f"  幾何学的重要度抽出エラー: {e}")
            # エラー時は均等スコアを返す
            return [1.0] * len(texts)
    
    def get_top_k_indices(self, texts, k=5):
        """重要度上位k個のインデックスを返す"""
        try:
            scores = self.extract_importance_scores(texts)
            indexed_scores = [(i, score) for i, score in enumerate(scores)]
            indexed_scores.sort(key=lambda x: x[1], reverse=True)
            return [idx for idx, _ in indexed_scores[:k]]
        except Exception as e:
            print(f"  Top-k抽出エラー: {e}")
            return list(range(min(k, len(texts))))


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
        "model": "qwen/qwen3-8b",
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


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude Sonnet呼び出し（高精度処理用）"""
    # 既存のClaude呼び出し処理（省略）
    pass


def extract_important_papers(papers, top_k=10):
    """幾何学的正則化を用いて重要な論文を抽出"""
    try:
        if not papers or len(papers) == 0:
            return papers
        
        # 論文のテキスト情報を結合
        texts = []
        for paper in papers:
            text_parts = []
            if paper.get('title'):
                text_parts.append(paper['title'])
            if paper.get('abstract'):
                text_parts.append(paper['abstract'])
            if paper.get('summary'):
                text_parts.append(paper['summary'])
            
            combined_text = ' '.join(text_parts)
            texts.append(combined_text if combined_text.strip() else paper.get('title', ''))
        
        # 幾何学的重要度抽出
        autoencoder = GeometricAutoencoder()
        top_indices = autoencoder.get_top_k_indices(texts, k=min(top_k, len(papers)))
        
        # 重要論文を抽出してスコア付きで返す
        important_papers = []
        importance_scores = autoencoder.extract_importance_scores(texts)
        
        for idx in top_indices:
            paper = papers[idx].copy()
            paper['importance_score'] = round(importance_scores[idx], 4)
            important_papers.append(paper)
        
        print(f"  幾何学的正則化: {len(papers)}論文中から重要度上位{len(important_papers)}件を抽出")
        return important_papers
        
    except Exception as e:
        print(f"  重要論文抽出エラー: {e}")
        # エラー時は元の論文リストをそのまま返す
        return papers[:top_k] if len(papers) > top_k else papers


def analyze_paper_manifold(papers):
    """論文群の多様体構造を分析（オプション機能）"""
    try:
        if len(papers) < 5:
            return {"analysis": "データ不足のため分析をスキップ"}
        
        texts = [
            (paper.get('title', '') + ' ' + paper.get('abstract', '')).strip()
            for paper in papers
        ]
        
        autoencoder = GeometricAutoencoder()
        scores = autoencoder.extract_importance_scores(texts)
        
        return {
            "total_papers": len(papers),
            "avg_importance": round(np.mean(scores), 4),
            "importance_variance": round(np.var(scores), 4),
            "top_3_scores": sorted(scores, reverse=True)[:3]
        }
        
    except Exception as e:
        return {"analysis_error": str(e)}
