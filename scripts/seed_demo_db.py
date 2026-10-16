"""生成 demo SQLite 数据库（data/demo.db），供 query_data 工具与数据周报 Skill 使用。

用法: .venv/Scripts/python scripts/seed_demo_db.py
"""

import random
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "demo.db"

CITIES = ["北京", "上海", "广州", "深圳", "杭州"]
CATEGORIES = ["数码", "服饰", "食品", "家居"]

conn = sqlite3.connect(DB)
conn.execute("drop table if exists sales")
conn.execute(
    """create table sales (
        id integer primary key,
        sale_date text not null,
        city text not null,
        category text not null,
        amount real not null
    )"""
)
rows = []
random.seed(42)
# 21 天 × 每天每城市每品类若干笔，量的分布让"近一周 vs 上一周"有可见差异
for day in range(1, 22):
    date = f"2026-10-{day:02d}"
    bump = 1.6 if day >= 15 else 1.0  # 第 3 周整体上量，周报能看出环比
    for city in CITIES:
        for cat in CATEGORIES:
            for _ in range(random.randint(1, 4)):
                rows.append((date, city, cat, round(random.uniform(20, 800) * bump, 2)))
conn.executemany("insert into sales (sale_date, city, category, amount) values (?, ?, ?, ?)", rows)
conn.commit()
conn.close()
print(f"seeded {DB} with {len(rows)} rows")
