"""
engine/ —— 板块②（B）：编译、打分、方案（关键路径）

职责（对齐分工表）：
  1. S0 规则编译与四类失败码（compiler.RuleCompiler）+ 失败语料（CompileCorpus）
  2. 五维打分器（ars_engine.ARSEngine）+ 唯一口径（rubric）
  3. 扣分解释器（explain.build_explains）—— ARS-2
  4. 改造方案器 / 报价 / 提升曲线 / 边际（plan.PlanGenerator）—— ARS-3 / ARS-4 / MT-4
  5. 抗博弈（antigaming）—— ARS-9
  6. 口径版本与 hash（rubric.rubric_hash）—— ARS-10
  7. 债务与预算（instrument）—— MT-2 / MT-3

对上游的两个端口（不 import ①③ 的任何实现）：
  signals.ClaimSignal  ← S3 LocatorResolver + S4/verifier 复算 + claim 级新鲜度
  signals.CheckSignal  ← S4 的同输入双跑一致性

对外只暴露 JSON 契约：
  ARSResult.to_payload()  → /api/ars
  Plan.to_payload()       → /api/plan
"""

from .antigaming import AntiGamingReport, evaluate as evaluate_antigaming
from .ars_engine import ARSEngine, CountBundle
from .compiler import CompileCorpus, RuleCompiler
from .explain import build_explains, top_explains
from .instrument import AcceptWithGrade, accept_with_grade, budget_snapshot, debt_summary
from .plan import Action, Plan, PlanGenerator
from .rubric import Counts, rubric_definition, rubric_hash, score_from_counts
from .signals import CaseSignals, CheckSignal, ClaimSignal

__all__ = [
    # S0
    "RuleCompiler",
    "CompileCorpus",
    # 口径
    "Counts",
    "score_from_counts",
    "rubric_hash",
    "rubric_definition",
    # 打分
    "ARSEngine",
    "CountBundle",
    # 解释
    "build_explains",
    "top_explains",
    # 方案
    "PlanGenerator",
    "Plan",
    "Action",
    # 抗博弈
    "evaluate_antigaming",
    "AntiGamingReport",
    # 仪表
    "debt_summary",
    "budget_snapshot",
    "accept_with_grade",
    "AcceptWithGrade",
    # 端口
    "ClaimSignal",
    "CheckSignal",
    "CaseSignals",
]
