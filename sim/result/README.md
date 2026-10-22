# sim/result —— 结果数据说明

本目录保存仿真数据、基线结果、Agent 决策结果与对比图表。
所有文件均由固定随机种子生成，可完全复现。

## 文件清单

| 文件 | 生成脚本 | 内容 |
|---|---|---|
| `sector_traffic.csv` | `sim/traffic_generator.py` | 合成流量原始数据（96 步 × 6 扇区 = 576 条） |
| `baseline_result.csv` | `sim/baseline_runner.py` | 全开 / 随机休眠 两条基线的逐周期明细 |
| `baseline_comparison.csv` | `sim/baseline_runner.py` | 两条基线汇总对比 |
| `agent_result.csv` | `graph.py --no-llm` | Agent 逐周期决策结果（模板解释，快速回归用） |
| `agent_result_llm.csv` | `graph.py --explain-mode on_change` | Agent 逐周期决策结果（含 Ollama 生成解释） |
| `strategy_comparison.csv` | `sim/compare_and_plot.py` | 三策略横向对比（申报引用主表） |
| `fig_energy_comparison.png` | `sim/compare_and_plot.py` | 24h 小区总功率对比曲线 |
| `fig_qos_comparison.png` | `sim/compare_and_plot.py` | 未服务用户比例对比曲线 |
| `fig_sleep_heatmap.png` | `sim/compare_and_plot.py` | 扇区 × 小时 休眠频次热力图 |
| `sector_traffic_7d.csv` | `sim/traffic_generator.py --days 7` | 7 天扩展流量数据（672 步 × 6 扇区） |
| `agent_result_7d.csv` | `graph.py --csv ... --no-llm` | 7 天连续回放的 Agent 决策结果（跨天稳定性验证） |

## 指标口径

| 指标 | 定义 |
|---|---|
| `total_energy_kwh` | Σ 每个决策周期能耗（功率 × 0.25 h / 1000） |
| `energy_saving_pct` | (全开能耗 − 策略能耗) / 全开能耗 × 100% |
| `weighted_unserved_pct` | Σ 未服务用户 / Σ 在线用户 × 100%（按时段加权，避免夜间低话务时段权重过高） |
| `peak_unserved_pct` | 单周期未服务比例的最大值 |
| `sleep_sector_steps` | Σ 每周期休眠扇区数（"扇区·周期"计数单位） |

## 重要说明

1. 所有数值基于**合成数据**与**简化功耗模型**，仅用于验证决策链路正确性，**不代表真实网络节能幅度**。
2. 未服务用户数为**模型估算值**，非实测值；试验网阶段应以真实 KPI 校准。
3. `agent_result.csv` 与 `agent_result_llm.csv` 的**决策结果完全一致**，区别仅在于解释文本来源（模板 / Ollama），可用于交叉验证"LLM 不影响决策"这一设计约束。
