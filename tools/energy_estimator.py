#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tools/energy_estimator.py
============================================================
工具 2：estimate_energy_saving(扇区ID列表, 休眠时长)
------------------------------------------------------------
职责：
    量化计算「让指定扇区休眠一段时间」可以节省多少电能。

功耗模型（线性静态+动态模型，公开文献常见形式）：
    P_on(load) = (P_STATIC + P_DYN_MAX * load/100) * hw_factor
    P_sleep    = P_SLEEP * hw_factor
    E_saving   = (P_on - P_sleep) * 时长(h) / 1000   [kWh]

其中 hw_factor 为扇区硬件差异系数（±2.5%），由扇区序号确定性生成，
保证同一扇区每次计算得到完全相同的数值，便于审计复算。

本工具为纯确定性计算，不含任何随机数，也不调用大模型。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.constants import (  # noqa: E402
    P_DYN_MAX_W,
    P_SLEEP_W,
    P_STATIC_W,
    STEP_HOURS,
)


def hardware_factor(sector_id: str) -> float:
    """
    根据扇区 ID 生成确定性的硬件差异系数（0.975 ~ 1.025）。

    用扇区编号的数值部分做偏移，避免随机带来的不可复现问题。
    """
    digits = "".join(ch for ch in sector_id if ch.isdigit())
    idx = int(digits) if digits else 0
    return 1.0 + ((idx % 6) - 2.5) * 0.01


def sector_power_w(sector_id: str, load_pct: float, sleeping: bool = False) -> float:
    """计算单个扇区在给定负载下的瞬时功耗（瓦）。"""
    factor = hardware_factor(sector_id)
    if sleeping:
        return P_SLEEP_W * factor
    return (P_STATIC_W + P_DYN_MAX_W * (load_pct / 100.0)) * factor


def estimate_energy_saving(sector_ids, duration_steps: int = 1, sector_states=None):
    """
    估算让一批扇区休眠指定时长后节省的电能。

    参数：
        sector_ids     : list[str]，拟休眠的扇区 ID 列表
        duration_steps : int，休眠时长（以决策周期为单位，1 步 = 15 分钟）
        sector_states  : list[dict]，当前时刻各扇区状态（含 load_pct）；
                         若为 None，则视为「按当前负载估计」，此时需要外部传入，
                         避免工具内部再做 IO，保持纯函数特性。

    返回：
        dict：
        {
            "sector_ids": [...],
            "duration_hours": 0.25,
            "saving_kwh": 0.182,             # 总节省电量
            "per_sector": [                  # 逐扇区明细，便于审计复算
                {"sector_id": "...", "load_pct": 3.1,
                 "power_on_w": 773.6, "power_sleep_w": 24.4, "saving_kwh": 0.187}
            ],
            "valid": True                    # 传入扇区是否都能在状态表中找到
        }
    """
    duration_steps = max(1, int(duration_steps))
    duration_hours = duration_steps * STEP_HOURS

    # 建立扇区 -> 状态 的索引，便于 O(1) 查询负载
    state_map = {}
    if sector_states:
        state_map = {s["sector_id"]: s for s in sector_states}

    per_sector = []
    total_saving = 0.0
    valid = True

    for sid in sector_ids:
        st = state_map.get(sid)
        if st is None:
            # 状态缺失时无法估算，标记为无效但不抛异常，保持流程连续
            valid = False
            continue
        load = float(st["load_pct"])
        p_on = sector_power_w(sid, load, sleeping=False)
        p_sleep = sector_power_w(sid, load, sleeping=True)
        saving_w = max(0.0, p_on - p_sleep)
        saving_kwh = saving_w * duration_hours / 1000.0
        total_saving += saving_kwh
        per_sector.append(
            {
                "sector_id": sid,
                "load_pct": round(load, 2),
                "power_on_w": round(p_on, 2),
                "power_sleep_w": round(p_sleep, 2),
                "saving_kwh": round(saving_kwh, 6),
            }
        )

    return {
        "sector_ids": list(sector_ids),
        "duration_hours": round(duration_hours, 4),
        "saving_kwh": round(total_saving, 6),
        "per_sector": per_sector,
        "valid": valid,
    }


def as_langchain_tool():
    """把本工具包装成 LangChain Tool（可选依赖，缺失时返回 None）。"""
    try:
        from langchain_core.tools import tool
    except Exception:
        return None

    @tool
    def estimate_energy_saving_tool(sector_ids: list, duration_steps: int) -> dict:
        """量化估算一批扇区休眠指定时长（15分钟/步）可节省的电量（kWh）。"""
        from tools.sector_state import get_sector_state

        return estimate_energy_saving(sector_ids, duration_steps)

    return estimate_energy_saving_tool


if __name__ == "__main__":
    # 冒烟检查：用一组低负载扇区估算节能量
    demo_states = [
        {"sector_id": "CELL-A-SEC-01", "load_pct": 3.1},
        {"sector_id": "CELL-A-SEC-02", "load_pct": 4.0},
    ]
    result = estimate_energy_saving(
        ["CELL-A-SEC-01", "CELL-A-SEC-02"], duration_steps=4, sector_states=demo_states
    )
    print(f"休眠 2 扇区 1 小时可节省: {result['saving_kwh']:.4f} kWh")
    for item in result["per_sector"]:
        print(f"  {item['sector_id']}: {item['power_on_w']}W -> {item['power_sleep_w']}W, 省 {item['saving_kwh']:.4f} kWh")
