#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tools/sector_state.py
============================================================
工具 1：get_sector_state(time)
------------------------------------------------------------
职责：
    读取指定时刻小区内所有扇区的实时状态（负载、在线用户数、可休眠权限）。

数据来源：
    sim/qellaris_fallback.py 提供的统一数据源（第三方仿真包或 NumPy 合成流量）。
    试验网阶段，这里将替换为「只读负载数据接口」的客户端实现；
    接口只读，具备任何写操作能力是不允许的。

确定性说明：
    本工具是纯函数式读取，不做任何推测或随机，同一时间戳永远返回同一结果。
"""

import sys
from pathlib import Path

# 允许以 `python tools/sector_state.py` 或从仓库根目录导入两种方式运行
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.qellaris_fallback import SectorSimulator  # noqa: E402

# 全局单例：避免每个时间步都重新解析 CSV，降低运行开销
_SIMULATOR = None


def _get_simulator() -> SectorSimulator:
    """惰性初始化数据源单例。"""
    global _SIMULATOR
    if _SIMULATOR is None:
        _SIMULATOR = SectorSimulator()
    return _SIMULATOR


def configure_source(csv_path) -> None:
    """
    显式指定数据源文件（用于回放非默认路径的流量数据，例如 7 天扩展数据）。

    必须由批处理入口在开始回放前调用，否则工具会一直读取默认的单日数据，
    导致跨天数据「读不到状态」而全部退化为保持全开。
    """
    global _SIMULATOR
    _SIMULATOR = SectorSimulator(Path(csv_path))


def get_sector_state(time: str):
    """
    读取某个决策时刻的全部扇区状态。

    参数：
        time : 时间戳字符串，格式 "YYYY-MM-DD HH:MM:SS"

    返回：
        dict，结构如下：
        {
            "timestamp": "2026-01-15 03:00:00",
            "sectors": [
                {
                    "sector_id": "CELL-A-SEC-01",
                    "load_pct": 3.1,          # 负载百分比 0~100
                    "active_users": 3,        # 在线用户数
                    "sleepable": True,        # 是否具备休眠权限（低负载候选）
                    "active": True            # 当前是否处于活跃（未休眠）状态
                },
                ...
            ],
            "total_users": 18,                # 小区总在线用户数
            "total_load_pct": 19.4            # 小区平均负载（各扇区负载均值）
        }
    """
    sim = _get_simulator()
    sectors = sim.get_state(time)

    if not sectors:
        # 时间戳不存在时返回空结构，由上层节点判定并走「保持全开」的保守分支
        return {
            "timestamp": time,
            "sectors": [],
            "total_users": 0,
            "total_load_pct": 0.0,
        }

    total_users = sum(s["active_users"] for s in sectors)
    total_load = sum(s["load_pct"] for s in sectors) / len(sectors)

    return {
        "timestamp": time,
        "sectors": [
            {
                "sector_id": s["sector_id"],
                "load_pct": s["load_pct"],
                "active_users": s["active_users"],
                "sleepable": bool(s["sleepable"]),
                "active": bool(s["active"]),
            }
            for s in sectors
        ],
        "total_users": total_users,
        "total_load_pct": round(total_load, 2),
    }


def list_timestamps():
    """返回数据源中全部决策时刻（升序），供 Agent 按周期推进。"""
    return _get_simulator().timestamps()


def data_source_name() -> str:
    """返回当前生效的数据源名称（numpy_synthetic / third_party:<name>），用于审计留痕。"""
    return _get_simulator().source


def as_langchain_tool():
    """
    把本工具包装成 LangChain Tool 对象。

    说明：
        Agent 的数值决策完全由代码完成，此处包装仅为满足「工具可被统一注册/检索」
        的工程规范；即使不安装 langchain-core，本模块也可独立运行。
    """
    try:
        from langchain_core.tools import tool
    except Exception:  # 未安装 langchain 时静默跳过
        return None

    @tool
    def get_sector_state_tool(time: str) -> dict:
        """读取指定时刻小区所有扇区的负载、在线用户数与休眠权限（只读）。"""
        return get_sector_state(time)

    return get_sector_state_tool


if __name__ == "__main__":
    # 直接运行时做一次冒烟检查
    ts = list_timestamps()
    print(f"数据源: {data_source_name()}  时间步: {len(ts)}")
    snapshot = get_sector_state(ts[12])
    print(f"时刻 {snapshot['timestamp']}  总用户 {snapshot['total_users']}  平均负载 {snapshot['total_load_pct']}%")
    for s in snapshot["sectors"]:
        print(f"  {s['sector_id']}  load={s['load_pct']:5.2f}%  users={s['active_users']:3d}  sleepable={s['sleepable']}")
