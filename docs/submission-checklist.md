# 申报材料清单（Submission Checklist）

- **申报计划**：紫金山实验室 Pre6G 试验网 · 开发者接入计划
- **项目名称**：基于 LangGraph 的小区扇区自主休眠节能调度 Agent
- **文档版本**：v1.0
- **编制日期**：2026-10-21

---

## 使用说明

本清单用于**逐项核对**申报材料是否齐备。提交前请将「状态」列勾选为 ✅，并在「备注」中补充实际链接或说明。

---

## 一、开发者身份材料

| 编号 | 材料 | 要求 | 状态 | 备注 |
|---|---|---|---|---|
| A1 | 开发者个人实名认证资料 | 姓名、身份证件、联系方式，与申报主体一致 | ⬜ | 姓名 周恒昌；证件号与手机号以私密附件另附，不写入公开仓库 |
| A2 | 单位/组织证明（如适用） | 在职证明、学生证或营业执照 | ✅ | 个人开发者，无需单位证明 |
| A3 | 联系方式确认 | 手机、邮箱，保持畅通 | ⬜ | 手机与邮箱以私密附件另附，不写入公开仓库 |

> 说明：实名信息仅用于试验网接入审核，不随代码仓库公开。

---

## 二、三份正式申报文档

| 编号 | 材料 | 文件路径 | 状态 |
|---|---|---|---|
| B1 | 项目描述 | `docs/project-description.md` | ✅ |
| B2 | 技术方案 | `docs/technical-proposal.md` | ✅ |
| B3 | 验证计划 | `docs/validation-plan.md` | ✅ |
| B4 | 本材料清单 | `docs/submission-checklist.md` | ✅ |

**三份文档的共同要点（审核关注项）**：

- [x] 明确写出「不控制任何真实基站硬件」
- [x] 明确写出「仅申请只读流量数据接口 + 模拟决策回传接口」
- [x] 明确写出「所有休眠指令只做记录、不下发至真实设备」
- [x] 明确写出「全部运算在本地 CPU 完成，无需云端大模型集群」
- [x] 明确写出「仅使用仿真合成数据，不接入运营商现网」

---

## 三、开源代码仓库

| 编号 | 材料 | 说明 | 状态 |
|---|---|---|---|
| C1 | GitHub 仓库链接 | `https://github.com/zhouhengchang/network-energy-agent`（公开仓库，main 分支） | ✅ |
| C2 | 开源协议 | Apache-2.0（`LICENSE`） | ✅ |
| C3 | README（含一键运行步骤） | `README.md` | ✅ |
| C4 | 依赖清单 | `requirements.txt` | ✅ |
| C5 | 代码可运行性自证 | 仓库内提供复现命令与预期输出 | ✅ |

### 仓库结构核对

```
network-energy-agent/
├── README.md                 # 项目简介、架构图、一键运行、申报状态
├── graph.py                  # LangGraph Agent 主逻辑
├── requirements.txt          # 依赖清单
├── LICENSE                   # Apache-2.0
├── tools/
│   ├── sector_state.py       # 扇区状态读取工具
│   ├── energy_estimator.py   # 节能收益估算
│   └── qos_estimator.py      # QoS 影响评估
├── sim/
│   ├── traffic_generator.py  # 合成流量生成
│   ├── baseline_runner.py    # 基线策略运行（全开 / 随机）
│   ├── qellaris_fallback.py  # 第三方仿真包适配兜底
│   └── result/               # 基线 + Agent 对比 CSV 与图表
└── docs/
    ├── project-description.md
    ├── technical-proposal.md
    ├── validation-plan.md
    └── submission-checklist.md
```

---

## 四、仿真对照数据与结果

| 编号 | 材料 | 文件 | 状态 |
|---|---|---|---|
| D1 | 合成流量原始数据 | `sim/result/sector_traffic.csv` | ✅ |
| D2 | 基线逐周期明细 | `sim/result/baseline_result.csv` | ✅ |
| D3 | 基线汇总对比 | `sim/result/baseline_comparison.csv` | ✅ |
| D4 | Agent 逐周期决策结果 | `sim/result/agent_result.csv` | ✅ |
| D5 | 三策略横向对比表 | `sim/result/strategy_comparison.csv` | ✅ |
| D6 | 能耗对比图 | `sim/result/fig_energy_comparison.png` | ✅ |
| D7 | QoS 对比图 | `sim/result/fig_qos_comparison.png` | ✅ |
| D8 | 决策分布图（扇区×时段休眠频次） | `sim/result/fig_sleep_heatmap.png` | ✅ |

### 数据可信性说明

- 全部数据由**固定随机种子**生成，任何人可复现；
- 三条策略使用**同一份输入数据**，对照口径一致；
- 能耗按同一功耗模型折算，QoS 按同一评估模型计算。

---

## 五、联系人与试验窗口

| 编号 | 材料 | 内容 | 状态 |
|---|---|---|---|
| E1 | 联系人姓名 | 周恒昌 | ✅ |
| E2 | 联系电话 / 邮箱 | 以私密附件另附（公开仓库不展示手机号与邮箱） | ⬜ |
| E3 | 期望试验时间窗口 | 申报通过后 2~4 周内启动，为期 7 天连续运行 | ⬜ |
| E4 | 期望试验场景 | 单小区多扇区（3~6 扇区），夜间话务回落明显区域 | ⬜ |
| E5 | 资源申请项 | 见《验证计划》§5：R1~R5（均为只读或本地资源） | ⬜ |

---

## 六、安全承诺（须随材料一并提交）

> **安全承诺备注**
>
> 本项目在试验网期间严格遵守以下承诺：
>
> 1. **全程仅读取网络 KPI 数据**：只申请小区/扇区级别的负载（PRB 利用率）与在线用户数只读接口，不申请任何用户级数据、信令内容或用户面报文；
> 2. **只做模拟决策记录**：Agent 输出的所有"休眠/保持全开"决策，仅写入本地结果文件与日志服务器，**不向任何真实基站、网元或网管系统下发配置指令**；
> 3. **无设备控制权限**：不申请基站配置下发、扇区关断、小区闭塞、网元管理面登录（SSH/SNMP Set/NETCONF 写）等任何写权限，项目中不存在任何面向网元的写通道；
> 4. **数据最小化**：不采集、不存储任何可识别到个人的信息，落盘数据仅为扇区级聚合统计量；
> 5. **数据安全**：试验数据仅在内网闭环流转，保留期不超过试验周期 + 30 天，到期删除；不用于本项目之外的任何用途；
> 6. **可审计**：每一条决策都保留完整的计算依据（负载、节电量、未服务比例、约束校验结果），审核方可在任意时刻要求复算。
>
> 如违反上述承诺，愿承担相应责任。

| 项 | 内容 |
|---|---|
| 承诺人 | 周恒昌 |
| 日期 | 2026 年 10 月 22 日 |
| 签字/电子签名 | 周恒昌（电子签名） |

---

## 七、提交前最终自检

| 序号 | 检查项 | 状态 |
|---|---|---|
| 1 | 三份文档的安全边界描述一致，无前后矛盾 | ✅ |
| 2 | 仓库内无硬编码密钥、账号、Token、内网地址 | ✅ |
| 3 | 仓库内无任何网元写操作代码（无 POST/PUT/SNMP/NETCONF） | ✅ |
| 4 | 数据文件中不含任何用户标识信息 | ✅ |
| 5 | README 中的一键运行命令已在干净环境验证通过 | ✅ |
| 6 | 所有指标的计算口径在文档中有明确定义 | ✅ |
| 7 | 模型估算值与实测值明确区分，未混用 | ✅ |
| 8 | 实名材料与申报主体一致 | ⬜ |
| 9 | 联系方式有效、可及时响应 | ⬜ |
| 10 | 开源仓库已设置为公开可访问 | ⬜ |

---

## 八、材料打包建议

```
Pre6G申报_NetworkEnergyAgent_<姓名>_<日期>/
├── 01_身份材料/
│   └── 实名认证资料.pdf
├── 02_申报文档/
│   ├── project-description.md
│   ├── technical-proposal.md
│   ├── validation-plan.md
│   └── submission-checklist.md
├── 03_开源仓库/
│   └── repository-link.txt        # 仓库地址 + commit hash
├── 04_仿真数据/
│   ├── sector_traffic.csv
│   ├── baseline_comparison.csv
│   ├── strategy_comparison.csv
│   └── figs/
│       ├── fig_energy_comparison.png
│       ├── fig_qos_comparison.png
│       └── fig_sleep_heatmap.png
└── 05_安全承诺/
    └── 安全承诺书（签字版）.pdf
```

> 建议同时提供 GitHub 仓库的 **commit hash**（而非仅分支名），确保审核方看到的代码版本与申报材料完全对应。
