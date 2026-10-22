#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sim/compare_and_plot.py
============================================================
三策略对比与图表导出
------------------------------------------------------------
读取三组策略的逐周期结果，在同一口径下计算指标并导出：

    输入：
        sim/result/baseline_result.csv     （全开 / 随机休眠 逐周期明细）
        sim/result/agent_result.csv        （Agent 智能休眠 逐周期明细）

    输出：
        sim/result/strategy_comparison.csv 三策略横向对比表
        sim/result/fig_energy_comparison.png  24h 功率曲线对比
        sim/result/fig_qos_comparison.png     未服务率对比
        sim/result/fig_sleep_heatmap.png      扇区×时段 休眠频次热力图

指标口径（三组完全一致）：
    total_energy_kwh        = Σ 每周期能耗
    energy_saving_pct       = (E_全开 − E_策略) / E_全开 × 100%
    weighted_unserved_pct   = Σ未服务用户 / Σ在线用户 × 100%
    peak_unserved_pct       = 单周期未服务比例的最大值

依赖 matplotlib（可选）：若未安装，仅导出 CSV，不导出图表。
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULT_DIR = Path(__file__).parent / "result"
BASELINE_CSV = RESULT_DIR / "baseline_result.csv"
AGENT_CSV = RESULT_DIR / "agent_result.csv"
OUT_CSV = RESULT_DIR / "strategy_comparison.csv"


def read_csv(path: Path):
    """读取 CSV 为字典列表。"""
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def summarize(rows, label, description):
    """按统一口径汇总一组策略的指标。"""
    total_energy = sum(float(r["step_energy_kwh"]) for r in rows)
    # 兼容两种列名：基线用 step_unserved_users，Agent 结果用 unserved_users
    unserved = sum(
        float(r.get("step_unserved_users") or r.get("unserved_users") or 0.0) for r in rows
    )
    users = sum(float(r.get("total_users", 0) or 0) for r in rows)
    peak = max((float(r.get("step_unserved_ratio") or r.get("unserved_ratio") or 0.0) for r in rows), default=0.0)
    sleep_steps = sum(
        len([x for x in (r.get("sleeping_sectors") or "").split("|") if x]) for r in rows
    )
    return {
        "strategy": label,
        "description": description,
        "total_energy_kwh": round(total_energy, 4),
        "weighted_unserved_pct": round(unserved / users * 100.0, 4) if users else 0.0,
        "peak_unserved_pct": round(peak * 100.0, 3),
        "sleep_sector_steps": sleep_steps,
        "_rows": rows,
    }


def setup_chinese_font():
    """尝试启用中文字体；失败则退回英文标签。"""
    try:
        import matplotlib
        from matplotlib import font_manager

        candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC"]
        available = {f.name for f in font_manager.fontManager.ttflist}
        for name in candidates:
            if name in available:
                matplotlib.rcParams["font.sans-serif"] = [name]
                matplotlib.rcParams["axes.unicode_minus"] = False
                return True
    except Exception:
        pass
    return False


def plot_all(strategies, zh: bool):
    """绘制三张对比图。"""
    import matplotlib
    matplotlib.use("Agg")  # 无界面后端，适合服务器/CPU 环境
    import matplotlib.pyplot as plt

    t = (lambda cn, en: cn if zh else en)

    always = strategies[0]["_rows"]
    random_rows = strategies[1]["_rows"]
    agent = strategies[2]["_rows"]

    # ---------- 图 1：24h 功率曲线 ----------
    fig, ax = plt.subplots(figsize=(11, 5))
    x = list(range(len(always)))
    ax.plot(x, [float(r["step_power_w"]) for r in always],
            label=t("全开基线", "Always-on baseline"), linewidth=1.8, color="#888888")
    ax.plot(x, [float(r["step_power_w"]) for r in random_rows],
            label=t("随机休眠", "Random sleep"), linewidth=1.2, linestyle="--", color="#e69f00")
    ax.plot(x, [float(r["step_power_w"]) for r in agent],
            label=t("Agent 智能休眠", "Agent sleep"), linewidth=1.8, color="#0072b2")

    # 标注 Agent 的休眠时段（阴影）
    sleep_x = [i for i, r in enumerate(agent) if int(r["sleeping_count"]) > 0]
    for i in sleep_x:
        ax.axvspan(i - 0.5, i + 0.5, color="#0072b2", alpha=0.06, linewidth=0)

    ax.set_xlabel(t("决策周期（15 分钟/步，共 96 步）", "Decision step (15 min each, 96 steps)"))
    ax.set_ylabel(t("小区总功率（W）", "Cell total power (W)"))
    ax.set_title(t("24 小时小区总功率对比（阴影为 Agent 休眠时段）",
                   "24h cell power comparison (shaded = Agent sleep)"))
    ax.legend()
    ax.grid(alpha=0.25)
    # 每 8 步（2 小时）一个刻度
    ax.set_xticks(list(range(0, len(always), 8)))
    ax.set_xticklabels([f"{i // 4:02d}:00" for i in range(0, len(always), 8)], fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig_energy_comparison.png", dpi=150)
    plt.close(fig)

    # ---------- 图 2：未服务率对比 ----------
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(x, [float(r.get("step_unserved_ratio", 0.0) or 0.0) * 100 for r in random_rows],
            label=t("随机休眠", "Random sleep"), linewidth=1.2, color="#e69f00")
    ax.plot(x, [float(r.get("step_unserved_ratio", 0.0) or 0.0) * 100 for r in agent],
            label=t("Agent 智能休眠", "Agent sleep"), linewidth=1.8, color="#0072b2")
    ax.axhline(1.0, color="#d55e00", linestyle=":", linewidth=1.6,
               label=t("QoS 约束上限 1%", "QoS limit 1%"))
    ax.set_xlabel(t("决策周期（15 分钟/步）", "Decision step (15 min)"))
    ax.set_ylabel(t("未服务用户比例（%）", "Unserved user ratio (%)"))
    ax.set_title(t("用户未服务比例对比（越低越好，不超过 1% 红线）",
                   "Unserved user ratio comparison (must stay under 1%)"))
    ax.legend()
    ax.grid(alpha=0.25)
    ax.set_xticks(list(range(0, len(always), 8)))
    ax.set_xticklabels([f"{i // 4:02d}:00" for i in range(0, len(always), 8)], fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig_qos_comparison.png", dpi=150)
    plt.close(fig)

    # ---------- 图 3：扇区 × 时段 休眠频次热力图 ----------
    sector_ids = sorted({s for r in agent for s in (r["sleeping_sectors"] or "").split("|") if s})
    if sector_ids:
        # 按时段（小时）聚合，统计每个扇区在该小时被休眠的周期数
        mat = [[0] * 24 for _ in sector_ids]
        for r in agent:
            hour = int(r["timestamp"][11:13])
            for s in (r["sleeping_sectors"] or "").split("|"):
                if s in sector_ids:
                    mat[sector_ids.index(s)][hour] += 1

        fig, ax = plt.subplots(figsize=(11, 2.6))
        im = ax.imshow(mat, aspect="auto", cmap="Blues")
        ax.set_yticks(range(len(sector_ids)))
        ax.set_yticklabels(sector_ids, fontsize=8)
        ax.set_xticks(range(0, 24, 2))
        ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 2)], fontsize=8)
        ax.set_xlabel(t("小时", "Hour"))
        ax.set_title(t("Agent 休眠决策分布（扇区 × 小时，颜色越深休眠越多）",
                       "Agent sleep decision distribution (sector x hour)"))
        fig.colorbar(im, ax=ax, label=t("休眠周期数", "sleep steps"))
        fig.tight_layout()
        fig.savefig(RESULT_DIR / "fig_sleep_heatmap.png", dpi=150)
        plt.close(fig)

    print(f"[OK] 图表已导出到 {RESULT_DIR}")


def main():
    if not BASELINE_CSV.exists():
        raise SystemExit(f"未找到 {BASELINE_CSV}，请先运行 python sim/baseline_runner.py")
    if not AGENT_CSV.exists():
        raise SystemExit(f"未找到 {AGENT_CSV}，请先运行 python graph.py")

    baseline_rows = read_csv(BASELINE_CSV)
    always_rows = [r for r in baseline_rows if r["strategy"] == "always_on"]
    random_rows = [r for r in baseline_rows if r["strategy"] == "random_sleep"]
    agent_rows = read_csv(AGENT_CSV)

    always = summarize(always_rows, "always_on", "全开基线：所有扇区全天常开")
    random_s = summarize(random_rows, "random_sleep", "随机基线：候选中随机 50% 休眠（不校验 QoS）")
    agent = summarize(agent_rows, "agent_sleep", "Agent 智能休眠：加权多目标择优 + QoS 硬约束 + 突发回滚")

    # 统一计算节能率（以全开基线为分母）
    base_energy = always["total_energy_kwh"]
    for s in (always, random_s, agent):
        s["energy_saving_pct"] = round((base_energy - s["total_energy_kwh"]) / base_energy * 100.0, 3)

    # --- 导出对比表 ---
    fields = [
        "strategy", "description", "total_energy_kwh", "energy_saving_pct",
        "weighted_unserved_pct", "peak_unserved_pct", "sleep_sector_steps",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for s in (always, random_s, agent):
            writer.writerow(s)

    # --- 控制台输出 ---
    print(f"{'策略':<14}{'总能耗(kWh)':>14}{'节能率':>10}{'未服务率':>12}{'峰值未服务':>12}{'休眠扇区·周期':>14}")
    print("-" * 78)
    for s in (always, random_s, agent):
        print(
            f"{s['strategy']:<14}{s['total_energy_kwh']:>14.3f}{s['energy_saving_pct']:>9.2f}%"
            f"{s['weighted_unserved_pct']:>11.4f}%{s['peak_unserved_pct']:>11.3f}%"
            f"{s['sleep_sector_steps']:>14d}"
        )
    print(f"\n[OK] 对比表已写入: {OUT_CSV}")

    # --- 图表 ---
    try:
        zh = setup_chinese_font()
        plot_all([always, random_s, agent], zh)
    except ImportError:
        print("[提示] 未安装 matplotlib，跳过图表导出（CSV 已生成）")
    except Exception as exc:
        print(f"[提示] 图表导出失败（{exc}），CSV 已生成，不影响数据结论")


if __name__ == "__main__":
    main()
