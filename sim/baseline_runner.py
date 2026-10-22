#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sim/baseline_runner.py
============================================================
基线策略运行器
------------------------------------------------------------
运行两条基线策略，为后续 Agent 策略提供量化对照基准：

    基线 1 · 全开（always_on）
        所有扇区全天常开，不做任何休眠 —— 代表当前网络的「默认运行方式」，
        是能耗的上界（最费电）与 QoS 的最优参照（无人掉线）。

    基线 2 · 随机休眠（random_sleep）
        每个决策周期，在「可休眠候选」中随机挑选 50% 的扇区休眠，
        **不做 QoS 校验** —— 代表缺乏调度智能的粗放节能方式，
        通常表现为能耗降了一些、但用户未服务比例明显恶化。

输出：
    sim/result/baseline_result.csv     —— 逐时间步明细
    sim/result/baseline_comparison.csv —— 两条基线的汇总对比表

说明：
    随机策略使用固定随机种子，保证结果完全可复现，便于审计与复现实验。
"""

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.qellaris_fallback import SectorSimulator  # noqa: E402
from tools.constants import STEP_HOURS  # noqa: E402
from tools.energy_estimator import sector_power_w  # noqa: E402
from tools.qos_estimator import estimate_qos_impact  # noqa: E402

RESULT_DIR = Path(__file__).parent / "result"


def run_always_on(sim: SectorSimulator):
    """执行「全开」基线：所有扇区常开，逐时间步累计能耗。"""
    rows = []
    total_energy = 0.0
    users_sum = 0
    for ts in sim.timestamps():
        states = sim.get_state(ts)
        step_power = sum(sector_power_w(s["sector_id"], s["load_pct"]) for s in states)
        step_energy = step_power * STEP_HOURS / 1000.0
        total_energy += step_energy
        users_sum += sum(s["active_users"] for s in states)
        rows.append(
            {
                "timestamp": ts,
                "strategy": "always_on",
                "sleeping_sectors": "",
                "total_users": sum(s["active_users"] for s in states),
                "step_power_w": round(step_power, 2),
                "step_energy_kwh": round(step_energy, 6),
                "step_unserved_ratio": 0.0,
                "step_unserved_users": 0.0,
            }
        )
    stats = {
        "energy_kwh": total_energy,
        "avg_unserved_ratio": 0.0,
        "weighted_unserved_ratio": 0.0,
        "sleep_sector_steps": 0,
        "users_sum": users_sum,
    }
    return rows, stats


def run_random_sleep(sim: SectorSimulator, seed: int = 7, ratio: float = 0.5):
    """
    执行「随机休眠」基线。

    参数：
        seed  : 随机种子，固定后结果可复现
        ratio : 在可休眠候选中被选中休眠的比例
    """
    rng = np.random.default_rng(seed)
    rows = []
    total_energy = 0.0
    unserved_ratio_sum = 0.0
    unserved_users_sum = 0.0
    users_sum = 0.0
    sleep_sector_steps = 0
    steps = 0

    for ts in sim.timestamps():
        states = sim.get_state(ts)
        candidates = [s["sector_id"] for s in states if s["sleepable"]]

        # 在候选中随机抽取一部分扇区休眠（不校验 QoS，模拟粗放节能）
        k = int(round(len(candidates) * ratio))
        chosen = list(rng.choice(candidates, size=k, replace=False)) if k > 0 else []

        step_power = 0.0
        for s in states:
            sleeping = s["sector_id"] in chosen
            step_power += sector_power_w(s["sector_id"], s["load_pct"], sleeping=sleeping)

        step_energy = step_power * STEP_HOURS / 1000.0
        total_energy += step_energy

        # 用与 Agent 完全相同的 QoS 模型评估，保证对比口径一致
        qos = estimate_qos_impact(chosen, duration_steps=1, sector_states=states)
        unserved_ratio_sum += qos["unserved_ratio"]
        unserved_users_sum += qos["unserved_users"]
        users_sum += qos["total_users"]
        sleep_sector_steps += len(chosen)
        steps += 1

        rows.append(
            {
                "timestamp": ts,
                "strategy": "random_sleep",
                "sleeping_sectors": "|".join(sorted(chosen)),
                "total_users": qos["total_users"],
                "step_power_w": round(step_power, 2),
                "step_energy_kwh": round(step_energy, 6),
                "step_unserved_ratio": qos["unserved_ratio"],
                "step_unserved_users": qos["unserved_users"],
            }
        )

    stats = {
        "energy_kwh": total_energy,
        "avg_unserved_ratio": (unserved_ratio_sum / steps) if steps else 0.0,
        "weighted_unserved_ratio": (unserved_users_sum / users_sum) if users_sum > 0 else 0.0,
        "sleep_sector_steps": sleep_sector_steps,
    }
    return rows, stats


def write_csv(rows, path: Path):
    """写出逐时间步明细 CSV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    sim = SectorSimulator()
    print(f"[信息] 数据源: {sim.source}  时间步: {len(sim.timestamps())}")

    # --- 基线 1：全开 ---
    always_rows, always_stats = run_always_on(sim)
    # --- 基线 2：随机休眠 ---
    random_rows, random_stats = run_random_sleep(sim)

    write_csv(always_rows + random_rows, RESULT_DIR / "baseline_result.csv")

    always_energy = always_stats["energy_kwh"]
    random_energy = random_stats["energy_kwh"]

    # --- 汇总对比表 ---
    summary = [
        {
            "strategy": "always_on",
            "description": "全开基线：所有扇区全天常开",
            "total_energy_kwh": round(always_energy, 4),
            "energy_saving_pct": 0.0,
            "avg_unserved_ratio_pct": 0.0,
            "weighted_unserved_ratio_pct": 0.0,
            "sleep_sector_steps": 0,
        },
        {
            "strategy": "random_sleep",
            "description": "随机基线：候选中随机 50% 休眠（不做 QoS 校验）",
            "total_energy_kwh": round(random_energy, 4),
            "energy_saving_pct": round((always_energy - random_energy) / always_energy * 100.0, 3),
            "avg_unserved_ratio_pct": round(random_stats["avg_unserved_ratio"] * 100.0, 4),
            "weighted_unserved_ratio_pct": round(random_stats["weighted_unserved_ratio"] * 100.0, 4),
            "sleep_sector_steps": random_stats["sleep_sector_steps"],
        },
    ]

    out = RESULT_DIR / "baseline_comparison.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)

    print(f"[OK] 逐时间步明细: {RESULT_DIR / 'baseline_result.csv'}")
    print(f"[OK] 基线对比表  : {out}")
    for row in summary:
        print(f"     {row['strategy']:<14} 能耗 {row['total_energy_kwh']:8.3f} kWh  "
              f"节能 {row['energy_saving_pct']:6.2f}%  未服务率 {row['avg_unserved_ratio_pct']:6.3f}%")


if __name__ == "__main__":
    main()
