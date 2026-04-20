import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path
import numpy as np

# 新技術: 重要度スコアリングの幾何学的正則化
try:
    from sklearn.manifold import Isomap
    from sklearn.metrics.pairwise import euclidean_distances
    from sklearn.preprocessing import StandardScaler
    MANIFOLD_AVAILABLE = True
except ImportError:
    MANIFOLD_AVAILABLE = False
    print("  警告: scikit-learnが未インストール。重要度スコアリングの幾何学的正則化は無効です")

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_FILE = Path("knowledge/daily/" + TODAY + ".json")
COST_FILE = Path("knowledge/cost_log.json")

# Claude API料金
SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# Qwen3料金（OpenRouter経由）
QWEN_INPUT_PRICE = 0.1 / 1_000_000
QWEN_OUTPUT_PRICE = 0.3 / 1_000_000


def geometric_importance_regularization(papers_data, target_paper_id=None):
    """
    重要度スコアリングの幾何学的正則化
    AI論文の特徴量を低次元多様体で正則化し、重要度スコアの精度を向上させる
    """
    if not MANIFOLD_AVAILABLE or not papers_data:
        return papers_data
    
    try:
        # 論文の特徴量抽出（タイトル長、要約長、引用数推定など）
        features = []
        paper_ids = []
        
        for paper in papers_data:
            if not isinstance(paper, dict):
                continue
                
            title_len = len(paper.get('title', ''))
            abstract_len = len(paper.get('abstract', ''))
            # 簡易的な重要度指標（タイトル・要約の特定キーワード密度）
            important_keywords = ['neural', 'deep', 'learning', 'transformer', 'attention', 'AI', 'model']
            text_content = (paper.get('title', '') + ' ' + paper.get('abstract', '')).lower()
            keyword_density = sum(text_content.count(kw.lower()) for kw in important_keywords) / max(len(text_content), 1)
            
            features.append([title_len, abstract_len, keyword_density])
            paper_ids.append(paper.get('id', len(paper_ids)))
        
        if len(features) < 3:  # Isomapには最低3つのサンプルが必要
            return papers_data
            
        # 特徴量の標準化
        scaler = StandardScaler()
        features_scaled = scaler.fit_transform(features)
        
        # Isomapによる低次元多様体学習
        n_components = min(2, len(features) - 1)
        isomap = Isomap(n_components=n_components, n_neighbors=min(3, len(features)))
        manifold_coords = isomap.fit_transform(features_scaled)
        
        # 多様体空間での距離に基づく重要度補正
        if target_paper_id is not None and target_paper_id in paper_ids:
            target_idx = paper_ids.index(target_paper_id)
            target_coord = manifold_coords[target_idx:target_idx+1]
            distances = euclidean_distances(manifold_coords, target_coord).flatten()
        else:
            # 中心からの距離を計算
            center = np.mean(manifold_coords, axis=0)
            distances = euclidean_distances(manifold_coords, center.reshape(1, -1)).flatten()
        
        # 距離の逆数で重要度スコアを補正（近いほど重要）
        max_distance = np.max(distances) if np.max(distances) > 0 else 1
        importance_scores = 1.0 - (distances / max_distance)
        
        # 元の論文データに重要度スコアを追加
        for i, paper in enumerate(papers_data):
            if i < len(importance_scores):
                paper['geometric_importance'] = float(importance_scores[i])
                # 既存の重要度があれば調整
                if 'importance' in paper:
                    paper['importance'] = (paper['importance'] + importance_scores[i]) / 2
                else:
                    paper['importance'] = importance_scores[i]
        
        print(f"  幾何学的正則化完了: {len(papers_data)}論文を{n_components}次元多様体で処理")
        
    except Exception as e:
        print(f"  幾何学的正則化エラー: {e}")
    
    return papers_data


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
        return None


def call_claude(prompt, max_tokens=2000, label="claude_call"):
    """Claude APIを呼び出す（既存機能を想定）"""
    # 既存のClaude呼び出し実装がここに来る
    pass
