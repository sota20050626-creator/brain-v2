import json
import os
import urllib.request
import re
from datetime import datetime
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.manifold import TSNE, Isomap
from sklearn.preprocessing import MinMaxScaler
import warnings
warnings.filterwarnings('ignore')

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_FILE = Path("knowledge/daily/" + TODAY + ".json")
COST_FILE = Path("knowledge/cost_log.json")

# Claude API料金
SONNET_INPUT_PRICE = 3.0 / 1_000_000
SONNET_OUTPUT_PRICE = 15.0 / 1_000_000

# Qwen3料金（OpenRouter経由）
QWEN_INPUT_PRICE = 0.1 / 1_000_000
QWEN_OUTPUT_PRICE = 0.3 / 1_000_000


class ImportanceScorer:
    """重要度スコアリングの幾何学的正規化クラス"""
    
    def __init__(self, method='tsne', n_components=2):
        self.method = method
        self.n_components = n_components
        self.vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
        self.manifold_learner = None
        self.scaler = MinMaxScaler()
        
    def _initialize_manifold_learner(self):
        """多様体学習器を初期化"""
        if self.method == 'tsne':
            self.manifold_learner = TSNE(
                n_components=self.n_components,
                random_state=42,
                perplexity=min(30, max(5, len(self.texts) // 4))
            )
        elif self.method == 'isomap':
            self.manifold_learner = Isomap(
                n_components=self.n_components,
                n_neighbors=min(10, len(self.texts) - 1)
            )
    
    def calculate_importance_scores(self, papers_data):
        """論文データから重要度スコアを計算"""
        try:
            if not papers_data or len(papers_data) < 3:
                # データが少ない場合は従来の簡単なスコアリング
                return self._simple_scoring(papers_data)
            
            # テキスト抽出
            self.texts = []
            for paper in papers_data:
                text = paper.get('title', '') + ' ' + paper.get('summary', '')
                self.texts.append(text)
            
            # TF-IDFベクトル化
            tfidf_matrix = self.vectorizer.fit_transform(self.texts)
            
            # 多様体学習で低次元圧縮
            self._initialize_manifold_learner()
            embedding = self.manifold_learner.fit_transform(tfidf_matrix.toarray())
            
            # 埋め込み空間での重要度計算
            scores = self._compute_manifold_scores(embedding, tfidf_matrix)
            
            # スコアを0-1に正規化
            normalized_scores = self.scaler.fit_transform(scores.reshape(-1, 1)).flatten()
            
            # 論文データにスコアを追加
            for i, paper in enumerate(papers_data):
                paper['importance_score'] = float(normalized_scores[i])
            
            print(f"  重要度スコアリング完了: {self.method}による{self.n_components}次元圧縮")
            return papers_data
            
        except Exception as e:
            print(f"  重要度スコアリングエラー: {e}")
            return self._simple_scoring(papers_data)
    
    def _compute_manifold_scores(self, embedding, tfidf_matrix):
        """多様体埋め込み空間での重要度スコア計算"""
        scores = np.zeros(len(embedding))
        
        for i in range(len(embedding)):
            # 1. 埋め込み空間での中心からの距離
            center = np.mean(embedding, axis=0)
            distance_from_center = np.linalg.norm(embedding[i] - center)
            
            # 2. TF-IDFの最大値（キーワードの重要度）
            max_tfidf = np.max(tfidf_matrix[i].toarray())
            
            # 3. 近傍論文との類似度の分散（独自性）
            distances = [np.linalg.norm(embedding[i] - embedding[j]) 
                        for j in range(len(embedding)) if i != j]
            uniqueness = np.var(distances) if distances else 0
            
            # 重要度スコア統合
            scores[i] = (
                0.4 * distance_from_center +
                0.4 * max_tfidf * 10 +  # TF-IDFは小さい値なのでスケール調整
                0.2 * uniqueness
            )
        
        return scores
    
    def _simple_scoring(self, papers_data):
        """従来の簡単なスコアリング（フォールバック）"""
        for i, paper in enumerate(papers_data):
            # タイトル長とサマリー長による簡単なスコア
            title_len = len(paper.get('title', ''))
            summary_len = len(paper.get('summary', ''))
            paper['importance_score'] = min(1.0, (title_len + summary_len * 0.1) / 200)
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
        print("  Qwen3エラー: " + str(e))
        return None


def call_claude(prompt, max_tokens=4000, label="claude_call"):
    """Claude Sonnet 3.5を呼び出す（高精度処理用）"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY環境変数が設定されていません")
    
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
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
        
        usage = result.get("usage", {})
        save_cost(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            label, model="claude"
        )
        
        return result["content"][0]["text"]
    except Exception as e:
        print(f"  Claude APIエラー: {e}")
        return None


def enhance_papers_with_scoring(papers_data, method='tsne'):
    """論文データに重要度スコアを追加"""
    try:
        scorer = ImportanceScorer(method=method, n_components=2)
        enhanced_papers = scorer.calculate_importance_scores(papers_data)
        
        # 重要度でソート
        enhanced_papers.sort(key=lambda x: x.get('importance_score', 0), reverse=True)
        
        return enhanced_papers
    except Exception as e:
        print(f"  論文スコアリング処理失敗: {e}")
        return papers_data


# 使用例関数
def process_papers_with_importance(papers_data):
    """論文データを重要度スコアリング付きで処理"""
    print("  重要度スコアリング開始...")
    
    # 重要度スコアを計算
    enhanced_papers = enhance_papers_with_scoring(papers_data, method='tsne')
    
    # 上位論文のみ詳細処理（コスト削減）
    top_papers = enhanced_papers[:10]  # 上位10論文のみ
    
    print(f"  処理対象: {len(enhanced_papers)}論文中の上位{len(top_papers)}論文")
    
    return top_papers, enhanced_papers


if __name__ == "__main__":
    # テスト用のサンプルデータ
    sample_papers = [
        {"title": "Deep Learning for Computer Vision", "summary": "This paper presents novel approaches to computer vision using deep neural networks."},
        {"title": "Natural Language Processing with Transformers", "summary": "We explore the use of transformer architectures for various NLP tasks."},
        {"title": "Reinforcement Learning in Robotics", "summary": "Application of reinforcement learning techniques to robotic control systems."}
    ]
    
    enhanced = enhance_papers_with_scoring(sample_papers)
    for paper in enhanced:
        print(f"タイトル: {paper['title']}")
        print(f"重要度スコア: {paper.get('importance_score', 0):.3f}")
        print()
