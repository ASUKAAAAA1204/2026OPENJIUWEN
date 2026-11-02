# 保证账本（Assurance Ledger）


本仓库是 openJiuwen 智能体创新大赛参赛项目。主载体为 openJiuwen Agent Framework（Rail）与 Agent Studio。

## 当前状态

| 项 | 状态 |
|---|---|
| 规格 | 已冻结：主 PRD v2.2 / ARS v1.2 / 链路 v1.2 |
| 数字链 | 47 → 79（34 CU，0.95 分/CU），全部可由整数计数复算 |
| 代码 | ②编译与评分模块可独立运行；①③仍有骨架，④未实现，**尚未完成端到端演示** |
| 测试 | `tests/` 已加入 B 模块回归；详见 [核查记录](docs/review/DSH_B_review_2026-11-01.md) |
| UI | 仅有 `ui/package.json`，`ui/src` 为空 |
| 时间线 | 创意提交 11-10 / DEMO 11-25 → 12-27 24:00 / 评审 12-28 |

## 快速开始

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/b_demo.py --output outputs/b_review/report.json
python scripts/verify_b_report.py outputs/b_review/report.json
python scripts/generate_sbom.py
```

`b_demo.py` 使用仓库内的合成信号，只验证评分与方案投影；输出明确标记未执行真实改造。
`verify_b_report.py` 仅用 Python 标准库独立复算，不能验证回源证据或真实成本。
Windows 沙箱若无法创建系统临时目录，可加 `--basetemp outputs/pytest-b-review` 运行 pytest。

## 目录结构

```
docs/
  prd/          # 三份现役 PRD（主 / ARS / 链路）+ ICT 赛题特调
  design/       # 设计与评审文档、应用实例清单、设计速查
  plan/         # 整改计划、调整方案、增强清单
  reference/    # 大赛 FAQ
  archive/      # 旧版 PRD 草稿
src/assurance_ledger/
  core/         # pydantic 数据模型（claim / evidence / check / ars / cost / ledger）
  schemas/      # 接口契约 C1–C7 校验
  rail/         # S2 采集与成本
  engine/       # S0 规则编译、ARS 打分、改造方案
  ledger/       # S3 证据包、S4 检查、S5 账本与 verify
scripts/        # SBOM 等工具
tests/          # B 模块回归与合成输入
ui/             # Studio 面板（待补）
```

## 核心口径（一页版）

| 段 | 何时 | 算哪几项 | 作用 |
|---|---|---|---|
| ARS_d 设计态 | S0/S1 之后，跑之前 | C 可编译率 | 设计预警（`C < 0.5` 提示，不阻断） |
| ARS_r 运行态 | S4/S5 之后 | L 可定位 / R 可复算 / D 确定性 / F 新鲜度 | 发布闸（ARS < 70 阻断） |
| ARS_g 治理态 | 改造后重测 | 全部 | 提升曲线 |

```text
ARS（总）= 0.25C + 0.25L + 0.20R + 0.15D + 0.15F

改造前：L 9/20  C 3/10   R 8/20   D 6/10   F 15/20 → 47（D 档）
改造后：L 18/20 C 7/10   R 16/20  D 10/14  F 17/20 → 79（B 档）
报价：34 CU（含 5% 缓冲），增益 32.46 分，0.95 分/CU
```

评分坐标必须写全：`skill@version · model · data_profile`，否则分数不可比。

## 文档入口

- [主 PRD](docs/prd/PRD_主_保证账本-v2.2.md) — 产品对象、两段 ARS、闸门、指标、人日
- [ARS 子 PRD](docs/prd/PRD_审计就绪度ARS-v1.2.md) — 五维定义、归一化、抗博弈、复算示例
- [链路子 PRD](docs/prd/PRD_可信可审计链路-v1.2.md) — S0–S7、契约 C1–C7
- [应用实例清单](docs/design/应用实例清单_2026-10-30.md) — U1–U7 场景走查
- [设计速查](docs/design/design_cheatsheet.md) — 队友上手用
- [整改计划](docs/plan/整改计划_v2.2_2026-10-30.md) — 当前口径的来由与代码迁移清单
- [进展记录](docs/PROGRESS.md)

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
