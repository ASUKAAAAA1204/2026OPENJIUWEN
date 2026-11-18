# B1 规范比较规则（b-cmp-v1）

本目录是 B 的三条规范数值比较规则及其固定 fixture
规则版本 `b-pe-v1`，IR 版本 `b-cmp-v1`
状态：B 本地编译与 fixture 已验证；C PR #5 已接入正式规则及 v2 映射，相关样例由 B 复跑通过；A 正式签收和项目整体验收仍未完成

## 文件

| 文件 | 内容 |
|---|---|
| `rules-v1.json` | 三条规则定义，字段、开关和声明字段逐一固定 |
| `fixtures-v1.json` | 每条规则的正例、反例、边界，共 9 条，含期望值与实际执行结果 |
| `compiled-v1.json` | 由当前 `RuleCompiler` 实际编译并以 `evaluate_cmp` 执行 fixture 后生成的结果，不是手写 IR |
| `README.md` | 口径、C 映射关系、验收边界 |

## 规则

| rule_id | 原文 | op |
|---|---|---|
| `b-pe-less-than-industry@1` | `pe < industry_pe` | `<` |
| `b-pe-greater-than-industry@1` | `pe > industry_pe` | `>` |
| `b-pe-equal-industry@1` | `pe == industry_pe` | `==` |

三条规则都要求 `pe`、`industry_pe` 已声明，`has_locator=true`，`uses_llm=false`
编译后 IR 形状统一为 `b-cmp-v1`

```json
{
  "type": "cmp",
  "op": "<",
  "left": {"field": "pe"},
  "right": {"field": "industry_pe"},
  "source_text": "pe < industry_pe"
}
```

## Fixture 矩阵

fixture 顺序固定为 positive、negative、boundary

| 规则 | positive | negative | boundary |
|---|---|---|---|
| `<` | pe=10, industry_pe=20 → true | pe=30, industry_pe=20 → false | pe=20, industry_pe=20 → false |
| `>` | pe=30, industry_pe=20 → true | pe=10, industry_pe=20 → false | pe=20, industry_pe=20 → false |
| `==` | pe=20, industry_pe=20 → true | pe=19, industry_pe=20 → false | pe=1, industry_pe=20 → false |

## C 分子

`RuleSet.c_numerator()` 只统计同时满足以下条件的规则

- `compilable=true` 且带受支持的 `cmp` IR
- fixture 恰好 3 条，kind 恰为 positive、negative、boundary 各一条
- 三条 fixture 全部 `passed=true`

本目录 3 条规则全部满足，`compiled-v1.json` 的记录为
`total=3, compilable=3, fixture_green=3, c_numerator=3, c_rate=1.0`
这个 3/3 只表示规则编译与本地 fixture 计数，不是 ARS 总分，也不是项目验收结论

## 非法输入

运行期 `evaluate_cmp` 对非法操作数返回 `status=INDETERMINATE`、`value=null` 并给出原因码

| 场景 | reason_code |
|---|---|
| 操作数字段缺失 | `MISSING_FIELD` |
| 不能解析为有限数的字符串、布尔、null | `NON_NUMERIC` |
| `Infinity`、`NaN` 等非有限值 | `NON_NUMERIC` |
| IR 形状不受支持 | `UNSUPPORTED_IR` |

非法输入不得被静默当成 false，也不得计入 fixture 通过

## 与旧全 `<` 映射的关系

B R1 只交付了 `b-pe-less-than-industry@1`
旧 `claim_rule_mapping.json` 将全部 claim 作为 `<` 候选，状态为 `superseded_by: c-claim-map-v1`，不得作为当前映射使用
`fixtures/a_100/claim-map-v1.json` 按 claim 逐条映射为 `<` 48 条、`>` 46 条、`==` 6 条
C 运行时只读这张静态映射，不再假设单一 `<`
本目录三条规则对应这三个 op，但 claim 到规则的映射仍由 C 的 `c-claim-map-v1` 负责，B 不生成也不改写该映射

## 与 C 临时模板的关系

C PR #5 将 `ledger/claim_map.py` 当前规则 ID 切换为本目录的三条正式 ID
`claim-map-v2.json` 使用正式 ID，每行保留 legacy_rule_id，顶层记录原 v1 映射摘要
`claim-map-v1.json` 仍保留三条临时 ID 及原 provenance，作为历史复核输入，只读兼容
B 复算默认使用 v2；复算历史 v1 run 时显式指定 `--claim-map fixtures/a_100/claim-map-v1.json`
摘要不匹配会拒绝复算，不自动迁移历史 run；样例对齐不表示全部数值边界或整体验收通过

## 验证

```bash
.venv\Scripts\python -m pytest -q tests/test_b_rules_v1.py
```

测试覆盖

- `rules-v1.json` 的版本、三条 rule_id、原文、声明字段与布尔开关
- 三条规则均编译为受支持的 `b-cmp-v1` IR，且与 `compiled-v1.json` 中的 IR 一致
- 9 条 fixture 全部由 `evaluate_cmp` 实际执行，结果与期望值、记录值一致
- 使用实际执行结果构造 `RuleSet`，断言 `c_numerator=3`、`c_rate=1.0`
- 缺字段、非数值、非有限值三类非法输入返回 `INDETERMINATE` 和对应原因码

## 未声明事项

- C 已接入正式规则，验证范围见 `docs/handoff/B_C_R2_复核_2026-11-05.md`
- 未声明 A 的证据包已通过本规则重跑
- 未声明自然语言 claim 语义已验证，本目录只覆盖数值谓词
- 未声明 100 条真实数据、删除测试、M10 或完整 ARS 验收通过
- 未声明 fixture 通过等于业务结论成立
