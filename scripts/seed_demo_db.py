"""生成 demo SQLite 数据库（data/demo.db），供 query_data 工具与数据周报 Skill 使用。

用法: .venv/Scripts/python scripts/seed_demo_db.py
"""

import random
import sqlite3
from datetime import date as _date
from datetime import timedelta
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

conn.execute("drop table if exists user_events")
conn.execute(
    """create table user_events (
        id integer primary key,
        user_id text not null,
        event_date text not null,
        event_type text not null
    )"""
)
random.seed(44)
# 近 30 天用户事件：几百用户，日活整体上行 + 周末回落，新增用户每日零星进入
START = _date(2026, 9, 24)
DAYS = 30
event_rows = []
user_first: dict[str, str] = {}
uid_counter = 0


def _new_user(first_day: str) -> str:
    global uid_counter
    uid_counter += 1
    uid = f"u{uid_counter:03d}"
    user_first[uid] = first_day
    return uid


active: list[str] = [_new_user(START.isoformat()) for _ in range(180)]  # 首日存量用户
for offset in range(DAYS):
    d = (START + timedelta(days=offset)).isoformat()
    # 每日新增 3~8 个用户，且首日必留下 login 事件（保证"当日首次出现"口径可聚合）
    newcomers = [_new_user(d) for _ in range(random.randint(3, 8))]
    for uid in newcomers:
        event_rows.append((uid, d, "login"))
    weekend = 0.6 if (START + timedelta(days=offset)).weekday() >= 5 else 1.0
    trend = 0.8 + 0.4 * offset / (DAYS - 1)  # 日活从偏冷缓慢走高
    for uid in active:
        if random.random() < 0.62 * weekend * trend:
            event_rows.append((uid, d, "login"))
            for _ in range(random.randint(0, 3)):
                event_rows.append((uid, d, random.choice(["view", "action"])))
    active.extend(newcomers)
conn.executemany(
    "insert into user_events (user_id, event_date, event_type) values (?, ?, ?)",
    event_rows,
)
conn.commit()
conn.close()
print(f"seeded {DB} with sales={len(rows)} user_events={len(event_rows)} rows")
