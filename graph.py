#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
graph.py
============================================================
小区扇区自主休眠节能调度 Agent（LangGraph 主逻辑）
------------------------------------------------------------
设计原则（可审计 / 可复现）：
    1. **数值决策全部由确定性代码完成**：候选筛选、组合枚举、加权打分、
       QoS 校验、异常回滚，全部是纯 Python 计算，没有随机数、没有 LLM 参与；
    2. **LLM 只做事后解释**：把已经算好的结构化决策结果翻译成自然语言说明，
       即使 LLM 不可用，也只影响「解释文本」，完全不影响决策结果；
    3. **决策链全程留痕**：每轮决策都写入 decision_history（保留最近 10 轮），
       任何人可以据此复算当轮的节能收益与 QoS 影响。

决策图节点顺序（与设计文档严格一致）：
    START
      -> ① read_sector_state      读取当前扇区状态
      -> ② filter_candidates      筛选满足休眠条件的候选扇区
      -> ③ score_combinations     遍历候选组合，计算「节能收益 - QoS损失」加权总分
      -> ④ select_best            选出总分最优的休眠组合
      -> ⑤ qos_check              QoS 校验（超阈值则放弃休眠、保持全开）
      -> ⑥ audit_and_rollback     写入决策历史；突发流量自动回滚至上一轮稳定配置
      -> ⑦ explain_with_llm      调用本地 Ollama 把决策结果翻译为自然语言（仅解释）
      -> END

运行方式：
    python graph.py --csv sim/result/sector_traffic.csv \
                    --out sim/result/agent_result.csv \
                    --llm-model qwen2.5-coder:0.5b
"""

import argparse
import csv
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, TypedDict

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.graph import END, START, StateGraph  # noqa: E402

from sim.qellaris_fallback import SectorSimulator  # noqa: E402
from tools.constants import (  # noqa: E402
    BURST_LOAD_DELTA_PCT,
    HISTORY_KEEP,
    MAX_COMBINATION_ENUM,
    QOS_CONSTRAINT_DEFAULT,
    STEP_HOURS,
    W_ENERGY,
    W_QOS,
    W_SWITCH,
)
from tools.energy_estimator import estimate_energy_saving, sector_power_w  # noqa: E402
from tools.qos_estimator import estimate_qos_impact  # noqa: E402
from tools.sector_state import configure_source, get_sector_state  # noqa: E402

# 节能目标：每个决策周期的能耗上限 = 全开能耗 × (1 - 该比例)
ENERGY_SAVING_TARGET = 0.10

# 本地 Ollama 服务地址与默认模型（无凭证、无外部服务）
OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_LLM_MODEL = "qwen2.5-coder:0.5b"


# ===========================================================================
# 第 4 步：Agent 全局状态 State（TypedDict）
# ===========================================================================
class AgentState(TypedDict, total=False):
    """
    LangGraph 全局状态。

    业务字段（严格按设计定义）：
        current_time     : 当前决策时刻（"YYYY-MM-DD HH:MM:SS"）
        sector_states    : 字典 {扇区ID: {load_pct, active_users, active, sleepable}}
        energy_budget    : 本时段能耗上限（kWh），用于约束节能目标
        qos_constraint   : 最大允许未服务用户占比（默认 1%）
        decision_history : 最近 10 轮决策记录（审计与回滚依据）

    流程内部字段：
        scratch          : 单轮决策的临时黑板（候选、打分结果、拟执行方案等）。
                           它只在一次 invoke 内有效，不参与跨轮审计，
                           目的是让业务字段保持干净、语义稳定。
    """

    current_time: str
    sector_states: Dict[str, Dict[str, Any]]
    energy_budget: float
    qos_constraint: float
    decision_history: List[Dict[str, Any]]
    scratch: Dict[str, Any]


# ===========================================================================
# 节点 ① 读取当前扇区状态
# ===========================================================================
def read_sector_state(state: AgentState) -> Dict[str, Any]:
    """
    调用只读工具 get_sector_state(time)，把列表形式的状态整理成字典，
    同时计算「若全部常开」的基准能耗，作为 energy_budget 的计算基准。
    """
    ts = state["current_time"]
    snapshot = get_sector_state(ts)

    sector_states: Dict[str, Dict[str, Any]] = {}
    baseline_power = 0.0
    for s in snapshot["sectors"]:
        sector_states[s["sector_id"]] = {
            "load_pct": s["load_pct"],
            "active_users": s["active_users"],
            "active": s["active"],
            "sleepable": s["sleepable"],
        }
        # 全开基准功率（用于能耗预算与节能率计算）
        baseline_power += sector_power_w(s["sector_id"], s["load_pct"], sleeping=False)

    baseline_energy = baseline_power * STEP_HOURS / 1000.0
    # 本时段能耗上限：全开能耗 × (1 - 节能目标)
    energy_budget = baseline_energy * (1.0 - ENERGY_SAVING_TARGET)

    # 保留上游传入的临时黑板配置（如 use_llm / llm_model），只更新本节点的计算结果
    scratch = dict(state.get("scratch", {}))
    scratch.update(
        {
            "baseline_power_w": round(baseline_power, 3),
            "baseline_energy_kwh": round(baseline_energy, 6),
            "total_users": snapshot["total_users"],
            "total_load_pct": snapshot["total_load_pct"],
        }
    )

    return {
        "sector_states": sector_states,
        "energy_budget": round(energy_budget, 6),
        "scratch": scratch,
    }


# ===========================================================================
# 节点 ② 筛选候选扇区
# ===========================================================================
def filter_candidates(state: AgentState) -> Dict[str, Any]:
    """
    筛选候选扇区：必须同时满足「具备休眠权限」且「当前是活跃状态」。

    这里只做规则过滤，不涉及任何打分或偏好。
    """
    candidates = [
        sid
        for sid, info in state["sector_states"].items()
        if info.get("sleepable") and info.get("active")
    ]
    scratch = dict(state.get("scratch", {}))
    scratch["candidates"] = sorted(candidates)
    return {"scratch": scratch}


# ===========================================================================
# 节点 ③ 遍历候选组合并打分
# ===========================================================================
def score_combinations(state: AgentState) -> Dict[str, Any]:
    """
    枚举所有候选扇区组合（含空集），对每个组合计算：
        score = W_ENERGY × 节省电量(kWh) − W_QOS × 未服务用户数

    约束过滤（两个硬约束）：
        1) 未服务用户占比 <= qos_constraint
        2) 本周期能耗 <= energy_budget

    组合数超过 MAX_COMBINATION_ENUM 时改为贪心增量策略，避免组合爆炸。
    """
    scratch = dict(state.get("scratch", {}))
    candidates: List[str] = scratch.get("candidates", [])
    sector_states = state["sector_states"]
    qos_constraint = state.get("qos_constraint", QOS_CONSTRAINT_DEFAULT)
    energy_budget = state.get("energy_budget", float("inf"))
    baseline_energy = scratch.get("baseline_energy_kwh", 0.0)

    # 上一轮实际执行的休眠配置（用于计算切换代价，抑制决策抖动）
    history = state.get("decision_history", [])
    prev_set = set(history[-1].get("sleeping_sectors", [])) if history else set()

    # 把扇区状态转成工具需要的列表形式
    state_list = [
        {
            "sector_id": sid,
            "load_pct": info["load_pct"],
            "active_users": info["active_users"],
        }
        for sid, info in sector_states.items()
    ]

    def evaluate(combo):
        """评估单个组合，返回 (score, 明细)。"""
        combo = list(combo)
        energy = estimate_energy_saving(combo, duration_steps=1, sector_states=state_list)
        qos = estimate_qos_impact(combo, duration_steps=1, sector_states=state_list)
        plan_energy = baseline_energy - energy["saving_kwh"]

        # 切换代价：与上一轮实际配置相比，新增或撤销的扇区数量
        # 用对称差衡量，避免单步在阈值边界反复开关扇区
        switch_count = len(set(combo) ^ prev_set)
        score = (
            W_ENERGY * energy["saving_kwh"]
            - W_QOS * qos["unserved_users"]
            - W_SWITCH * switch_count
        )
        feasible = (qos["unserved_ratio"] <= qos_constraint) and (plan_energy <= energy_budget + 1e-9)
        return {
            "sectors": combo,
            "saving_kwh": round(energy["saving_kwh"], 6),
            "plan_energy_kwh": round(plan_energy, 6),
            "unserved_users": round(qos["unserved_users"], 4),
            "unserved_ratio": round(qos["unserved_ratio"], 6),
            "switch_count": switch_count,
            "score": round(score, 6),
            "feasible": feasible,
        }

    options: List[Dict[str, Any]] = []

    if len(candidates) <= 12 and (2 ** len(candidates)) <= MAX_COMBINATION_ENUM:
        # 全量枚举：小规模时最优解一定在枚举范围内
        for r in range(len(candidates) + 1):
            for combo in itertools.combinations(candidates, r):
                options.append(evaluate(combo))
        strategy = "exhaustive"
    else:
        # 贪心增量：按「单位未服务代价的节能量」从高到低逐个加入候选
        ranked = sorted(
            candidates,
            key=lambda sid: (
                sector_power_w(sid, sector_states[sid]["load_pct"], False)
                - sector_power_w(sid, sector_states[sid]["load_pct"], True)
            ),
            reverse=True,
        )
        current: List[str] = []
        options.append(evaluate(current))
        for sid in ranked:
            trial = current + [sid]
            result = evaluate(trial)
            if result["feasible"]:
                current = trial
            options.append(result)
        strategy = "greedy"

    # 只在可行解中选最优；若没有可行解（含空集不可行的极端情况）则退回空集
    feasible_options = [o for o in options if o["feasible"]]
    if feasible_options:
        best = max(feasible_options, key=lambda o: (o["score"], len(o["sectors"])))
    else:
        best = evaluate([])

    scratch.update(
        {
            "scored_strategy": strategy,
            "scored_options": options,
            "option_count": len(options),
            "proposed_plan": best,
        }
    )

    # 记录最优方案和空集基线，便于解释模块说明「为什么选它 / 为什么不全开」
    scratch["empty_plan"] = evaluate([])
    return {"scratch": scratch}


# ===========================================================================
# 节点 ④ 选出最优休眠组合
# ===========================================================================
def select_best(state: AgentState) -> Dict[str, Any]:
    """
    从上一步的打分结果中取总分最优的组合，形成「拟执行方案」。

    本节点不做任何新的计算，只是把最优方案显式化，便于审计时定位决策依据。
    """
    scratch = dict(state.get("scratch", {}))
    best = scratch.get("proposed_plan", {})
    scratch["selected_plan"] = {
        "sectors": list(best.get("sectors", [])),
        "saving_kwh": best.get("saving_kwh", 0.0),
        "unserved_users": best.get("unserved_users", 0.0),
        "unserved_ratio": best.get("unserved_ratio", 0.0),
        "score": best.get("score", 0.0),
        "plan_energy_kwh": best.get("plan_energy_kwh", 0.0),
    }
    return {"scratch": scratch}


# ===========================================================================
# 节点 ⑤ QoS 校验
# ===========================================================================
def qos_check(state: AgentState) -> Dict[str, Any]:
    """
    对最优方案做一次独立的 QoS 复核。

    注意：这一步不是重复计算，而是「独立复核」——
    即使前面打分环节出现逻辑漏洞，这里仍会用工具重新计算一次未服务比例，
    只有复核通过的方案才允许执行。
    """
    scratch = dict(state.get("scratch", {}))
    plan = scratch.get("selected_plan", {})
    sector_states = state["sector_states"]
    qos_constraint = state.get("qos_constraint", QOS_CONSTRAINT_DEFAULT)

    state_list = [
        {"sector_id": sid, "load_pct": info["load_pct"], "active_users": info["active_users"]}
        for sid, info in sector_states.items()
    ]
    verify = estimate_qos_impact(plan.get("sectors", []), duration_steps=1, sector_states=state_list)

    passed = verify["unserved_ratio"] <= qos_constraint
    scratch["qos_verify"] = {
        "unserved_ratio": verify["unserved_ratio"],
        "unserved_users": verify["unserved_users"],
        "limit": qos_constraint,
        "passed": passed,
    }
    scratch["qos_passed"] = passed
    return {"scratch": scratch}


def route_after_qos(state: AgentState) -> str:
    """条件边：QoS 复核通过则执行休眠，否则放弃休眠并保持全开。"""
    return "apply" if state.get("scratch", {}).get("qos_passed") else "abort"


# ===========================================================================
# 节点 ⑥-a 执行休眠方案 / ⑥-b 放弃休眠（全开）
# ===========================================================================
def apply_decision(state: AgentState) -> Dict[str, Any]:
    """执行休眠方案：记录本周期实际能耗与未服务比例。"""
    scratch = dict(state.get("scratch", {}))
    plan = scratch.get("selected_plan", {})
    sector_states = state["sector_states"]
    sleeping = set(plan.get("sectors", []))

    total_power = sum(
        sector_power_w(sid, info["load_pct"], sleeping=(sid in sleeping))
        for sid, info in sector_states.items()
    )
    step_energy = total_power * STEP_HOURS / 1000.0

    scratch["applied"] = {
        "sectors": sorted(sleeping),
        "step_energy_kwh": round(step_energy, 6),
        "step_power_w": round(total_power, 2),
        "action": "sleep" if sleeping else "keep_all_on",
    }
    return {"scratch": scratch}


def abort_all_on(state: AgentState) -> Dict[str, Any]:
    """
    放弃休眠、保持全开。

    触发条件：最优方案的未服务比例超过 qos_constraint（用户服务阈值优先于节能）。
    """
    scratch = dict(state.get("scratch", {}))
    sector_states = state["sector_states"]
    total_power = sum(
        sector_power_w(sid, info["load_pct"], sleeping=False)
        for sid, info in sector_states.items()
    )
    step_energy = total_power * STEP_HOURS / 1000.0

    scratch["applied"] = {
        "sectors": [],
        "step_energy_kwh": round(step_energy, 6),
        "step_power_w": round(total_power, 2),
        "action": "abort_all_on",
    }
    return {"scratch": scratch}


# ===========================================================================
# 节点 ⑥ 写入决策历史 + 突发流量回滚
# ===========================================================================
def audit_and_rollback(state: AgentState) -> Dict[str, Any]:
    """
    审计与回滚：
        1) 与上一轮决策比较小区总负载，若跳变超过阈值（突发流量），
           立即撤销本轮休眠、回滚到上一轮稳定配置（保守地回到全开）；
        2) 把本轮决策写入 decision_history，只保留最近 HISTORY_KEEP 轮。

    回滚原则：宁可少省电，也不让突发流量下出现用户掉线。
    """
    scratch = dict(state.get("scratch", {}))
    applied = scratch.get("applied", {})
    history: List[Dict[str, Any]] = list(state.get("decision_history", []))
    total_load = scratch.get("total_load_pct", 0.0)

    # --- 突发流量检测 ---
    rollback_triggered = False
    rollback_reason = ""
    if history:
        prev_load = history[-1].get("total_load_pct", 0.0)
        delta = total_load - prev_load
        if delta > BURST_LOAD_DELTA_PCT and applied.get("sectors"):
            rollback_triggered = True
            rollback_reason = (
                f"小区总负载较上一周期突增 {delta:.1f} 个百分点"
                f"（>{BURST_LOAD_DELTA_PCT:.0f}），判定为突发流量"
            )
            # 回滚：撤销全部休眠，恢复到「全开」稳定配置
            sector_states = state["sector_states"]
            total_power = sum(
                sector_power_w(sid, info["load_pct"], sleeping=False)
                for sid, info in sector_states.items()
            )
            applied = {
                "sectors": [],
                "step_energy_kwh": round(total_power * STEP_HOURS / 1000.0, 6),
                "step_power_w": round(total_power, 2),
                "action": "rollback_all_on",
            }
            scratch["applied"] = applied

    # --- 写审计记录 ---
    record = {
        "time": state["current_time"],
        "action": applied.get("action", "keep_all_on"),
        "sleeping_sectors": sorted(applied.get("sectors", [])),
        "step_energy_kwh": applied.get("step_energy_kwh", 0.0),
        "step_power_w": applied.get("step_power_w", 0.0),
        "total_load_pct": total_load,
        "total_users": scratch.get("total_users", 0),
        "baseline_energy_kwh": scratch.get("baseline_energy_kwh", 0.0),
        "energy_budget_kwh": state.get("energy_budget", 0.0),
        "qos_verify": scratch.get("qos_verify", {}),
        "qos_constraint": state.get("qos_constraint", QOS_CONSTRAINT_DEFAULT),
        "score": scratch.get("selected_plan", {}).get("score", 0.0),
        "rollback": rollback_triggered,
        "rollback_reason": rollback_reason,
    }
    history.append(record)
    history = history[-HISTORY_KEEP:]

    scratch["rollback_triggered"] = rollback_triggered
    scratch["last_record"] = record
    return {"decision_history": history, "scratch": scratch}


# ===========================================================================
# 节点 ⑦ 调用本地 LLM 生成决策解释（仅解释，不参与决策）
# ===========================================================================
EXPLAIN_PROMPT = """你是无线网络节能调度助手。系统已经用确定性算法完成了一次调度决策，
下面是决策要点，请你用一句不超过 50 字的中文说明「为什么这样决策」。

要求：
1. 只讲判断逻辑（为什么能休眠 / 为什么不能休眠），不要罗列数字，不要重复扇区编号；
2. 不要给出新的建议，不要修改任何结论；
3. 直接输出这一句话，不要加"根据"之类的开头。

决策类型：{action_type}
决策要点：{summary}
"""


def _template_explanation(record) -> str:
    """
    纯代码生成的中文解释（LLM 不可用时的兜底）。

    这条路保证：即使 Ollama 没启动、模型没下载，决策链也完整可运行，
    解释文本仍然准确、可审计。
    """
    sleeping = record.get("sleeping_sectors", [])
    ratio = record.get("qos_verify", {}).get("unserved_ratio", 0.0) * 100
    saving = max(0.0, record.get("baseline_energy_kwh", 0.0) - record.get("step_energy_kwh", 0.0))
    if record.get("rollback"):
        return (
            f"{record['time']} 检测到{record['rollback_reason']}，已自动撤销本轮休眠并恢复全开配置，"
            f"优先保证用户接入不受影响。"
        )
    if record.get("action") == "abort_all_on":
        return (
            f"{record['time']} 最优休眠方案会使未服务用户比例达到 {ratio:.2f}%，"
            f"超过 {record['qos_constraint'] * 100:.0f}% 的服务阈值，因此放弃休眠、保持扇区全开。"
        )
    if not sleeping:
        return (
            f"{record['time']} 当前各扇区负载均不满足休眠条件（或休眠收益不足以抵消风险），"
            f"保持全开运行，无节能动作。"
        )
    return (
        f"{record['time']} 小区处于低负载时段，休眠 {len(sleeping)} 个低负载扇区"
        f"（{', '.join(sleeping)}），本周期节省约 {saving:.3f} kWh，"
        f"预计未服务用户比例 {ratio:.2f}%，低于 {record['qos_constraint'] * 100:.0f}% 的约束上限。"
    )


def _call_ollama(prompt: str, model: str, timeout: float = 60.0):
    """调用本地 Ollama 生成解释文本；失败返回 None（由调用方兜底）。"""
    try:
        import ollama
    except Exception:
        return None
    try:
        client = ollama.Client(host=OLLAMA_HOST, timeout=timeout)
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 160},
        )
        text = (resp.get("message") or {}).get("content", "").strip()
        return text or None
    except Exception:
        return None


def explain_with_llm(state: AgentState) -> Dict[str, Any]:
    """
    把结构化决策结果翻译成自然语言理由。

    重要边界：本节点**只读取**已经确定的决策结果，输出的是文字说明，
    不会写入 sector_states / energy_budget / decision_history，
    因此 LLM 的任何输出都不可能改变调度决策。
    """
    scratch = dict(state.get("scratch", {}))
    record = scratch.get("last_record", {})
    model = scratch.get("llm_model", DEFAULT_LLM_MODEL)

    # 先用代码把决策归类并生成要点摘要，模型只负责把要点"说人话"
    sleeping = record.get("sleeping_sectors", [])
    ratio = record.get("qos_verify", {}).get("unserved_ratio", 0.0) * 100
    limit = record.get("qos_constraint", 0.0) * 100

    if record.get("rollback"):
        action_type = "突发流量回滚"
        summary = "检测到话务突然上涨，已撤销本轮休眠并恢复扇区全开，优先保证用户接入"
    elif record.get("action") == "abort_all_on":
        action_type = "放弃休眠"
        summary = f"最优休眠方案会让未服务用户升到 {ratio:.2f}%，超过 {limit:.0f}% 的服务阈值，因此保持全开"
    elif not sleeping:
        action_type = "保持全开"
        summary = "当前各扇区负载都不满足休眠条件，休眠收益不足以抵消风险"
    else:
        action_type = "执行休眠"
        summary = (
            f"{len(sleeping)} 个扇区处于低负载且在线用户很少，邻区剩余容量足够吸收其用户，"
            f"预计未服务 {ratio:.2f}%，低于 {limit:.0f}% 阈值，因此可以休眠省电"
        )

    prompt = EXPLAIN_PROMPT.format(action_type=action_type, summary=summary)

    llm_text = _call_ollama(prompt, model) if scratch.get("use_llm", True) else None
    record["explanation"] = llm_text or _template_explanation(record)
    record["explanation_source"] = "ollama" if llm_text else "template"
    scratch["last_record"] = record
    return {"scratch": scratch}


# ===========================================================================
# 构建 LangGraph 决策图
# ===========================================================================
def build_graph(use_llm: bool = True, llm_model: str = DEFAULT_LLM_MODEL):
    """
    组装决策图并编译。

    参数：
        use_llm   : 是否启用 LLM 解释（False 时只走模板解释，用于纯离线/快速回归）
        llm_model : Ollama 模型名
    """
    builder = StateGraph(AgentState)

    builder.add_node("read_state", read_sector_state)
    builder.add_node("filter_candidates", filter_candidates)
    builder.add_node("score_combinations", score_combinations)
    builder.add_node("select_best", select_best)
    builder.add_node("qos_check", qos_check)
    builder.add_node("apply_decision", apply_decision)
    builder.add_node("abort_all_on", abort_all_on)
    builder.add_node("audit_and_rollback", audit_and_rollback)
    builder.add_node("explain", explain_with_llm)

    builder.add_edge(START, "read_state")
    builder.add_edge("read_state", "filter_candidates")
    builder.add_edge("filter_candidates", "score_combinations")
    builder.add_edge("score_combinations", "select_best")
    builder.add_edge("select_best", "qos_check")
    builder.add_conditional_edges(
        "qos_check", route_after_qos, {"apply": "apply_decision", "abort": "abort_all_on"}
    )
    builder.add_edge("apply_decision", "audit_and_rollback")
    builder.add_edge("abort_all_on", "audit_and_rollback")
    builder.add_edge("audit_and_rollback", "explain")
    builder.add_edge("explain", END)

    return builder.compile()


# ===========================================================================
# 批量运行：把 24 小时（或 7 天）的时序逐周期喂给 Agent
# ===========================================================================
def run_agent(csv_path: str, out_path: str, use_llm: bool = True, llm_model: str = DEFAULT_LLM_MODEL,
              explain_mode: str = "on_change"):
    """
    逐决策周期运行 Agent，导出逐周期结果。

    参数：
        explain_mode : every     —— 每个周期都调用 LLM 解释（最慢）
                       on_change —— 仅在休眠组合发生变化时调用 LLM（默认，平衡速度与可读性）
                       never     —— 不调用 LLM，只用模板解释（最快，用于回归测试）
    """
    sim = SectorSimulator(Path(csv_path))
    # 关键：把工具层的只读数据源显式指向同一份 CSV，
    # 否则工具会退回默认单日数据，回放多天数据时后续日期将读不到状态。
    configure_source(csv_path)
    app = build_graph(use_llm=use_llm, llm_model=llm_model)

    history: List[Dict[str, Any]] = []
    rows = []
    prev_signature = None

    for ts in sim.timestamps():
        # 是否本轮需要调用 LLM：由「休眠组合是否变化」决定
        need_llm = explain_mode == "every"
        state_in: AgentState = {
            "current_time": ts,
            "sector_states": {},
            "energy_budget": 0.0,
            "qos_constraint": QOS_CONSTRAINT_DEFAULT,
            "decision_history": history,
            "scratch": {"use_llm": need_llm, "llm_model": llm_model},
        }
        state_out = app.invoke(state_in)
        record = state_out["scratch"]["last_record"]

        signature = tuple(record["sleeping_sectors"])
        if explain_mode == "on_change" and signature != prev_signature:
            # 组合发生变化时，重跑一次带 LLM 的解释（决策结果已固定，重跑不会改变数值）
            state_in2: AgentState = dict(state_out)
            state_in2["scratch"] = dict(state_out["scratch"])
            state_in2["scratch"]["use_llm"] = True
            record = explain_with_llm(state_in2)["scratch"]["last_record"]
        prev_signature = signature

        history = state_out["decision_history"]
        rows.append(
            {
                "timestamp": record["time"],
                "action": record["action"],
                "sleeping_sectors": "|".join(record["sleeping_sectors"]),
                "sleeping_count": len(record["sleeping_sectors"]),
                "total_users": record["total_users"],
                "total_load_pct": record["total_load_pct"],
                "baseline_energy_kwh": record["baseline_energy_kwh"],
                "step_power_w": record["step_power_w"],
                "step_energy_kwh": record["step_energy_kwh"],
                "saving_kwh": round(max(0.0, record["baseline_energy_kwh"] - record["step_energy_kwh"]), 6),
                "unserved_ratio": record["qos_verify"].get("unserved_ratio", 0.0),
                "unserved_users": record["qos_verify"].get("unserved_users", 0.0),
                "score": record["score"],
                "rollback": record["rollback"],
                "qos_passed": record["qos_verify"].get("passed", False),
                "explanation_source": record.get("explanation_source", "template"),
                "explanation": record.get("explanation", "").replace("\n", " "),
            }
        )

        if len(rows) % 24 == 0:
            print(f"[进度] 已处理 {len(rows)} 个决策周期 ...")

    # --- 导出结果 ---
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total_energy = sum(r["step_energy_kwh"] for r in rows)
    total_baseline = sum(r["baseline_energy_kwh"] for r in rows)
    total_unserved = sum(r["unserved_users"] for r in rows)
    total_users = sum(r["total_users"] for r in rows)
    sleep_steps = sum(r["sleeping_count"] for r in rows)
    rollbacks = sum(1 for r in rows if r["rollback"])
    llm_count = sum(1 for r in rows if r["explanation_source"] == "ollama")

    stats = {
        "total_energy_kwh": round(total_energy, 4),
        "baseline_energy_kwh": round(total_baseline, 4),
        "energy_saving_pct": round((total_baseline - total_energy) / total_baseline * 100.0, 3),
        "weighted_unserved_ratio_pct": round(total_unserved / total_users * 100.0, 4) if total_users else 0.0,
        "sleep_sector_steps": sleep_steps,
        "rollback_count": rollbacks,
        "llm_explained_steps": llm_count,
        "steps": len(rows),
    }

    print(f"[OK] Agent 逐周期结果已写入: {out}")
    print(f"     总能耗 {total_energy:.3f} kWh（全开基准 {total_baseline:.3f} kWh）")
    print(f"     节能率 {stats['energy_saving_pct']:.2f}%   未服务率 {stats['weighted_unserved_ratio_pct']:.4f}%")
    print(f"     休眠扇区·周期数 {sleep_steps}   回滚次数 {rollbacks}   LLM 解释条数 {llm_count}")
    return stats, rows


def main():
    parser = argparse.ArgumentParser(description="运行小区扇区休眠节能调度 Agent")
    parser.add_argument("--csv", default=str(Path(__file__).parent / "sim" / "result" / "sector_traffic.csv"))
    parser.add_argument("--out", default=str(Path(__file__).parent / "sim" / "result" / "agent_result.csv"))
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--explain-mode", choices=["every", "on_change", "never"], default="on_change")
    parser.add_argument("--no-llm", action="store_true", help="完全禁用 LLM（仅用模板解释）")
    args = parser.parse_args()

    stats, _ = run_agent(
        csv_path=args.csv,
        out_path=args.out,
        use_llm=not args.no_llm,
        llm_model=args.llm_model,
        explain_mode="never" if args.no_llm else args.explain_mode,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
