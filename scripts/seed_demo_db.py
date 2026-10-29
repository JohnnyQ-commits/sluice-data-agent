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
# 21 天窗口锚定系统日期（末日=昨天）："上周"（自然周）恒有完整数据，demo 无保质期（备案②）。
# 量的分布让"近一周 vs 上一周"有可见差异：异常周（自然周）整体上量，周报能看出环比。
# 渠道异常（Skill B 异常下钻的教学数据）：上周整体涨，但深圳暴跌——整体指标掩盖
# 单一渠道崩盘，逐维度下钻才能定位。random 调用序列保持不变（倍率乘在抽样之后），
# 行数与既有播种一致。
END = _date.today() - timedelta(days=1)
# 异常窗口与评测口径对齐（自然周，周一~周日）：周一=今天所在周周一再往前推 7 天。
# 教训：异常埋在"滚动近 7 天"上会与自然周锚定的评测错位（模型按滚动窗口下钻
# 数字全对、确定性通道按自然周对账全不中）。
_today = _date.today()
_last_sunday = _today - timedelta(days=_today.weekday() + 1)
_last_monday = _last_sunday - timedelta(days=6)
for offset in range(21):
    date = (END - timedelta(days=20 - offset)).isoformat()
    d = END - timedelta(days=20 - offset)
    in_anomaly_week = _last_monday <= d <= _last_sunday
    for city in CITIES:
        # 异常周整体上量，但深圳反常暴跌
        city_bump = (0.15 if city == "深圳" else 1.9) if in_anomaly_week else 1.0
        for cat in CATEGORIES:
            for _ in range(random.randint(1, 4)):
                rows.append((date, city, cat, round(random.uniform(20, 800) * city_bump, 2)))
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
# 同样锚定系统日期（末日=昨天），与 sales 同一播种窗口语义
START = _date.today() - timedelta(days=30)
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
