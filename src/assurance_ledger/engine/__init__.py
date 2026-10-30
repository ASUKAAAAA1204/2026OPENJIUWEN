"""
engine/ —— B 管：编译、打分、方案

职责：
  1. S0 规则编译（RuleCompiler）
  2. ARS_d / ARS_r / ARS_g 打分（ARSEngine）
  3. 改造方案生成（PlanGenerator）—— 贪心，分/CU 降序
  4. S1 fixture 生成（FixtureGenerator）
"""

from .compiler import RuleCompiler
from .ars_engine import ARSEngine
from .plan import PlanGenerator

__all__ = ["RuleCompiler", "ARSEngine", "PlanGenerator"]
