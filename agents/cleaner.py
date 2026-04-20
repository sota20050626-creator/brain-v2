"""
cleaner.py - 古いデータを自動削除（30日以上のraw data）
"""

from datetime import datetime, timedelta
from pathlib import Path
import json

DAILY_DIR = Path("knowledge/daily")
RETAIN_DAYS = 30

def main():
    print(f"🧹 Brain Cleaner starting...")
    
    if not DAILY_DIR.exists():
        return
    
    cutoff = datetime.now() - timedelta(days=RETAIN_DAYS)
    deleted = 0
    
    for filepath in DAILY_DIR.glob("*.json"):
        try:
            date_str = filepath.stem  # "2026-01-01"
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            if file_date < cutoff:
                # raw_itemsだけ削除してサイズを削減（summaryは保持）
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)
                
                if "raw_items" in data:
                    del data["raw_items"]
                    data["cleaned"] = True
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"  🗑️  Cleaned raw data: {filepath.name}")
                    deleted += 1
        except Exception as e:
            print(f"  ⚠️  Error processing {filepath}: {e}")
    
    print(f"✅ Cleaned {deleted} old files")

if __name__ == "__main__":
    main()
