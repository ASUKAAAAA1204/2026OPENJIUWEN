# A：16项基线差异审查

当前活跃版本已合并远端a76045f。以下为静态差异审查，行为测试与C复核待完成。

## src\assurance_ledger\core\ars.py

原根版本 87 行；当前 303 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,32 +1,133 @@

 """
-ARS 评分模型（ARS 子 PRD §4）
-
-三段分离是结构上最关键的设计决策：
-  ARS_d 设计态（L+C，跑之前就能出，不依赖链路运行）
-  ARS_r 运行态（R+D+F，跑完证据后出，依赖链路）
-  ARS_g 治理态（改造后全量重算，与改造前对比）
+ARS 评分模型（主 PRD v2.2 / ARS 子 PRD v1.2 §4）—— 板块②（B）归属
+
+两段结构是本产品最关键的设计决策（v1.2 修正后的落点）：
+
+    ARS_d 设计态 = C 单项          满分 25，跑之前就能出 → 设计预警（不阻断）
+    ARS_r 运行态 = L + R + D + F   满分 75，跑完证据后出 → 发布闸
+    ARS_g 治理态 = 改造后全量重算
+
+    ARS（总）= 0.25C + 0.25L + 0.20R + 0.15D + 0.15F
 
 评分坐标：ARS = f(skill_version, model, data_profile)
-  不写这三个，分数不可比。报告标题栏必须显示。
+不写这三个，分数不可比；报告标题栏必须显示。
+
+三层状态（整改计划 §3，两层不在同一层，因此不互斥）：
+    维度级 DimStatus：VALUE / NA / MISSING
+    文档级 DocStatus：DESIGN_ONLY / PARTIAL / COMPLETE
+    判定顺序：DESIGN_ONLY → PARTIAL → COMPLETE，先命中先返回。
+
+红线（写进类型注释，防止后来者改坏）：
+  1. 不得称其为"置信度 / 正确率 / 可信度"；
+  2. 不得由 LLM 直接打分（必须由五项计数算出）；
+  3. 只报整数与分档，不报小数（小数制造不存在的精度）；
+  4. PARTIAL 状态下不得给总分。
+
+改动纪律：本文件属板块②（B），但被 ①③④ 只读引用；
+只做**向后兼容的增量**（旧字段保留，新增字段均有默认值）。
 """
 
 from __future__ import annotations
-from datetime import datetime
-from typing import Optional
+
+from datetime import datetime, timezone
+from enum import Enum
+from typing import Any, Dict, List, Optional
+
 from pydantic import BaseModel, Field
 
 
+# ============================================================
+# 坐标
+# ============================================================
 class ARSCoord(BaseModel):
     """
     评分三元组坐标 —— 决定分数可比性的三个变量
 
-    同一个 Skill 换模型、换数据，分数会变。
+    同一个 Skill 换模型、换数据画像，输出格式就变，可定位率随之变。
     坐标必须写进每一份报告的标题栏。
     """
+
     skill_version: str           # "inv-daily@v1.2.0"
     model: str                   # "gpt-5-mini"
     data_profile: str            # "industry.csv:150行"
-    # 可选：具体数据指纹（digest），让 data_profile 可复算
-    data_digest: Optional[str] = None
+    data_digest: Optional[str] = None   # 可选：让 data_profile 可复算
+
+    def label(self) -> str:
+        """报告标题栏用的一行：skill@v · model= · data="""
+        return f"{self.skill_version} · model={self.model} · data={self.data_profile}"
+
+    def to_payload(self) -> Dict[str, Any]:
+        return {
+            "skill_version": self.skill_version,
+            "model": self.model,
+            "data_profile": self.data_profile,
+            "data_digest": self.data_digest,
+            "label": self.label(),
+        }
+
+
+# ============================================================
+# 两层状态
+# ============================================================
+class DimStatus(str, Enum):
+    """维度级状态（整改计划 §3.1）"""
+
+    VALUE = "VALUE"      # 有值，正常计分
+    NA = "NA"            # 该维不适用（如没有规则）→ 同段内重分配
+    MISSING = "MISSING"  # 该维应有但数据未产出 → 不计分，推高文档级 PARTIAL
+
+
+class DocStatus(str, Enum):
+    """文档级状态（整改计划 §3.2，按顺序判定，先命中先返回）"""
+
+    DESIGN_ONLY = "DESIGN_ONLY"  # r 态未跑且 C=VALUE：只报 C 分项，标"仅设计态"
+    PARTIAL = "PARTIAL"          # 样本 <10 或任一维 MISSING：只报分项，不出总分与分档
+    COMPLETE = "COMPLETE"        # 其余（含存在 NA 重分配）：总分 + 分档
```

## src\assurance_ledger\core\rule.py

原根版本 57 行；当前 115 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,16 +1,35 @@

 """
-规则编译（S0）—— 为 ARS_d 的 C 项供数
+规则编译结果（S0）与规则集（供 ARS 的 C 项）—— 板块②（B）归属
 
-四类失败码：NO_LOCATOR / SUBJECTIVE_RULE / MISSING_FIELD / NON_DETERMINISTIC
-编译成功 → 产出 IR（简单谓词表示）
-编译失败 → 落失败码 + 语料（ARS-6）
+对齐 PRD v2.2 / ARS v1.2 的口径修正：
+
+    C（可编译率）= 「编译通过」且「fixture 三条全绿」的规则 / 总规则
+
+旧版只数 `compilable`，C 会虚高：一条规则能编译但 fixture 不绿，
+意味着"这个检查本身不可信"（链路 PRD §S1「缺 fixture 的检查不许进生产」）。
+因此 fixture 未齐 = 不计入 C 的分子。
+
+改动纪律：本文件属板块②（B），但被 ①③④ 只读引用；
+只做**向后兼容的增量**（新字段均有默认值，旧的构造方式仍然可用）。
 """
 
 from __future__ import annotations
-from datetime import datetime
-from typing import List, Optional, Any
+
+from datetime import datetime, timezone
+from typing import Any, Dict, List, Optional
+
 from pydantic import BaseModel, Field
 
 from .types import CompileFailCode
+
+# 链路 PRD §S1：每条检查必须有 3 个 fixture（正例 / 反例 / 边界）
+FIXTURES_PER_CHECK = 3
+
+
+class FixtureResult(BaseModel):
+    """S1 装配期的一个 fixture 执行结果 —— C 项的第二道门"""
+
+    kind: str          # positive / negative / boundary
+    passed: bool
 
 
@@ -19,22 +38,46 @@

     S0 对单条规则的编译结果
 
-    compilable=False 时必须有 fail_code，且 snippet 要落语料库。
+    compilable=False 时必须有 fail_code，且 fail_snippet 落语料库（ARS-6）；
+    compilable=True 时必须有 ir（契约 C3：否则拒绝装配该规则）。
     """
+
     rule_id: str
     original_text: str
     compilable: bool
-    ir: Optional[Any] = None                 # 编译后的 IR（简单谓词）
+    ir: Optional[Any] = None                 # 编译后的谓词 IR（S4 求值用）
     fail_code: Optional[CompileFailCode] = None
-    fail_snippet: Optional[str] = None       # 用于语料库
-    compiled_at: datetime = Field(default_factory=datetime.utcnow)
+    fail_snippet: Optional[str] = None       # 用于失败语料库
+    fixtures: List[FixtureResult] = Field(default_factory=list)
+    compiled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
+
+    # ---------- 供 C 项使用 ----------
+
+    @property
+    def fixtures_all_green(self) -> bool:
+        """
+        三条 fixture 全通过才算绿
+
+        少于 3 条（含 0 条）一律不算绿 —— 这是"不许进生产"的机械执行，
+        也让"我只补 fixture 不补语义"这种刷分手段无效。
+        """
+        return len(self.fixtures) == FIXTURES_PER_CHECK and all(f.passed for f in self.fixtures)
+
+    def counts_for_c(self, require_fixtures: bool = True) -> bool:
+        """本条规则是否计入 C 的分子"""
+        if not self.compilable:
+            return False
+        if not require_fixtures:
+            return True
+        return self.fixtures_all_green
 
 
 class RuleSet(BaseModel):
     """
-    一次编译的完整结果 —— 为 ARS_d 的 C 项供数
+    一次编译的完整结果 —— 为 ARS 的 C 项供数
 
-    C = 全部 compilable=True 且过 fixture 的规则 / 总规则
-    （ARS PRD §4.2 五维表）
+    C = 全部 compilable=True 且 fixture 三条全绿的规则 / 总规则
+    （ARS PRD §4.2 五维表；分母是规则条数，不是覆盖的 claim 数）
     """
+
     skill_version: str
     results: List[RuleCompileResult] = Field(default_factory=list)
@@ -46,9 +89,24 @@

     @property
```

## src\assurance_ledger\engine\ars_engine.py

原根版本 232 行；当前 459 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,32 +1,51 @@

 """
-ARS 三段打分引擎 —— PRD 核心算法
-
-关键设计决策：
-  ARS_d 设计态（L+C, 跑之前就能出）
-  ARS_r 运行态（R+D+F, 跑完证据后出）
-  ARS_g 治理态（改造后全量重算，与改造前对比）
-
-  总 ARS = (L×25 + C×25) + (R×20 + D×15 + F×15)
-  每段各自算完再相加；N/A 在同段内重分配，跨段不重分配。
+ARS 打分引擎（ARS-1）—— 板块②（B）归属
+
+它只做三件事：**收计数 → 调口径 → 挂解释**。
+公式不在这里，在 rubric.py（唯一口径出处）；解释不在这里，在 explain.py。
+
+为什么这么拆：
+  M1 要求"第三方脚本复算差异 = 0"。引擎公开计数与口径，
+  由独立脚本检验算术一致性；真实重测结果还取决于动作执行。
+
+两段落点（主 PRD v2.2 §0.3）：
+    ARS_d 设计态 = C 单项，跑之前就能出 → 设计预警（C < 0.5 提示，不阻断）
+    ARS_r 运行态 = L + R + D + F，跑完证据后出 → 发布闸（ARS < 70 阻断）
+
+C7（S4 → ARS）在 build_counts 里执行：
+    L 的分子只来自 locator_source == resolver 的 RESOLVED
+    R 的分子只来自 recompute_by == verifier 的 CONSISTENT
+    自报/自证的条目被剔除并计入 c7_violations。
 """
 
 from __future__ import annotations
-from datetime import datetime
-from typing import List, Optional
-
-from ..core.types import LocatorLevel, ClaimStatus, CheckKind
-from ..core.claim import Claim
+
+from dataclasses import dataclass, field, replace
+from typing import Any, Dict, List, Mapping, Optional, Sequence
+
+from ..core.ars import ARSCoord, ARSDims, ARSResult, DocStatus
 from ..core.rule import RuleSet
-from ..core.evidence import EvidenceBundle
-from ..core.check import CheckResult
-from ..core.ars import ARSCoord, ARSResult, ARSDims, ARSBand
-
-
-# 权重 —— PRD 锁定的常数
-WEIGHT_L = 25   # d 态
-WEIGHT_C = 25   # d 态
-WEIGHT_R = 20   # r 态
-WEIGHT_D = 15   # r 态
-WEIGHT_F = 15   # r 态
+from .compiler import RuleCompiler, fixtures_eligible, is_supported_cmp_ir
+from .rubric import (
+    MIN_SAMPLE,
+    REQUIRE_FIXTURES_FOR_C,
+    RUBRIC_VERSION,
+    Counts,
+    ScoreLine,
+    rubric_hash,
+    score_from_counts,
+)
+from .signals import LOC_NO_LOCATOR, RC_NOT_RUN, REASON_INSUFFICIENT_EVIDENCE
+
+
+@dataclass
+class CountBundle:
+    """一次评分的中间结果 —— 计数 + 归因 + C7 诊断"""
+
+    counts: Counts
+    actionable: Dict[str, int] = field(default_factory=dict)
+    structural: Dict[str, int] = field(default_factory=dict)
+    c7: Dict[str, int] = field(default_factory=dict)
+    targets: Dict[str, List[str]] = field(default_factory=dict)
 
 
@@ -35,198 +54,406 @@

     ARS 打分引擎
 
-    三段分离是结构上最关键的设计决策。
-    - ARS_d 不依赖任何链路运行数据（门禁不能等测完再拦）
-    - ARS_r 必须等证据包和检查结果齐了才能算
+    典型用法（③ 板块跑完 S4 之后）：
+
+        engine = ARSEngine()
+        result = engine.score(coord, rule_set=rs, claim_signals=claims, check_signals=checks)
+        if not result.can_gate:        # PARTIAL / DESIGN_ONLY
+            ...                        # 只展示分项，不进闸门比较
     """
 
-    # ---------- ARS_d：设计态（L + C）----------
-
-    def compute_design_phase(
+    def __init__(self, *, require_fixtures_for_c: bool = REQUIRE_FIXTURES_FOR_C) -> None:
+        # 口径唯一（ARS-10）：这些运行期开关会改变评分结果却不改变 rubric hash，
+        # 属于非规范评分设置 —— 直接拒绝，不允许"同一 hash 出两种分"。
+        if require_fixtures_for_c is not REQUIRE_FIXTURES_FOR_C:
```

## src\assurance_ledger\engine\compiler.py

原根版本 86 行；当前 406 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,43 +1,172 @@

 """
-S0 规则编译器 —— 为 ARS_d 的 C 项供数
-
-编译规则文本 → {IR 或失败码}。
-四类失败码：NO_LOCATOR / SUBJECTIVE_RULE / MISSING_FIELD / NON_DETERMINISTIC
-
-关键纪律（链路 PRD §S0）：
-  - 判定必须可复现：LLM 抽取时温度 0 + 缓存
-  - 人工确认环节不可省：LLM 给候选，人勾选
-  - 失败也要落库（这是 ARS 语料的来源）
+S0 规则编译器 —— 为 ARS 的 C 项供数 —— 板块②（B）归属
+
+职责只有一个：把规则文本判成「可编译的谓词」或「四类失败码之一」，
+并让每一次失败都落进语料库（ARS-6）——语料是这个项目唯一会自己长大的资产。
+
+四类失败码（判定顺序 = 先致命、后可恢复）：
+
+    1. SUBJECTIVE_RULE   含"合理/适当/良好"等不可判词 —— 最致命，花钱也改不回客观
+    2. NO_LOCATOR        引用的事实没有可定位字段
+    3. MISSING_FIELD     规则要求的输入不存在（含两侧字段任一未声明）
+    4. NON_DETERMINISTIC 判定依赖 LLM 或随机，或确定性语法但编不出受支持的简单比较 IR
+
+只有**整条**简单比较表达式（字段 运算符 字段/数值[%]）可判为可编译；
+不做子串匹配（"PE < 1 and PB > 5" 整体判 NON_DETERMINISTIC），也没有
+predicate 兜底 IR —— compilable=True 必带 type=cmp 的结构化 IR。
+
+纪律（链路 PRD §S0）：
+  - 判定必须可复现：同一输入两次判定结果一致（用 LLM 抽取时温度 0 + 缓存）；
+  - 人工确认环节不可省：LLM 给候选，人勾选；
+  - 失败也要落库。
+
+注意：本模块只做 S0 的判定，不做 S4 的求值 —— 表达式求值、回源重算是 ③ 板块的事。
+这里产出的 IR 是"谓词候选"，S4 可以细化它，但不许绕过它。
 """
 
 from __future__ import annotations
-from typing import List, Optional
+
 import re
-
+import math
+from datetime import datetime, timezone
+from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
+
+from ..core.rule import FIXTURES_PER_CHECK, FixtureResult, RuleCompileResult, RuleSet
 from ..core.types import CompileFailCode
-from ..core.rule import RuleCompileResult, RuleSet
-
-
-# 不可判词表 —— SUBJECTIVE_RULE 的触发依据
-SUBJECTIVE_TERMS = [
-    r"合理", r"适当", r"良好", r"充分", r"足够", r"相当",
+
+# ============================================================
+# 判定依据
+# ============================================================
+#: 不可判词 —— SUBJECTIVE_RULE 的触发依据（中英各一套）
+SUBJECTIVE_TERMS: tuple = (
+    r"合理", r"适当", r"良好", r"充分", r"足够", r"相当", r"酌情", r"酌情考虑",
+    r"一定程度上", r"大致", r"较好", r"较差", r"建议关注", r"看好",
     r"reasonable", r"appropriate", r"sufficient", r"adequate", r"good", r"well",
-]
-SUBJECTIVE_PATTERN = re.compile(r"(" + "|".join(SUBJECTIVE_TERMS) + r")", re.IGNORECASE)
+    r"fair", r"satisfactory",
+)
+SUBJECTIVE_PATTERN = re.compile("(" + "|".join(SUBJECTIVE_TERMS) + ")", re.IGNORECASE)
+
+#: 依赖模型的判定词 —— NON_DETERMINISTIC 的文本线索
+LLM_TERMS: tuple = (
+    r"由模型判断", r"模型判断", r"交给\s*LLM", r"LLM\s*判断", r"大模型", r"语义判断",
+    r"主观评估", r"综合判断", r"打分", r"llm", r"gpt",
+)
+LLM_PATTERN = re.compile("(" + "|".join(LLM_TERMS) + ")", re.IGNORECASE)
+
+#: 简单比较谓词的形态：左值 运算符 右值（**必须整式匹配**，不允许子串命中：
+#: "PE < 1 and PB > 5" 整式不匹配 → NON_DETERMINISTIC，而不是从子串里抠出 PE < 1）
+_CMP_RE = re.compile(
+    r"(?P<left>[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5.]*)"
+    r"\s*(?P<op>>=|<=|==|!=|≥|≤|=|>|<)"
+    r"\s*(?P<right>[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5.]*|-?\d+(?:\.\d+)?%?)"
+)
+
+_OP_NORMALIZE = {"≥": ">=", "≤": "<=", "=": "=="}
+
+#: cmp IR 里允许出现的运算符（引擎侧闸门与编译共用）
+SUPPORTED_OPS = frozenset((">=", "<=", "==", "!=", ">", "<"))
+
+#: fixture kind 词表：每个规则最多三条，恰好 positive / negative / boundary 各一条
+FIXTURE_KINDS = ("positive", "negative", "boundary")
+
+
+def is_supported_cmp_ir(ir: Any) -> bool:
+    """
+    受支持的可求值 IR 判定 —— compilable=True 的结果必须带这样的 IR 才计入 C 分子。
+
+    type=cmp、op ∈ SUPPORTED_OPS、左值是字段、右值是字段或数值常量；
+    predicate 兜底 IR / None / 未知 op 一律不算数（引擎侧闸门，见 ars_engine）。
```

## src\assurance_ledger\engine\plan.py

原根版本 220 行；当前 435 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,79 +1,163 @@

 """
-改造方案生成器 —— ARS-3
-
-算法（ARS PRD §6.3）：贪心
-  每轮取 gain/CU 最高的动作，直到达到目标档或边际枯竭（< 0.5 分/CU）。
-
-动作库（ARS PRD §8）：
-  add_locator   — 影响 L，0.5-1 CU/条，+1.6 分/条
-  split_rule    — 影响 C，3-5 CU/条，+2.5 分/条
-  add_field     — 影响 C、R，2-4 CU/条，+2.0 分/条
-  add_fixture   — 影响 C，1-2 CU/条，+0.8 分/条
-  rerun_check   — 影响 R、F，1-3 CU/条，+1.5 分/条
+改造方案器（ARS-3）+ 提升曲线（ARS-4）+ 边际分析（MT-4）—— 板块②（B）归属
+
+方案器回答的是产品最值钱的那个问题：**"我差 23 分，补什么、多少钱、补完几分"**。
+
+三条纪律
+--------
+1. **报价必须来自投影，不能来自查表。**
+   旧实现给每个动作一个固定的"分/条"，加总即预测 —— 那样预测永远对不上重测。
+   这里的做法是：动作 → 计数增量 → 用同一个 `rubric.score_from_counts()` 重算。
+   预测和实际重测可以采用同一口径，但 M3/M4 仍需真实执行与费用记录。
+
+2. **归因到条。**
+   每个动作带 `targets`（claim_id / rule_id 列表），这是整改计划 §2.4 的归因链：
+   每一分钱都能回答"对哪一维、改哪几条"。
+
+3. **不报不可执行的缺口。**
+   引擎只把有原因码、且有对应动作的缺口放进 `actionable`；
+   3 条 refuted 的 claim 永远不会进方案 —— 审计不能把错结论变对。
+
+两种策略
+--------
+policy="full"      ：把全部可执行动作都做完（对外演示用，34 CU → 79）
+policy="to_target" ：到目标档即停（逐条贪心；当前示例报价 20 CU → 71，不保证全局最优）
 """
 
 from __future__ import annotations
+
 from dataclasses import dataclass, field
-from typing import List, Optional, Dict
-
-from ..core.ars import ARSResult, ARSBand
-
-
-MIN_MARGIN_PER_CU = 0.5  # 边际枯竭阈值：低于此值时方案停止
-
-
+import math
+from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
+
+from ..core.ars import ARSBand, ARSResult
+from .ars_engine import ARSEngine
+from .rubric import (
+    ACTION_LIBRARY,
+    ACTION_ORDER,
+    BANDS,
+    BUFFER_RATE,
+    MARGIN_FLOOR,
+    MIN_SAMPLE,
+    Counts,
+    band_of,
+    round_half_up,
+    score_from_counts,
+)
+
+POLICY_FULL = "full"
+POLICY_TO_TARGET = "to_target"
+
+#: 停止原因
+STOP_ALL_DONE = "ALL_ACTIONS_TAKEN"
+STOP_TARGET = "TARGET_REACHED"
+STOP_MARGINAL = "MARGINAL_EXHAUSTED"
+STOP_BUDGET = "BUDGET_EXHAUSTED"
+
+
+# ============================================================
+# 动作
+# ============================================================
 @dataclass
 class Action:
-    """一条改造动作"""
-    kind: str                    # add_locator / split_rule / add_field / ...
-    target_count: int            # 要改多少条
-    cu_per_unit: float           # 单次 CU
-    gain_per_unit: float         # 单次增益（分/条）
-    affects: List[str] = field(default_factory=list)  # 影响哪些维度
+    """一条改造动作 —— 数量、单价、增益、归因对象"""
+
+    kind: str
+    n: int
+    cu_per_unit: float
+    gain: float                     # 投影得到的 Δ分（精确值，未取整）
+    affects: List[str] = field(default_factory=list)
+    targets: List[str] = field(default_factory=list)
     reason: str = ""
+    rank: int = 0                   # 贪心排序位次（1 = 最先做）
```

## src\assurance_ledger\engine\__init__.py

原根版本 15 行；当前 62 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,15 +1,62 @@

 """
-engine/ —— B 管：编译、打分、方案
+engine/ —— 板块②（B）：编译、打分、方案（关键路径）
 
-职责：
-  1. S0 规则编译（RuleCompiler）
-  2. ARS_d / ARS_r / ARS_g 打分（ARSEngine）
-  3. 改造方案生成（PlanGenerator）—— 贪心，分/CU 降序
-  4. S1 fixture 生成（FixtureGenerator）
+职责（对齐分工表）：
+  1. S0 规则编译与四类失败码（compiler.RuleCompiler）+ 失败语料（CompileCorpus）
+  2. 五维打分器（ars_engine.ARSEngine）+ 唯一口径（rubric）
+  3. 扣分解释器（explain.build_explains）—— ARS-2
+  4. 改造方案器 / 报价 / 提升曲线 / 边际（plan.PlanGenerator）—— ARS-3 / ARS-4 / MT-4
+  5. 抗博弈（antigaming）—— ARS-9
+  6. 口径版本与 hash（rubric.rubric_hash）—— ARS-10
+  7. 债务与预算（instrument）—— MT-2 / MT-3
+
+对上游的两个端口（不 import ①③ 的任何实现）：
+  signals.ClaimSignal  ← S3 LocatorResolver + S4/verifier 复算 + claim 级新鲜度
+  signals.CheckSignal  ← S4 的同输入双跑一致性
+
+对外只暴露 JSON 契约：
+  ARSResult.to_payload()  → /api/ars
+  Plan.to_payload()       → /api/plan
 """
 
-from .compiler import RuleCompiler
-from .ars_engine import ARSEngine
-from .plan import PlanGenerator
+from .antigaming import AntiGamingReport, evaluate as evaluate_antigaming
+from .ars_engine import ARSEngine, CountBundle
+from .compiler import CompileCorpus, RuleCompiler
+from .explain import build_explains, top_explains
+from .instrument import AcceptWithGrade, accept_with_grade, budget_snapshot, debt_summary
+from .plan import Action, Plan, PlanGenerator
+from .rubric import Counts, rubric_definition, rubric_hash, score_from_counts
+from .signals import CaseSignals, CheckSignal, ClaimSignal
 
-__all__ = ["RuleCompiler", "ARSEngine", "PlanGenerator"]
+__all__ = [
+    # S0
+    "RuleCompiler",
+    "CompileCorpus",
+    # 口径
+    "Counts",
+    "score_from_counts",
+    "rubric_hash",
+    "rubric_definition",
+    # 打分
+    "ARSEngine",
+    "CountBundle",
+    # 解释
+    "build_explains",
+    "top_explains",
+    # 方案
+    "PlanGenerator",
+    "Plan",
+    "Action",
+    # 抗博弈
+    "evaluate_antigaming",
+    "AntiGamingReport",
+    # 仪表
+    "debt_summary",
+    "budget_snapshot",
+    "accept_with_grade",
+    "AcceptWithGrade",
+    # 端口
+    "ClaimSignal",
+    "CheckSignal",
+    "CaseSignals",
+]
```

## src\assurance_ledger\ledger\checker.py

原根版本 74 行；当前 270 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,74 +1,270 @@

+"""主 checker（c-checker-v1）
+
+只做两个 checker：recompute 和 line_lookup。
+每次检查同输入独立执行两次，比较稳定业务结果；时间/耗时/随机 ID 不进比较。
+双跑的两次耗时都累加进成本。
 """
-S4 检查执行器 —— 只实现两个 checker
-
-链路 PRD 明确：
-  只实现 recompute（回源重算）和 line_lookup（行/字段定位）两个 checker。
-  不确定时输出 indeterminate，交给 S6 记债务。
-  不实现新裁决算法。
-"""
-
 from __future__ import annotations
-from typing import Optional
-from datetime import datetime
-
-from ..core.evidence import EvidenceBundle
-from ..core.check import CheckTemplate, CheckResult
-from ..core.types import CheckKind, ClaimStatus
+
+import time
+from typing import Any, Callable, Mapping, TypeVar
+
+from . import evaluator
+from .canonical import canonical_bytes, digest_json
+from .integrity import check_package as check_integrity
+from .reasons import is_known
+
+CHECKER_VERSION = "c-checker-v1"
+T = TypeVar("T")
+
+
+def _timed(fn: Callable[[], T]) -> tuple[T, float]:
+    start = time.perf_counter()
+    value = fn()
+    return value, (time.perf_counter() - start) * 1000.0
+
+
+def _stable(value: Any) -> bytes:
+    return canonical_bytes(value)
+
+
+def _check_record(
+    *,
+    check_id: str,
+    kind: str,
+    status: str,
+    reason_code: str | None,
+    double_run_consistent: bool | None,
+    run_payloads: list[dict[str, Any]],
+    duration_ms: float | None,
+    evidence_refs: list[str],
+    measurement_status: str = "measured",
+    extra: Mapping[str, Any] | None = None,
+) -> dict[str, Any]:
+    measured = measurement_status == "measured" and duration_ms is not None
+    cost_cu = None if duration_ms is None else round(duration_ms / 1000.0, 9)
+    record = {
+        "check_id": check_id,
+        "kind": kind,
+        "checker": CHECKER_VERSION,
+        "status": status,
+        "reason_code": reason_code,
+        "double_run_consistent": double_run_consistent,
+        "is_deterministic": double_run_consistent is True,
+        "runs": run_payloads,
+        "duration_ms": None if duration_ms is None else round(duration_ms, 6),
+        "tokens_in": 0,
+        "tokens_out": 0,
+        "human_minutes": None,
+        "cost_cu": cost_cu if measured else None,
+        "measurement_status": "measured" if measured else "unknown",
+        "evidence_refs": list(evidence_refs),
+    }
+    if extra:
+        record.update(dict(extra))
+    return record
+
+
+def _verify_double_run(
+    fn: Callable[[], Any],
+) -> tuple[Any, list[dict[str, Any]], bool | None, float]:
+    first, ms1 = _timed(fn)
+    second, ms2 = _timed(fn)
+    payload_first = first.stable_payload() if isinstance(first, evaluator.EvalResult) else first
+    payload_second = second.stable_payload() if isinstance(second, evaluator.EvalResult) else second
+    consistent = _stable(payload_first) == _stable(payload_second)
+    return first, [payload_first, payload_second], consistent, ms1 + ms2
+
+
+def _integrity_failure(claim_id: str, integrity) -> dict[str, Any]:
+    reason = "PACKAGE_HASH_MISMATCH"
+    line = _check_record(
+        check_id=f"check:{claim_id}:line_lookup",
+        kind="line_lookup",
```

## src\assurance_ledger\ledger\debt_tracker.py

原根版本 49 行；当前 170 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,49 +1,170 @@

+"""债务事件台账（c-debt-v1）
+
+追加式事件：open / resolve / waive。
+每条带 claim_id、type、reason_code、动作、owner、due、before/after、金额。
+未知金额保持 null，不填 0。补验后追加 resolve，不改历史。
 """
-债务台账 —— S6 决策产物
+from __future__ import annotations
 
-三类债务（主 PRD §5）：
-  unverified   — 还没验（预算不够、低严重度等）
-  stale        — 验过但数据过期（digest 变了）
-  structural   — 结构上验不了（缺 locator、缺字段、规则主观）
+from pathlib import Path
+from typing import Any, Iterable, Mapping
 
-只有 100% 未验证 claim 都带等级 + 原因码 + 预算账，
-才能说"没验可归因"（G4 目标）。
-"""
+from ..core.types import DebtType
+from .canonical import append_jsonl, utc_iso
+from .reasons import action_for, debt_type
 
-from __future__ import annotations
-from typing import List, Optional
+DEBT_VERSION = "c-debt-v1"
+EVENTS = ("open", "resolve", "waive")
 
-from ..core.ledger_entry import DebtItem
-from ..core.types import DebtType
+
+def _debt_type(value: str) -> str:
+    allowed = {item.value for item in DebtType}
+    if value not in allowed:
+        raise ValueError(f"债务类型必须是 {sorted(allowed)}")
+    return value
+
+
+def _amount(value: Any) -> float | None:
+    if value is None:
+        return None
+    if isinstance(value, bool) or not isinstance(value, (int, float)):
+        raise ValueError("amount_cu 必须是有限数值或 null")
+    number = float(value)
+    if number != number or number in (float("inf"), float("-inf")) or number < 0:
+        raise ValueError("amount_cu 必须是非负有限数值或 null")
+    return number
 
 
 class DebtTracker:
-    """未兑现验证义务的台账"""
+    version = DEBT_VERSION
 
-    def __init__(self):
-        self._debts: List[DebtItem] = []
+    def __init__(self, events: Iterable[Mapping[str, Any]] | None = None,
+                 *, event_path: Path | str | None = None):
+        self._events: list[dict[str, Any]] = []
+        self.event_path = Path(event_path) if event_path is not None else None
+        for event in events or ():
+            self._events.append(dict(event))
+        self._open: dict[tuple[str, str], dict[str, Any]] = {}
+        for event in self._events:
+            self._apply(event)
 
-    def record(self, claim_id: str, debt_type: DebtType, reason_code: str, amount_cu: Optional[float] = None):
-        self._debts.append(DebtItem(
-            claim_id=claim_id,
-            type=debt_type,
-            reason_code=reason_code,
-            amount_cu=amount_cu,
-        ))
+    def _apply(self, event: Mapping[str, Any]) -> None:
+        key = (str(event["claim_id"]), str(event["reason_code"]))
+        if event["event"] == "open":
+            self._open[key] = {
+                "claim_id": key[0],
+                "type": event["type"],
+                "reason_code": key[1],
+                "action": event.get("action"),
+                "owner": event.get("owner"),
+                "due": event.get("due"),
+                "amount_cu": event.get("amount_cu"),
+            }
+        elif event["event"] in ("resolve", "waive"):
+            self._open.pop(key, None)
 
-    def summary(self) -> dict:
-        """按类型聚合"""
-        by_type: dict = {t.value: 0 for t in DebtType}
-        total_cu = 0.0
-        for d in self._debts:
-            by_type[d.type.value] = by_type.get(d.type.value, 0) + 1
-            if d.amount_cu:
-                total_cu += d.amount_cu
+    def _emit(self, event: dict[str, Any]) -> dict[str, Any]:
+        if event["event"] not in EVENTS:
```

## src\assurance_ledger\ledger\ledger.py

原根版本 105 行；当前 167 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,105 +1,167 @@

+"""S5 账本：版本化信封、RFC8785、哈希链、verify
+
+格式：assurance-ledger-v1
+hash_policy.hash_input = entry minus record_hash, prev_hash included
+时间统一 UTC ISO8601；不再使用顶层裸数组或 json.dumps(sort_keys=True)。
 """
-S5 账本：哈希链 + verify 命令
+from __future__ import annotations
 
-纪律（链路 PRD）：
-  - record_hash = sha256(JCS(record 去掉 prev_hash))
-  - prev_hash 串联形成链
-  - verify 子命令输出 PASS/FAIL + 首个断链位置
-  - PDF 可离线阅读（初版先做 JSON）
-"""
+from pathlib import Path
+from typing import Any, Iterable, Mapping
 
-from __future__ import annotations
-from typing import List, Optional, Tuple
-import hashlib
-import json
-from datetime import datetime
+from pydantic import BaseModel, Field, field_validator
 
-from ..core.ledger_entry import LedgerEntry, Decision, DebtItem
+from .canonical import digest_json, json_dump, read_json, utc_iso
+from .ledger_verify import HASH_POLICY, LEDGER_FORMAT, verify_document
+
+TRUST_LEVELS = ("L0", "L1", "L2")
+RECORD_PHASES = ("pre_execution", "post_execution", "decision")
+DECISION_ACTIONS = ("release", "block", "accept_with_grade")
 
 
-class Ledger:
-    """
-    账本 —— 持久化审计记录
+class LedgerRecord(BaseModel):
+    seq: int
+    ts: str
+    principal: str
+    record_phase: str
+    trust_level: str
+    run_id: str
+    claim_ids: list[str] = Field(default_factory=list)
+    adjudication: dict[str, Any] | None = None
+    decision: dict[str, Any] | None = None
+    debts: list[dict[str, Any]] = Field(default_factory=list)
+    evidence_refs: list[str] = Field(default_factory=list)
+    payload_digest: str | None = None
+    verifier: dict[str, Any] | None = None
+    prev_hash: str | None = None
+    record_hash: str | None = None
 
-    接口冻结后，任何模块不得绕过本类直接写 JSON 文件。
-    """
+    @field_validator("principal", "run_id")
+    @classmethod
+    def _non_empty(cls, value: str) -> str:
+        if not isinstance(value, str) or not value.strip():
+            raise ValueError("principal/run_id 必须非空")
+        return value
 
-    def __init__(self, path: str = "ledger.json"):
-        self.path = path
-        self._entries: List[LedgerEntry] = []
-        self._next_seq = 0
+    @field_validator("record_phase")
+    @classmethod
+    def _phase(cls, value: str) -> str:
+        if value not in RECORD_PHASES:
+            raise ValueError(f"record_phase 必须是 {RECORD_PHASES}")
+        return value
+
+    @field_validator("trust_level")
+    @classmethod
+    def _trust(cls, value: str) -> str:
+        if value not in TRUST_LEVELS:
+            raise ValueError(f"trust_level 必须是 {TRUST_LEVELS}")
+        return value
+
+
+class AssuranceLedger:
+    format = LEDGER_FORMAT
+    hash_policy = HASH_POLICY
+
+    def __init__(self, path: Path | str | None = None):
+        self.path = Path(path) if path is not None else None
+        self.entries: list[dict[str, Any]] = []
+
+    # ---------- 读取 ----------
+    @classmethod
+    def load(cls, path: Path | str) -> "AssuranceLedger":
+        document = read_json(path)
+        if not isinstance(document, Mapping) or document.get("format") != LEDGER_FORMAT:
+            raise ValueError("不是 assurance-ledger-v1")
+        entries = document.get("entries")
```

## src\assurance_ledger\ledger\packer.py

原根版本 66 行；当前 223 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,66 +1,223 @@

+"""A 的 S3：冻结模型、JCS 摘要、基于外部复算器的删除测试。
+
+不执行 S4 判定、S5 账本校验或 ARS 评分。配套 payloads 使 JSON 可以离线交接。
 """
-S3 证据包生成器
-
-职责：
-  1. 从事件流中为每条 claim 抽证据
-  2. 计算 digest（JCS-RFC 8785 规范化后 SHA-256）
-  3. 确定 locator 级别（为 ARS_d 的 L 项供数）
-  4. 最小集通过删除测试确定
-"""
-
 from __future__ import annotations
-from typing import List
-import hashlib
-import json
+
+from copy import deepcopy
+from dataclasses import dataclass
+from datetime import datetime
+from typing import Callable
 
 from ..core.claim import Claim
 from ..core.event import EventStream
 from ..core.evidence import Evidence, EvidenceBundle
+from ..rail.evidence_io import canonical_bytes, digest_json
+from ..schemas.contracts import check_C1, check_C2
+
+
+PACKAGE_FORMAT = "assurance-evidence-v1"
+Replay = Callable[[dict, dict], dict | None]
+
+
+@dataclass
+class PackedEvidence:
+    bundle: EvidenceBundle
+    payloads: dict[str, dict]
+    diagnostics: dict
+    minimality: dict
+
+    def document(self) -> dict:
+        document = {
+            "format": PACKAGE_FORMAT,
+            "bundle": self.bundle.model_dump(mode="json"),
+            "payloads": deepcopy(self.payloads),
+            "diagnostics": deepcopy(self.diagnostics),
+            "minimality": deepcopy(self.minimality),
+            "hash_policy": {
+                "canonicalization": "RFC8785",
+                "algorithm": "SHA-256",
+                "evidence_digest": "JCS(payloads[ref])",
+                "record_hash": "JCS(bundle excluding record_hash; prev_hash included)",
+                "package_hash": "JCS(document excluding package_hash)",
+            },
+        }
+        document["package_hash"] = digest_json(document)
+        return document
 
 
 class EvidencePacker:
-    """
-    S3 证据包生成
-
-    初版：从 EventStream 里按 claim_id 聚事件 → 抽关键字段做 Evidence
-    """
+    @staticmethod
+    def _digest(obj) -> str:
+        return digest_json(obj)
+
+    @staticmethod
+    def _seal(bundle: EvidenceBundle) -> None:
+        bundle.record_hash = digest_json(bundle.model_dump(mode="json", exclude={"record_hash"}))
 
     def pack(self, claim: Claim, stream: EventStream) -> EvidenceBundle:
-        """为单条 claim 生成证据包"""
-        # 1. 按 claim_id 聚事件
-        related_events = [e for e in stream.events if e.claim_id == claim.id]
-
-        # 2. 转 Evidence 条目
-        evidences: List[Evidence] = []
-        for e in related_events:
-            ev = Evidence(
-                kind="tool_call",
-                ref=f"{e.agent}.{e.tool or e.kind}",
-                locator=None,  # 初版不精细到字段（这是补 locator 动作要做的）
-                digest=self._digest(e.extra or {}),
-                captured_at=e.ts,
-                freshness="fresh",
-            )
-            evidences.append(ev)
-
-        bundle = EvidenceBundle(
-            id=f"evb-{claim.id}",
-            skill=claim.skill_version.split("@")[0] if "@" in claim.skill_version else claim.skill_version,
-            version=claim.skill_version.split("@")[1] if "@" in claim.skill_version else "?",
-            claim=claim,
```

## src\assurance_ledger\ledger\__init__.py

原根版本 16 行；当前 25 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,16 +1,25 @@

 """
-ledger/ —— C 管：证据、账本、回执
+ledger/ —— C 管：证据、判定、账本
 
 职责：
   1. S3 证据包生成（EvidencePacker）
-  2. S4 检查执行（Checker —— 只实现 recompute / line_lookup 两个）
-  3. S5 账本与哈希链（Ledger + verify）
-  4. Debt 台账
+  2. S4 判定（Checker / 独立 verifier / replay）
+  3. S5 账本与哈希链（AssuranceLedger + verify）
+  4. 债务台账与报告 payload
 """
 
+from .checker import Checker, check_package
+from .debt_tracker import DebtTracker
+from .ledger import AssuranceLedger
+from .ledger_verify import verify_document, verify_file
 from .packer import EvidencePacker
-from .checker import Checker
-from .ledger import Ledger
-from .debt_tracker import DebtTracker
 
-__all__ = ["EvidencePacker", "Checker", "Ledger", "DebtTracker"]
+__all__ = [
+    "AssuranceLedger",
+    "Checker",
+    "DebtTracker",
+    "EvidencePacker",
+    "check_package",
+    "verify_document",
+    "verify_file",
+]
```

## src\assurance_ledger\rail\cache.py

原根版本 39 行；当前 46 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -21,4 +21,6 @@

 
     def __init__(self, max_size: int = 1000):
+        if max_size < 1:
+            raise ValueError("缓存容量必须为正数")
         self._events: List[AgentEvent] = []
         self._by_claim: Dict[str, List[AgentEvent]] = {}
@@ -27,11 +29,16 @@

     def put(self, event: AgentEvent):
         self._events.append(event)
-        if len(self._events) > self.max_size:
-            self._events = self._events[-self.max_size:]
         if event.claim_id:
             self._by_claim.setdefault(event.claim_id, []).append(event)
+        while len(self._events) > self.max_size:
+            removed = self._events.pop(0)
+            if removed.claim_id:
+                group = self._by_claim[removed.claim_id]
+                group.pop(0)
+                if not group:
+                    del self._by_claim[removed.claim_id]
 
     def for_claim(self, claim_id: str) -> List[AgentEvent]:
-        return self._by_claim.get(claim_id, [])
+        return list(self._by_claim.get(claim_id, []))
 
     def clear(self):
```

## src\assurance_ledger\rail\collector.py

原根版本 71 行；当前 71 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -8,5 +8,5 @@

 from __future__ import annotations
 from typing import Callable, List, Optional
-from datetime import datetime
+from datetime import datetime, timezone
 
 from ..core.event import AgentEvent, EventStream
@@ -19,5 +19,5 @@

     用法（W0 spike 之后具体实现）：
       collector = RailCollector()
-      collector.on_call = my_handler
+      collector.on_event = my_handler
       # 注册到 openJiuwen Rail 的 hooks
     """
@@ -48,5 +48,5 @@

         event = AgentEvent(
             seq=self._next_seq,
-            ts=datetime.utcnow(),
+            ts=datetime.now(timezone.utc),
             agent=agent,
             kind=kind,
```

## src\assurance_ledger\rail\cost.py

原根版本 34 行；当前 132 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -6,6 +6,42 @@

 
 from __future__ import annotations
+
+import math
+from collections import defaultdict
+
 from ..core.event import AgentEvent
 from ..core.cost import CU, CostBreakdown
+
+
+class IncompleteCostError(ValueError):
+    """证据不完整时不能用冻结模型的 float 字段伪装成已知成本。"""
+
+
+def _minutes(value):
+    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
+        raise ValueError("人工分钟必须是非负有限数值")
+    return float(value)
+
+
+def missing_cost_fields(event: AgentEvent) -> list[str]:
+    missing = []
+    if (event.extra.get("timing_status") != "measured" or type(event.latency_ms) not in (int, float)
+            or not math.isfinite(event.latency_ms) or event.latency_ms < 0):
+        missing.append("latency")
+    status = event.extra.get("token_status")
+    token_types_valid = all(type(value) is int and value >= 0 for value in (event.tokens_in, event.tokens_out))
+    if event.kind == "tool":
+        if not token_types_valid or status != "not_applicable_tool" or event.tokens_in != 0 or event.tokens_out != 0:
+            missing.append("tool_token_status")
+    else:
+        response = event.extra.get("response")
+        usage = response.get("usage_metadata") if isinstance(response, dict) else None
+        receipt_matches = (isinstance(usage, dict)
+                           and all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("input_tokens", "output_tokens"))
+                           and usage["input_tokens"] == event.tokens_in and usage["output_tokens"] == event.tokens_out)
+        if (event.kind != "model" or not token_types_valid or status != "reported"
+                or event.extra.get("mode") != "live" or not receipt_matches):
+            missing.append("real_model_tokens")
+    return missing
 
 
@@ -19,4 +55,8 @@

 
     def event_to_cu(self, event: AgentEvent, human_minutes: float = 0.0) -> CU:
+        human_minutes = _minutes(human_minutes)
+        missing = missing_cost_fields(event)
+        if missing:
+            raise IncompleteCostError("缺少实测用量或耗时，不能出完整 CU")
         breakdown = CostBreakdown(
             tokens_in=event.tokens_in,
@@ -28,7 +68,65 @@

 
     def total_cu(self, events: list, human_minutes_per_claim: float = 0.0) -> CU:
-        """一批事件的总成本"""
-        total = 0.0
-        for e in events:
-            total += self.event_to_cu(e, human_minutes_per_claim).value
-        return CU(value=total)
+        """人工时间按结论计一次；未归属调用的实测成本仍计入批次总额。"""
+        minutes = _minutes(human_minutes_per_claim)
+        events = list(events)
+        self._validate_batch(events)
+        if minutes and any(event.claim_id is None for event in events):
+            raise ValueError("有未归属事件时，不能猜测每条结论的人工时间")
+        total = sum(self.event_to_cu(event).value for event in events)
+        count = len({event.claim_id for event in events if event.claim_id is not None})
+        return CU(value=total + count * minutes * 60)
+
+    @staticmethod
+    def _validate_batch(events):
+        if any(type(event.seq) is not int or event.seq < 1 for event in events):
+            raise ValueError("成本事件序号必须为正整数")
+        if any(not isinstance(event.extra.get("run_id"), str) or not event.extra["run_id"].strip() for event in events):
+            raise ValueError("成本事件缺少运行编号")
+        if len({event.seq for event in events}) != len(events):
+            raise ValueError("成本批次包含重复事件序号")
+        if len({event.extra.get("run_id") for event in events}) > 1:
+            raise ValueError("不能把不同运行的同名结论混合计费")
+
+    def summarize(self, events: list[AgentEvent], *, claim_ids: list[str],
+                  human_minutes_by_claim: dict[str, float] | None = None) -> dict:
+        """JSON 交接：完整成本为数值，缺测为 null，同时保留已知部分。"""
+        self._validate_batch(events)
+        if len(set(claim_ids)) != len(claim_ids):
+            raise ValueError("结论编号重复")
+        human = human_minutes_by_claim or {}
+        if set(human) - set(claim_ids):
+            raise ValueError("人工时间包含未登记结论")
+        for value in human.values():
+            _minutes(value)
+        groups = defaultdict(list)
+        for event in events:
+            if event.claim_id is not None and event.claim_id not in claim_ids:
+                raise ValueError("事件包含未登记结论")
+            groups[event.claim_id].append(event)
+
```

## scripts\generate_sbom.py

原根版本 96 行；当前 97 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -11,4 +11,5 @@

 """
 
+import os
 import subprocess
 import sys
@@ -21,15 +22,15 @@

 
     try:
-        # 方式 1：用 cyclonedx-python-lib 从 requirements.txt 生成
-        # （需要 pip install cyclonedx-python-lib）
+        # 方式 1：用 cyclonedx-py 从 requirements.txt 生成
+        # （需要 pip install cyclonedx-bom）
+        env = dict(os.environ, PYTHONUTF8="1")
         result = subprocess.run(
             [
                 sys.executable, "-m", "cyclonedx_py",
-                "pip",
-                "--requirements", str(root / "requirements.txt"),
-                "--out", str(output),
+                "requirements", str(root / "requirements.txt"),
+                "-o", str(output),
                 "--output-format", "json",
             ],
-            capture_output=True, text=True, cwd=str(root),
+            capture_output=True, text=True, cwd=str(root), env=env,
         )
         if result.returncode == 0:
@@ -90,5 +91,5 @@

         json.dump(sbom, f, indent=2, ensure_ascii=False)
     print(f"[SBOM] Manual fallback → {output}")
-    print("[SBOM] Warning: manual SBOM — run 'pip install cyclonedx-python-lib' for auto-gen")
+    print("[SBOM] Warning: manual SBOM — run 'pip install cyclonedx-bom' for auto-gen")
 
 
```

## scripts\run_daily_agent.py

原根版本 257 行；当前 125 行。选择当前合并版，保留原根快照以便追溯；禁止回退覆盖远端后续规则迁移和绑定修订。复核状态：静态比较完成，行为验收待C。

```diff
--- original

+++ active

@@ -1,255 +1,123 @@

-"""
-真跑一次「日报 Skill」：S0 规则编译 → S2 生成日报 → S3 抽取 claim + locator
-
-全部为真实 LLM 调用（DeepSeek），温度 0，输入为 fixtures/source 的真实行情快照。
-不做任何人工预设：编译失败码、claim 条数、locator 精确度均由模型真实产出。
-
-输出到 fixtures/runs/：
-    s0_compile.json   10 条规则的编译判定
-    report.md         Agent 输出的自然语言日报
-    claims.json       从日报抽取的 claim + locator
-    meta.json         模型、温度、token 用量（供 CU 计算）
-
-用法：
-    python scripts/run_daily_agent.py
-"""
-
-from __future__ import annotations
-
+"""A 的采集验证入口；默认本地替身，--mode live 才调用模型服务。"""
+import argparse
+import asyncio
 import json
-import os
-import re
-import time
-from datetime import datetime, timezone
+import sys
 from pathlib import Path
-
-import pandas as pd
-from openai import OpenAI
-
-ROOT = Path(__file__).resolve().parents[1]
-SRC = ROOT / "fixtures" / "source"
-OUT = ROOT / "fixtures" / "runs"
-
-MODEL = "deepseek-chat"
-FAIL_CODES = ["NO_LOCATOR", "SUBJECTIVE_RULE", "MISSING_FIELD", "NON_DETERMINISTIC"]
-
-# 待审 Skill 的 10 条规则（真实日报类 Skill 的常见写法，含主观词与缺字段）
-RULES = [
-    ("R01", "若个股 PE(TTM) 低于所属行业 PE 中位数 20% 以上，标注为低估。"),
-    ("R02", "若个股 PE(TTM) 高于所属行业 PE 中位数 30% 以上，标注为估值偏高。"),
-    ("R03", "列出当日涨幅最高的 3 只标的，并说明涨幅。"),
-    ("R04", "对估值处于合理区间的标的，建议持有。"),
-    ("R05", "当收盘价高于 30 日均线 5% 时，提示短期偏强。"),
-    ("R06", "当成交额低于行业平均水平时，提示关注度下降。"),
-    ("R07", "每条结论需注明数据来源与取数日期。"),
-    ("R08", "结合银行板块净息差走势进行判断。"),
-    ("R09", "综合市场情绪给出仓位建议。"),
-    ("R10", "若个股 PB 低于 1，标注为破净。"),
-]
-
-
-def client() -> OpenAI:
-    key = os.environ.get("DEEPSEEK_API_KEY", "")
-    if not key:
-        raise SystemExit("缺少 DEEPSEEK_API_KEY")
-    return OpenAI(api_key=key, base_url="https://api.deepseek.com")
-
-
-def chat(cli: OpenAI, system: str, user: str, retries: int = 3) -> tuple[str, dict]:
-    """带重试的单轮调用，返回（正文，用量）"""
-    last = None
-    for i in range(retries):
-        try:
-            r = cli.chat.completions.create(
-                model=MODEL,
-                temperature=0,
-                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
-                timeout=90,
-            )
-            usage = {
-                "prompt_tokens": r.usage.prompt_tokens,
-                "completion_tokens": r.usage.completion_tokens,
-            }
-            return r.choices[0].message.content or "", usage
-        except Exception as exc:
-            last = exc
-            time.sleep(2 * (i + 1))
-    raise RuntimeError(f"LLM 调用失败：{last}")
-
-
-def extract_json(text: str) -> dict | list:
-    """从模型输出里取第一个 JSON 块"""
-    text = text.strip()
-    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
-    if m:
-        text = m.group(1)
-    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)
-    if start < 0:
-        raise ValueError("输出中无 JSON")
-    for end in range(len(text), start, -1):
-        try:
-            data = json.loads(text[start:end])
-        except json.JSONDecodeError:
-            continue
```
