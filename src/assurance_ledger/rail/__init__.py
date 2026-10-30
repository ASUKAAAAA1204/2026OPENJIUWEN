"""
rail/ —— A 管：采集、缓存、成本

对接 openJiuwen Rail 的 ℋ_call / ℋ_task 钩子。
职责：
  1. 采集事件流（S2）
  2. 计算成本（CU）
  3. 缓存与去重（避免双写）
"""

from .collector import RailCollector
from .cost import CostCalculator
from .cache import EventCache

__all__ = ["RailCollector", "CostCalculator", "EventCache"]
