"""
S0 规则编译器 —— 为 ARS 的 C 项供数 —— 板块②（B）归属

职责只有一个：把规则文本判成「可编译的谓词」或「四类失败码之一」，
并让每一次失败都落进语料库（ARS-6）——语料是这个项目唯一会自己长大的资产。

四类失败码（判定顺序 = 先致命、后可恢复）：

    1. SUBJECTIVE_RULE   含"合理/适当/良好"等不可判词 —— 最致命，花钱也改不回客观
    2. NO_LOCATOR        引用的事实没有可定位字段
    3. MISSING_FIELD     规则要求的输入不存在（含两侧字段任一未声明）
    4. NON_DETERMINISTIC 判定依赖 LLM 或随机，或确定性语法但编不出受支持的简单比较 IR

只有**整条**简单比较表达式（字段 运算符 字段/数值[%]）可判为可编译；
不做子串匹配（"PE < 1 and PB > 5" 整体判 NON_DETERMINISTIC），也没有
predicate 兜底 IR —— compilable=True 必带 type=cmp 的结构化 IR。

纪律（链路 PRD §S0）：
  - 判定必须可复现：同一输入两次判定结果一致（用 LLM 抽取时温度 0 + 缓存）；
  - 人工确认环节不可省：LLM 给候选，人勾选；
  - 失败也要落库。

注意：本模块只做 S0 的判定，不做 S4 的求值 —— 表达式求值、回源重算是 ③ 板块的事。
这里产出的 IR 是"谓词候选"，S4 可以细化它，但不许绕过它。
"""

from __future__ import annotations

import re
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..core.rule import FIXTURES_PER_CHECK, FixtureResult, RuleCompileResult, RuleSet
from ..core.types import CompileFailCode

# ============================================================
# 判定依据
# ============================================================
#: 不可判词 —— SUBJECTIVE_RULE 的触发依据（中英各一套）
SUBJECTIVE_TERMS: tuple = (
    r"合理", r"适当", r"良好", r"充分", r"足够", r"相当", r"酌情", r"酌情考虑",
    r"一定程度上", r"大致", r"较好", r"较差", r"建议关注", r"看好",
    r"reasonable", r"appropriate", r"sufficient", r"adequate", r"good", r"well",
    r"fair", r"satisfactory",
)
SUBJECTIVE_PATTERN = re.compile("(" + "|".join(SUBJECTIVE_TERMS) + ")", re.IGNORECASE)

#: 依赖模型的判定词 —— NON_DETERMINISTIC 的文本线索
LLM_TERMS: tuple = (
    r"由模型判断", r"模型判断", r"交给\s*LLM", r"LLM\s*判断", r"大模型", r"语义判断",
    r"主观评估", r"综合判断", r"打分", r"llm", r"gpt",
)
LLM_PATTERN = re.compile("(" + "|".join(LLM_TERMS) + ")", re.IGNORECASE)

#: 简单比较谓词的形态：左值 运算符 右值（**必须整式匹配**，不允许子串命中：
#: "PE < 1 and PB > 5" 整式不匹配 → NON_DETERMINISTIC，而不是从子串里抠出 PE < 1）
_CMP_RE = re.compile(
    r"(?P<left>[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5.]*)"
    r"\s*(?P<op>>=|<=|==|!=|≥|≤|=|>|<)"
    r"\s*(?P<right>[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5.]*|-?\d+(?:\.\d+)?%?)"
)

_OP_NORMALIZE = {"≥": ">=", "≤": "<=", "=": "=="}

#: cmp IR 里允许出现的运算符（引擎侧闸门与编译共用）
SUPPORTED_OPS = frozenset((">=", "<=", "==", "!=", ">", "<"))

#: fixture kind 词表：每个规则最多三条，恰好 positive / negative / boundary 各一条
FIXTURE_KINDS = ("positive", "negative", "boundary")


def is_supported_cmp_ir(ir: Any) -> bool:
    """
    受支持的可求值 IR 判定 —— compilable=True 的结果必须带这样的 IR 才计入 C 分子。

    type=cmp、op ∈ SUPPORTED_OPS、左值是字段、右值是字段或数值常量；
    predicate 兜底 IR / None / 未知 op 一律不算数（引擎侧闸门，见 ars_engine）。
    """
    if not isinstance(ir, Mapping):
        return False
    if ir.get("type") != "cmp":
        return False
    if ir.get("op") not in SUPPORTED_OPS:
        return False
    left = ir.get("left")
    right = ir.get("right")
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    if set(left) != {"field"} or set(right) not in ({"field"}, {"const"}):
        return False
    left_field = left.get("field")
    if not isinstance(left_field, str) or not left_field.strip():
        return False
    if "field" in right:
        right_field = right.get("field")
        return isinstance(right_field, str) and bool(right_field.strip())
    const = right.get("const")
    return isinstance(const, (int, float)) and not isinstance(const, bool) and math.isfinite(const)


def fixtures_eligible(fixtures: Iterable[FixtureResult]) -> bool:
    """
    引擎侧 fixture 闸门：正好 3 条、全部通过、kind 恰好 positive/negative/boundary 各一条。

    外部直接构造的 RuleSet 不经 _coerce_fixtures，也必须在引擎这里过闸，
    否则"三条 positive 全绿"也能把 C 分子做上去。
    """
    items = list(fixtures)
    if len(items) != FIXTURES_PER_CHECK:
        return False
    if {f.kind for f in items} != set(FIXTURE_KINDS):
        return False
    return all(f.passed is True for f in items)


class CompileCorpus:
    """
    失败语料库（ARS-6）—— 只增不删

    每条：{skill_version, rule_id, code, snippet, at}
    验收口径：能回答"哪类失败最多"，并能在改造后展示语料增量。
    """

    def __init__(self) -> None:
        self._items: List[Dict[str, Any]] = []

    def add(self, skill_version: str, rule_id: str, code: str, snippet: Optional[str]) -> None:
        self._items.append(
            {
                "skill_version": skill_version,
                "rule_id": rule_id,
                "code": code,
                "snippet": (snippet or "")[:120],
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def extend_from(self, rule_set: RuleSet) -> None:
        for r in rule_set.results:
            if not r.compilable and r.fail_code is not None:
                self.add(rule_set.skill_version, r.rule_id, r.fail_code.value, r.fail_snippet)

    def items(self) -> List[Dict[str, Any]]:
        return list(self._items)

    def stats(self) -> Dict[str, int]:
        """按失败码计数 —— 回答"哪类失败最多" """
        out: Dict[str, int] = {c.value: 0 for c in CompileFailCode}
        for item in self._items:
            out[item["code"]] = out.get(item["code"], 0) + 1
        return out

    def __len__(self) -> int:
        return len(self._items)


class RuleCompiler:
    """
    S0 规则编译器（窄路：正则 + 关键词 + 结构解析，不调 LLM）

    窄是刻意的：可复现、可复算、零 token。
    遇到真的需要语义判断的规则，正确输出是 `NON_DETERMINISTIC`（诚实），
    而不是"让模型猜一下"（那会让 C 项变成不可复算的分数）。
    """

    def __init__(self) -> None:
        self.corpus = CompileCorpus()

    # ---------- 单条 ----------
    def compile_one(
        self,
        rule_id: str,
        text: str,
        has_locator: bool = True,
        declared_fields: Optional[Sequence[str]] = None,
        uses_llm: bool = False,
        fixtures: Optional[Iterable[Any]] = None,
    ) -> RuleCompileResult:
        """
        编译一条规则

        只把**整条**简单比较表达式（`字段 运算符 字段/数值[%]`）判为可编译；
        不做子串匹配，不产 predicate 兜底 IR：
          - 整式不是受支持的比较形态 → NON_DETERMINISTIC（确定性语法但编不出 IR）
          - 两侧字段任一侧未声明 → MISSING_FIELD

        fixtures 允许两种形态：
          - `FixtureResult`
          - `{"kind": "positive", "passed": true}`
        fixture 不影响 compilable（那是 S0 的判断），但影响 C 的分子（见 rule.py）。
        """
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError(f"rule_id 必须是非空字符串，收到 {rule_id!r}")
        if type(has_locator) is not bool:
            raise ValueError(
                f"has_locator 必须是 bool，收到 {type(has_locator).__name__}：{has_locator!r}"
            )
        if type(uses_llm) is not bool:
            raise ValueError(
                f"uses_llm 必须是 bool，收到 {type(uses_llm).__name__}：{uses_llm!r}"
            )
        if not isinstance(text, str):
            raise ValueError("rule text must be a string")
        if isinstance(declared_fields, (str, bytes, Mapping)):
            raise ValueError("declared_fields must be a sequence of field names")
        declared_fields = list(declared_fields or [])
        for name in declared_fields:
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"declared_fields 必须是非空字符串，收到 {name!r}")

        fixture_models = _coerce_fixtures(fixtures)

        fail_code: Optional[CompileFailCode] = None
        match: Optional["re.Match[str]"] = None

        if SUBJECTIVE_PATTERN.search(text):
            fail_code = CompileFailCode.SUBJECTIVE_RULE
        elif not has_locator:
            fail_code = CompileFailCode.NO_LOCATOR
        elif not declared_fields:
            fail_code = CompileFailCode.MISSING_FIELD
        elif uses_llm or LLM_PATTERN.search(text):
            fail_code = CompileFailCode.NON_DETERMINISTIC
        else:
            match = _CMP_RE.fullmatch(text.strip())
            if match is None:
                # 整式不是受支持的简单比较 → 编不出确定性 IR，诚实判 NON_DETERMINISTIC
                fail_code = CompileFailCode.NON_DETERMINISTIC
            else:
                left_name = match.group("left")
                right_operand = _operand(match.group("right"), declared_fields)
                undeclared = [left_name] if left_name not in declared_fields else []
                if "field" in right_operand and right_operand["field"] not in declared_fields:
                    undeclared.append(right_operand["field"])
                if undeclared:
                    fail_code = CompileFailCode.MISSING_FIELD

        if fail_code is not None:
            return RuleCompileResult(
                rule_id=rule_id,
                original_text=text,
                compilable=False,
                fail_code=fail_code,
                fail_snippet=text[:120],
                fixtures=fixture_models,
            )

        assert match is not None  # fail_code 为 None 时必然已解析成功
        return RuleCompileResult(
            rule_id=rule_id,
            original_text=text,
            compilable=True,
            ir=self._build_ir(match, text, declared_fields),
            fixtures=fixture_models,
        )

    # ---------- 整包 ----------
    def compile_set(
        self,
        rules: Sequence[Mapping[str, Any]],
        skill_version: str,
        fixture_results: Optional[Mapping[str, Sequence[Any]]] = None,
    ) -> RuleSet:
        """
        编译一个规则集

        rules 每项：{rule_id, text, has_locator?, declared_fields?, uses_llm?, fixtures?}
        fixture_results：{rule_id: [fixture...]}，当 S1 独立回填 fixture 时用

        has_locator / uses_llm 只接受真 bool（缺省取默认值），不做 bool 强转；
        rule_id 必须非空且不重复。
        """
        results: List[RuleCompileResult] = []
        seen_rule_ids: set = set()
        for raw in rules:
            rule_id = _rule_id_of(raw)
            if rule_id in seen_rule_ids:
                raise ValueError(f"规则集内存在重复 rule_id：{rule_id!r}")
            seen_rule_ids.add(rule_id)
            fixtures = raw.get("fixtures")
            if fixtures is None and fixture_results is not None:
                fixtures = fixture_results.get(rule_id)
            results.append(
                self.compile_one(
                    rule_id=rule_id,
                    text=str(raw.get("text") or ""),
                    has_locator=_flag_of(raw, "has_locator", True),
                    declared_fields=raw.get("declared_fields") or [],
                    uses_llm=_flag_of(raw, "uses_llm", False),
                    fixtures=fixtures,
                )
            )
        rule_set = RuleSet(skill_version=skill_version, results=results)
        self.corpus.extend_from(rule_set)
        return rule_set

    # ---------- IR ----------
    def _build_ir(
        self,
        match: "re.Match[str]",
        text: str,
        declared_fields: Sequence[str],
    ) -> Dict[str, Any]:
        """
        由已整式解析的比较式产出 cmp IR（契约 C3：compilable=True 必须有 ir）。

        没有 predicate 兜底：编不出 cmp IR 的规则在 compile_one 里已经被判
        NON_DETERMINISTIC，到不了这里。
        """
        op = _OP_NORMALIZE.get(match.group("op"), match.group("op"))
        return {
            "type": "cmp",
            "op": op,
            "left": _operand(match.group("left"), declared_fields),
            "right": _operand(match.group("right"), declared_fields),
            "source_text": text[:120],
        }


# ============================================================
# 内部
# ============================================================
def _operand(token: str, declared_fields: Sequence[str]) -> Dict[str, Any]:
    """把比较式的一侧变成 const 或 field"""
    if re.fullmatch(r"-?\d+(?:\.\d+)?%?", token):
        value = float(token[:-1]) / 100.0 if token.endswith("%") else float(token)
        if not math.isfinite(value):
            raise ValueError("numeric operand must be finite")
        return {"const": value}
    return {"field": token}


def _rule_id_of(raw: Mapping[str, Any]) -> str:
    """取非空 rule_id：缺 rule_id 时回退 id；空串 / 非字符串一律拒绝"""
    value = raw.get("rule_id")
    if value is None:
        value = raw.get("id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"规则必须提供非空 rule_id，收到 {value!r}")
    return value


def _flag_of(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    """严格布尔开关：缺省取 default，其余只接受真 bool，不做 bool 强转"""
    if key not in raw:
        return default
    value = raw[key]
    if type(value) is not bool:
        raise ValueError(f"{key} 必须是 bool，收到 {type(value).__name__}：{value!r}")
    return value


def _coerce_fixtures(fixtures: Optional[Iterable[Any]]) -> List[FixtureResult]:
    """
    严格化 fixture：

      - kind 只接受 positive / negative / boundary（大小写/空白归一），且不得重复；
      - passed 只接受真 bool（dict 输入），字符串 / 数字一律拒绝；
      - 超过 FIXTURES_PER_CHECK 条直接报错，**不截断**（截断 = 静默吞掉脏数据）。
    """
    if fixtures is None:
        return []
    out: List[FixtureResult] = []
    seen_kinds: set = set()
    for item in fixtures:
        if isinstance(item, FixtureResult):
            kind = str(item.kind).strip().lower()
            if kind not in FIXTURE_KINDS:
                raise ValueError(
                    f"fixture kind 必须是 {FIXTURE_KINDS} 之一，收到 {item.kind!r}"
                )
            if kind in seen_kinds:
                raise ValueError(
                    f"fixture kind 重复：{kind!r}（要求 positive/negative/boundary 各一条）"
                )
            seen_kinds.add(kind)
            out.append(FixtureResult(kind=kind, passed=item.passed))
        elif isinstance(item, Mapping):
            raw_kind = item.get("kind")
            if not isinstance(raw_kind, str) or raw_kind.strip().lower() not in FIXTURE_KINDS:
                raise ValueError(
                    f"fixture 必须提供 kind ∈ {FIXTURE_KINDS}，收到 {raw_kind!r}"
                )
            kind = raw_kind.strip().lower()
            passed = item.get("passed")
            if type(passed) is not bool:
                raise ValueError(
                    f"fixture.passed 必须是 bool，收到 {type(passed).__name__}：{passed!r}"
                )
            if kind in seen_kinds:
                raise ValueError(
                    f"fixture kind 重复：{kind!r}（要求 positive/negative/boundary 各一条）"
                )
            seen_kinds.add(kind)
            out.append(FixtureResult(kind=kind, passed=passed))
        else:
            raise ValueError(
                f"fixture 只接受 FixtureResult 或 dict，收到 {type(item).__name__}"
            )
    if len(out) > FIXTURES_PER_CHECK:
        raise ValueError(
            f"fixture 最多 {FIXTURES_PER_CHECK} 条（positive/negative/boundary），"
            f"收到 {len(out)} 条，拒绝截断"
        )
    return out
