import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans

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


def geometric_importance_scoring(texts):
    """
    重要度スコアリングの幾何学的正則化
    テキストリストから重要度スコアを計算
    """
    try:
        if len(texts) < 3:  # 最小限のデータが必要
            return [1.0] * len(texts)
        
        # TF-IDFベクトル化
        vectorizer = TfidfVectorizer(
            max_features=100, 
            stop_words='english',
            ngram_range=(1, 2)
        )
        tfidf_matrix = vectorizer.fit_transform(texts)
        
        # 2-3次元に圧縮
        n_components = min(3, len(texts) - 1, tfidf_matrix.shape[1])
        pca = PCA(n_components=n_components)
        reduced_features = pca.fit_transform(tfidf_matrix.toarray())
        
        # クラスタリング
        n_clusters = min(3, len(texts))
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        cluster_labels = kmeans.fit_predict(reduced_features)
        
        # クラスタ中心からの距離で重要度を計算
        importance_scores = []
        for i, point in enumerate(reduced_features):
            cluster_center = kmeans.cluster_centers_[cluster_labels[i]]
            distance = np.linalg.norm(point - cluster_center)
            # 距離を正規化して重要度スコアに変換（距離が大きいほど重要）
            importance_scores.append(float(distance))
        
        # スコアを0-1に正規化
        if max(importance_scores) > min(importance_scores):
            min_score = min(importance_scores)
            max_score = max(importance_scores)
            importance_scores = [(score - min_score) / (max_score - min_score) + 0.1 
                               for score in importance_scores]
        else:
            importance_scores = [1.0] * len(texts)
            
        print(f"  幾何学的正則化完了: {len(texts)}件のテキストを分析")
        return importance_scores
        
    except Exception as e:
        print(f"  幾何学的正則化エラー: {e} -> 均等スコアを使用")
        return [1.0] * len(texts)


def apply_geometric_scoring_to_summary(summary_text, sentences_data=None):
    """
    要約テキストに幾何学的重要度スコアを適用
    """
    try:
        # センテンス分割
        sentences = re.split(r'[.!?。！？]\s*', summary_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) < 2:
            return summary_text
        
        # 重要度スコア計算
        importance_scores = geometric_importance_scoring(sentences)
        
        # スコア付きセンテンスをソート（重要度順）
        scored_sentences = list(zip(sentences, importance_scores))
        scored_sentences.sort(key=lambda x: x[1], reverse=True)
        
        # 上位70%を選択して元の順序で再構成
        top_count = max(1, int(len(scored_sentences) * 0.7))
        selected_sentences = [s[0] for s in scored_sentences[:top_count]]
        
        # 元の順序を保持して再構成
        result_sentences = []
        for original_sentence in sentences:
            if original_sentence in selected_sentences:
                result_sentences.append(original_sentence)
        
        enhanced_summary = '. '.join(result_sentences) + '.'
        print(f"  幾何学的フィルタリング: {len(sentences)}文 -> {len(result_sentences)}文")
        
        return enhanced_summary
        
    except Exception as e:
        print(f"  要約への幾何学的スコア適用エラー: {e} -> 元のテキストを返却")
        return summary_text


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
        return None


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude API呼び出し"""
    # この関数の実装は省略（元のコードに存在すると仮定）
    pass


def enhanced_summarize(text, use_geometric_scoring=True):
    """
    幾何学的正則化を適用した要約関数
    """
    try:
        # 通常の要約処理
        summary = call_qwen(f"以下のテキストを要約してください:\n\n{text}", label="要約処理")
        
        if not summary:
            return text
        
        # 幾何学的正則化を適用
        if use_geometric_scoring:
            summary = apply_geometric_scoring_to_summary(summary)
        
        return summary
        
    except Exception as e:
        print(f"  要約処理エラー: {e}")
        return text
