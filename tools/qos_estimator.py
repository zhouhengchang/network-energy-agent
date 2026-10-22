#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tools/qos_estimator.py
============================================================
工具 3：estimate_qos_impact(扇区ID列表, 休眠时长)
------------------------------------------------------------
职责：
    预估一批扇区休眠后，会有多少用户无法接入（未服务），
    并给出未服务用户占小区总在线用户的比例。

QoS 影响模型（两级确定性判定）：
    1) 覆盖空洞：即使邻区有余量，扇区关断瞬间仍有一部分用户因
       覆盖盲区/切换失败而掉线 —— 按 HOLE_RATE 比例估计；
    2) 容量溢出：把休眠扇区的用户迁移到同小区其他扇区，
       若邻区剩余容量不足，超出部分也计为未服务。

    unserved = min(休眠扇区用户数, 覆盖空洞用户 + 容量溢出用户)
    unserved_ratio = unserved / 小区总在线用户数

本工具为纯确定性计算：同一份输入永远得到同一份输出，可逐条复算。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.constants import (  # noqa: E402
    HOLE_RATE,
    RESIDUAL_MARGIN,
    SECTOR_USER_CAPACITY,
    STEP_HOURS,
)


def residual_capacity_users(load_pct: float) -> float:
    """
    计算某扇区可对外吸收的用户余量。

    余量 = 容量 * (安全边际 - 当前负载占比)，负值归零。
    例如负载 8% 时，余量 ≈ 容量 * 0.77。
    """
    return max(0.0, SECTOR_USER_CAPACITY * (RESIDUAL_MARGIN - load_pct / 100.0))


def estimate_qos_impact(sector_ids, duration_steps: int = 1, sector_states=None):
    """
    估算让一批扇区休眠对用户服务的影响。

    参数：
        sector_ids     : list[str]，拟休眠扇区列表
        duration_steps : int，休眠持续时长（决策周期数，1 步 = 15 分钟）
        sector_states  : list[dict]，当前时刻全部扇区状态（含 load_pct / active_users）

    返回：
        dict：
        {
            "sleeping_users": 9,             # 休眠扇区上的在线用户数
            "total_users": 21,               # 小区总在线用户数
            "absorbed_users": 8.8,           # 可被邻区吸收的用户数
            "unserved_users": 0.18,          # 预计未服务用户数
            "unserved_ratio": 0.0086,        # 未服务用户占比
            "user_minutes_lost": 2.7,        # 累计影响（用户-分钟），用于跨周期汇总
            "detail": {...},                 # 明细，便于审计
            "valid": True
        }
    """
    duration_steps = max(1, int(duration_steps))
    sleep_set = set(sector_ids)

    if not sector_states:
        return {
            "sleeping_users": 0,
            "total_users": 0,
            "absorbed_users": 0.0,
            "unserved_users": 0.0,
            "unserved_ratio": 0.0,
            "user_minutes_lost": 0.0,
            "detail": {},
            "valid": False,
        }

    total_users = sum(int(s["active_users"]) for s in sector_states)

    # 休眠扇区承载的用户（需要迁移的用户）
    sleeping_users = sum(
        int(s["active_users"]) for s in sector_states if s["sector_id"] in sleep_set
    )

    # 未休眠扇区可对外吸收的用户余量总和
    absorb_capacity = sum(
        residual_capacity_users(float(s["load_pct"]))
        for s in sector_states
        if s["sector_id"] not in sleep_set
    )

    # 覆盖空洞导致的掉线用户
    hole_users = sleeping_users * HOLE_RATE
    # 容量不足导致的掉线用户
    overflow_users = max(0.0, sleeping_users - absorb_capacity)

    unserved = min(float(sleeping_users), hole_users + overflow_users)
    ratio = (unserved / total_users) if total_users > 0 else 0.0

    # 校验传入的扇区 ID 是否都能在状态表中找到
    state_ids = {s["sector_id"] for s in sector_states}
    valid = all(sid in state_ids for sid in sector_ids)

    return {
        "sleeping_users": sleeping_users,
        "total_users": total_users,
        "absorbed_users": round(sleeping_users - unserved, 3),
        "unserved_users": round(unserved, 3),
        "unserved_ratio": round(ratio, 6),
        # 用户-分钟：未服务用户数 × 时长（分钟），用于跨周期累计影响统计
        "user_minutes_lost": round(unserved * duration_steps * 15.0, 2),
        "detail": {
            "absorb_capacity_users": round(absorb_capacity, 2),
            "hole_users": round(hole_users, 3),
            "overflow_users": round(overflow_users, 3),
            "duration_hours": round(duration_steps * STEP_HOURS, 3),
        },
        "valid": valid,
    }


def as_langchain_tool():
    """把本工具包装成 LangChain Tool（可选依赖，缺失时返回 None）。"""
    try:
        from langchain_core.tools import tool
    except Exception:
        return None

    @tool
    def estimate_qos_impact_tool(sector_ids: list, duration_steps: int) -> dict:
        """预估一批扇区休眠后未服务用户占比（QoS 影响）。"""
        return estimate_qos_impact(sector_ids, duration_steps)

    return estimate_qos_impact_tool


if __name__ == "__main__":
    # 冒烟检查：凌晨低负载场景下休眠 3 个扇区的 QoS 影响
    demo_states = [
        {"sector_id": "CELL-A-SEC-01", "load_pct": 3.1, "active_users": 3},
        {"sector_id": "CELL-A-SEC-02", "load_pct": 4.0, "active_users": 4},
        {"sector_id": "CELL-A-SEC-03", "load_pct": 2.8, "active_users": 3},
        {"sector_id": "CELL-A-SEC-04", "load_pct": 5.2, "active_users": 5},
        {"sector_id": "CELL-A-SEC-05", "load_pct": 3.6, "active_users": 4},
        {"sector_id": "CELL-A-SEC-06", "load_pct": 4.4, "active_users": 4},
    ]
    r = estimate_qos_impact(
        ["CELL-A-SEC-01", "CELL-A-SEC-02", "CELL-A-SEC-03"], duration_steps=1, sector_states=demo_states
    )
    print(f"休眠 3 扇区 -> 未服务 {r['unserved_users']} 人 / 总 {r['total_users']} 人 = {r['unserved_ratio']*100:.3f}%")
