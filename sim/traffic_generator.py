#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sim/traffic_generator.py
============================================================
合成流量生成器（纯 NumPy 离线方案）
------------------------------------------------------------
用途：
    在没有真实基站、也没有第三方节能仿真包可用的情况下，
    用 NumPy 生成 24 小时（15 分钟粒度）的多扇区小区流量时序，
    作为 Agent 决策与基线对比的唯一数据源。

设计要点：
    1. 完全可复现：固定随机种子，任何人跑出的数据完全一致；
    2. 只用合成数据：不对接任何运营商现网、不读取任何真实 KPI；
    3. 输出字段严格为：时间戳 / 扇区ID / 负载百分比 / 在线用户数 / 是否可休眠。

流量曲线建模：
    用「夜间底噪 + 若干高斯活动峰」叠加来刻画一天的人群活动，
    不同扇区挂不同的场景模板（住宅/办公/商业/园区/工业），
    从而形成真实网络中「同一小区内各扇区忙闲不均」的特征。

运行方式：
    python sim/traffic_generator.py
    python sim/traffic_generator.py --days 7 --out sim/result/sector_traffic_7d.csv
"""

import argparse
import csv
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# 场景常量（全部为仿真假设值，不代表任何真实设备参数）
# ---------------------------------------------------------------------------

# 时间步长：15 分钟
STEP_MINUTES = 15
STEPS_PER_DAY = 24 * 60 // STEP_MINUTES  # 96

# 每个扇区的用户容量（同时在线用户数上限），用于把用户数折算成负载百分比
SECTOR_USER_CAPACITY = 100

# 夜间底噪用户数：凌晨最低谷时每个扇区仍有的常驻连接数
NIGHT_BASE_USERS = 2.5

# 允许休眠的判定阈值：负载低于该百分比且用户数很少时，才标记为可休眠候选
SLEEPABLE_LOAD_PCT = 20.0
SLEEPABLE_MAX_USERS = 8

# 数据起始时间（固定，保证可复现）
START_TIME = datetime(2026, 1, 15, 0, 0, 0)

# 扇区场景模板：每个模板由若干「活动峰」组成
# 峰的结构：(中心时刻(小时), 宽度 sigma(小时), 幅度(用户数))
SCENARIO_PEAKS = {
    "residential": [(8.0, 1.0, 10.0), (19.5, 2.2, 44.0), (22.5, 1.2, 18.0)],
    "office": [(10.5, 1.6, 38.0), (15.0, 2.0, 32.0), (20.0, 1.2, 12.0)],
    "commercial": [(13.0, 2.0, 26.0), (20.5, 2.2, 46.0)],
    "campus": [(10.0, 1.5, 30.0), (15.5, 1.8, 28.0), (21.0, 1.0, 10.0)],
    "industrial": [(9.5, 1.8, 22.0), (14.5, 2.5, 20.0)],
}

# 单小区 6 扇区，各自绑定一种场景模板
SECTORS = [
    {"sector_id": "CELL-A-SEC-01", "scenario": "residential"},
    {"sector_id": "CELL-A-SEC-02", "scenario": "office"},
    {"sector_id": "CELL-A-SEC-03", "scenario": "residential"},
    {"sector_id": "CELL-A-SEC-04", "scenario": "commercial"},
    {"sector_id": "CELL-A-SEC-05", "scenario": "campus"},
    {"sector_id": "CELL-A-SEC-06", "scenario": "industrial"},
]


def activity_users(hour: float, scenario: str) -> float:
    """
    计算某一时刻、某场景模板下的「期望在线用户数」。

    参数：
        hour     : 一天中的小时数（0.0 ~ 24.0，含小数，如 8.25 表示 08:15）
        scenario : 场景模板名，需存在于 SCENARIO_PEAKS

    返回：
        期望用户数（浮点，未加随机扰动）
    """
    total = NIGHT_BASE_USERS
    for center, sigma, amplitude in SCENARIO_PEAKS[scenario]:
        # 高斯峰：把「时刻差」按环形处理，避免 24 点与 0 点处断裂
        diff = abs(hour - center)
        diff = min(diff, 24.0 - diff)
        total += amplitude * math.exp(-(diff ** 2) / (2 * sigma ** 2))
    return total


def generate(days: int = 1, seed: int = 42):
    """
    生成多天、每 15 分钟粒度的扇区流量记录。

    参数：
        days : 生成天数（验证计划用 7 天，基线对比用 1 天）
        seed : 随机种子，固定后结果可复现，保证 Agent 决策可审计

    返回：
        rows : list[dict]，每条记录对应「某一时刻 + 某一扇区」
    """
    rng = np.random.default_rng(seed)
    rows = []
    total_steps = STEPS_PER_DAY * days

    for step in range(total_steps):
        ts = START_TIME + timedelta(minutes=step * STEP_MINUTES)
        # 取当天内的小时数（多天数据时曲线按天重复）
        hour = ts.hour + ts.minute / 60.0

        for sector in SECTORS:
            base = activity_users(hour, sector["scenario"])

            # 叠加 ±8% 的乘性噪声，模拟真实话务的随机起伏
            noise = float(rng.normal(0.0, 0.08))
            users = max(0.0, base * (1.0 + noise))

            # 用户数取整（在线用户是离散量）
            active_users = int(round(users))

            print_load = active_users / SECTOR_USER_CAPACITY * 100.0
            # 再叠加一点负载测量噪声（负载与用户数不是严格线性关系）
            print_load = print_load + float(rng.normal(0.0, 0.6))
            load_pct = round(float(np.clip(print_load, 0.4, 99.0)), 2)

            # 可休眠标记：负载足够低且在线用户很少，才允许作为休眠候选
            sleepable = (load_pct <= SLEEPABLE_LOAD_PCT) and (active_users <= SLEEPABLE_MAX_USERS)

            rows.append(
                {
                    "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "sector_id": sector["sector_id"],
                    "load_pct": load_pct,
                    "active_users": active_users,
                    "sleepable": "true" if sleepable else "false",
                }
            )
    return rows


def save_csv(rows, out_path: Path):
    """把记录写成 CSV（UTF-8，带表头）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "sector_id", "load_pct", "active_users", "sleepable"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="生成小区多扇区合成流量时序（纯 NumPy）")
    parser.add_argument("--days", type=int, default=1, help="生成天数，默认 1 天（96 个时间步）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子，默认 42")
    parser.add_argument(
        "--out",
        type=str,
        default=str(Path(__file__).parent / "result" / "sector_traffic.csv"),
        help="输出 CSV 路径",
    )
    args = parser.parse_args()

    rows = generate(days=args.days, seed=args.seed)
    out = Path(args.out)
    save_csv(rows, out)

    # 打印一份摘要，方便人工核对数据是否合理
    sleepable_cnt = sum(1 for r in rows if r["sleepable"] == "true")
    users = [r["active_users"] for r in rows]
    load = [r["load_pct"] for r in rows]
    print(f"[OK] 已生成合成流量数据: {out}")
    print(f"     时间步数: {args.days * STEPS_PER_DAY}  扇区数: {len(SECTORS)}  记录数: {len(rows)}")
    print(f"     负载区间: {min(load):.2f}% ~ {max(load):.2f}%   平均在线用户/扇区: {sum(users)/len(users):.1f}")
    print(f"     可休眠标记记录数: {sleepable_cnt} ({sleepable_cnt/len(rows)*100:.1f}%)")


if __name__ == "__main__":
    main()
