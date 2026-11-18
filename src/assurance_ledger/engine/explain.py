"""
扣分解释器（ARS-2）—— 板块②（B）归属

报告里每个分项必须给三样东西：**一个数字、一句人话、一个动作指针**。

    可定位率 0.45：20 条结论里 9 条回源解析成功，11 条没有
    → 补 9 个定位（约 6.3 CU，预期 +11.25 分）

这段文案是产品化的关键：分数本身没人看，"差什么、多少钱"才有人看。
所以文案里不许出现"置信度""正确率"，也不许由 LLM 生成 —— 模板 + 计数，可复算。

验收口径（ARS PRD §6.2）：任一分项点开 ≤2 层可达具体条目列表。
这里给的是第 1 层（分项级）；具体条目列表由 ③/④ 用 claim_id 渲染。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.ars import ARSResult, Dim, DimStatus
from .rubric import ACTION_LIBRARY, ACTION_ORDER

#: 维度 → 主要负责的动作（用于"动作指针"）
DIM_ACTIONS: Dict[str, List[str]] = {
    "L": ["add_locator"],
    "C": ["rewrite_rule"],
    "R": ["add_field", "rerun_check"],
    "D": ["rewrite_rule"],       # 改写规则会新增 check，D 的分母随之变化
    "F": ["rerun_stale"],
}

#: 维度中文名（报告直接用）
DIM_NAMES: Dict[str, str] = {
    "L": "可定位率",
    "C": "可编译率",
    "R": "可复算率",
    "D": "确定性率",
    "F": "新鲜度",
}


def build_explains(result: ARSResult) -> List[Dict[str, Any]]:
    """
    生成五维解释，按"丢了多少分"降序

    报告只展示前 3 条（ARS PRD §9），但 JSON 里给全 5 条，面板可以展开。
    """
    explains = [_explain_dim(result, dim) for dim in result.dims.items()]
    explains.sort(key=lambda e: (-(e["lost_points"] or 0.0), e["dim"]))
    for idx, item in enumerate(explains, start=1):
        item["is_top3"] = idx <= 3 and (item["lost_points"] or 0) > 0
    return explains


def top_explains(result: ARSResult, n: int = 3) -> List[Dict[str, Any]]:
    return [e for e in (result.explains or build_explains(result)) if e["is_top3"]][:n]


# ============================================================
# 单维
# ============================================================
def _explain_dim(result: ARSResult, dim: Dim) -> Dict[str, Any]:
    name = DIM_NAMES.get(dim.key, dim.key)
    lost_points = None
    if dim.available:
        segment = [d for d in result.dims.items() if d.segment == dim.segment]
        active_weight = sum(d.weight for d in segment if d.status != DimStatus.NA)
        maximum = sum(d.weight for d in segment)
        lost_points = round(dim.weight * maximum / active_weight * (1 - dim.value), 4)

    if dim.status == DimStatus.MISSING:
        text = f"{name}（{dim.key}）数据未产出，不推断扣分；报告状态为 {result.doc_status.value}，不出总分。"
        return _payload(dim, name, text, None, None)

    if dim.status == DimStatus.NA:
        text = f"{name}（{dim.key}）本次不适用（分母为 0），权重在同段内重分配。"
        return _payload(dim, name, text, None, 0.0)

    num, den = dim.numerator or 0, dim.denominator or 0
    gap = den - num
    text = _sentence(dim.key, num, den, gap)

    action = _action_pointer(result, dim.key, gap)
    if action:
        text = f"{text} → {action['text']}"

    return _payload(dim, name, text, action, lost_points)


def _sentence(key: str, num: int, den: int, gap: int) -> str:
    if key == "L":
        return (
            f"{den} 条结论里 {num} 条回源解析成功（定位到 file:row:field 且值与源一致），"
            f"{gap} 条未解析成功"
        )
    if key == "C":
        return (
            f"{den} 条规则里 {num} 条编译通过且 fixture 三条全绿，"
            f"{gap} 条编不出来或 fixture 没跑绿"
        )
    if key == "R":
        return (
            f"{den} 条结论里 {num} 条经独立 verifier 复算一致，"
            f"{gap} 条不一致或无法判定"
        )
    if key == "D":
        return (
            f"{den} 个检查里 {num} 个同输入两次运行结果一致，"
            f"{gap} 个不一致或未双跑"
        )
    if key == "F":
        return (
            f"{den} 条结论里 {num} 条的全部证据都是 fresh，"
            f"{gap} 条存在过期证据"
        )
    return f"{key}：{num}/{den}"


def _action_pointer(result: ARSResult, key: str, gap: int) -> Optional[Dict[str, Any]]:
    """把"缺口"翻成"动作 + 报价"，只报该维真正可执行的那部分"""
    kinds = [k for k in ACTION_ORDER if k in DIM_ACTIONS.get(key, [])]
    picked: List[Dict[str, Any]] = []
    total_n = 0
    total_cu = 0.0
    for kind in kinds:
        n = int(result.actionable.get(kind, 0) or 0)
        if n <= 0:
            continue
        spec = ACTION_LIBRARY[kind]
        cu = n * float(spec["cu"])
        picked.append({"kind": kind, "label": spec["label"], "n": n, "cu": round(cu, 2)})
        total_n += n
        total_cu += cu

    if not picked:
        return None

    if len(picked) == 1:
        only = picked[0]
        text = f"{only['label']} {only['n']} 条（{only['cu']} CU）"
    else:
        parts = " + ".join(f"{p['label']} {p['n']} 条（{p['cu']} CU）" for p in picked)
        text = f"{parts}，合计 {round(total_cu, 1)} CU"

    return {
        "text": text,
        "kinds": [p["kind"] for p in picked],
        "n": total_n,
        "cu": round(total_cu, 2),
    }


def _payload(
    dim: Dim,
    name: str,
    text: str,
    action: Optional[Dict[str, Any]],
    lost_points: Optional[float],
) -> Dict[str, Any]:
    return {
        "dim": dim.key,
        "name": name,
        "status": dim.status.value,
        "numerator": dim.numerator,
        "denominator": dim.denominator,
        "value": None if dim.value is None else round(dim.value, 4),
        "weight": dim.weight,
        "points": None if dim.points is None else round(dim.points, 2),
        "lost_points": None if lost_points is None else round(lost_points, 2),
        "text": text,
        "action": action,
    }
