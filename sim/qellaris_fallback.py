#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sim/qellaris_fallback.py
============================================================
仿真包适配 / 兜底脚本
------------------------------------------------------------
本项目的首选数据源是第三方基站节能仿真包（CellEnergySim / Qellaris）。
但这类包大多未公开发布在 PyPI 上，或在无外网环境、离线算力环境中无法安装。

因此本脚本承担「统一数据源适配层」的职责：
    1. 先尝试导入第三方仿真包；
    2. 导入失败（包不存在 / 网络不可用 / 版本不匹配）时，
       自动降级到 sim/traffic_generator.py 的纯 NumPy 合成流量；
    3. 对外暴露完全一致的接口，上层 Agent 无需感知数据源差异。

这里没有任何硬编码的外部地址或凭证，全部为本地文件读取。
"""

import csv
import importlib
from pathlib import Path

# 优先尝试的第三方仿真包候选（按优先级排列）
THIRD_PARTY_CANDIDATES = ["cellenergysim", "qellaris", "cellsim"]

RESULT_DIR = Path(__file__).parent / "result"
DEFAULT_TRAFFIC_CSV = RESULT_DIR / "sector_traffic.csv"


def detect_third_party():
    """
    探测可用的第三方仿真包。

    返回：
        (module_name, module) —— 找到时返回包名与模块对象
        (None, None)          —— 全部不可用时返回空
    """
    for name in THIRD_PARTY_CANDIDATES:
        try:
            module = importlib.import_module(name)
            return name, module
        except Exception:
            # 包不存在或依赖缺失，继续尝试下一个候选
            continue
    return None, None


def load_traffic_csv(csv_path: Path = DEFAULT_TRAFFIC_CSV):
    """
    读取合成流量 CSV，返回 {时间戳: [扇区状态字典, ...]}。

    参数：
        csv_path : 流量数据文件路径

    返回：
        timeline : dict，键为时间戳字符串，值为该时刻所有扇区的状态列表
        sector_ids : list，稳定排序的扇区 ID 列表
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"未找到流量数据文件 {csv_path}。请先运行：python sim/traffic_generator.py"
        )

    timeline = {}
    sector_ids = []
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = row["timestamp"]
            sid = row["sector_id"]
            if sid not in sector_ids:
                sector_ids.append(sid)
            timeline.setdefault(ts, []).append(
                {
                    "sector_id": sid,
                    "load_pct": float(row["load_pct"]),
                    "active_users": int(row["active_users"]),
                    "sleepable": row["sleepable"].strip().lower() == "true",
                    # 默认所有扇区初始为活跃（常开）状态
                    "active": True,
                }
            )

    # 同一时刻内的扇区按 ID 排序，保证遍历顺序确定、决策可复现
    for ts in timeline:
        timeline[ts].sort(key=lambda s: s["sector_id"])
    sector_ids.sort()
    return timeline, sector_ids


class SectorSimulator:
    """
    统一的数据源访问入口。

    优先级：
        1) 第三方仿真包（若可导入）；
        2) 本地 NumPy 合成流量 CSV（兜底，默认路径）。

    source 属性会记录最终生效的数据源，供审计日志留痕。
    """

    def __init__(self, csv_path: Path = DEFAULT_TRAFFIC_CSV):
        self.csv_path = Path(csv_path)
        name, module = detect_third_party()
        self.third_party_name = name
        self.third_party_module = module

        if name is not None:
            # 第三方包可用时的接入点：真实部署时在此把包的场景对象适配为统一字段
            self.source = f"third_party:{name}"
            try:
                self.timeline, self.sector_ids = self._load_from_third_party(module)
            except Exception as exc:  # 适配失败同样降级，绝不让流程中断
                print(f"[WARN] 第三方仿真包 {name} 适配失败（{exc}），自动降级为 NumPy 合成流量")
                self.source = "numpy_synthetic"
                self.timeline, self.sector_ids = load_traffic_csv(self.csv_path)
        else:
            self.source = "numpy_synthetic"
            self.timeline, self.sector_ids = load_traffic_csv(self.csv_path)

    def _load_from_third_party(self, module):
        """
        第三方包适配桩函数。

        说明：不同仿真包的 API 差异很大，这里保留统一出口；
        若包提供了多扇区场景对象，可在此转换为本项目字段：
            sector_id / load_pct / active_users / sleepable / active
        目前未找到公开可安装的包，因此该方法在实战中通常走异常分支降级。
        """
        raise NotImplementedError("当前环境未提供可用的第三方仿真包适配实现")

    def timestamps(self):
        """返回按时间升序排列的时间戳列表。"""
        return sorted(self.timeline.keys())

    def get_state(self, timestamp: str):
        """读取指定时刻所有扇区的状态快照（只读）。"""
        return self.timeline.get(timestamp, [])


if __name__ == "__main__":
    sim = SectorSimulator()
    print(f"数据源: {sim.source}")
    print(f"扇区数: {len(sim.sector_ids)} -> {sim.sector_ids}")
    ts_list = sim.timestamps()
    print(f"时间步数: {len(ts_list)}，起点 {ts_list[0]}，终点 {ts_list[-1]}")
