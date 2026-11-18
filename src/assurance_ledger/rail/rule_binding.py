"""A 接收 B R1 的数据适配层；不导入 B 的候选 core，不编译或判定结论。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path, PurePosixPath

from ..core.claim import Claim, Locator
from .evidence_io import digest_json, parse_json


RULE_FILES = (
    "RETURN_MANIFEST.json", "interfaces/rules.json", "interfaces/compiled_rules.json",
    "interfaces/field_mapping.json", "interfaces/claim_rule_mapping.json",
    "interfaces/C_alignment_request.json", "cases/fixtures.json",
    "results/report.json", "results/a_case.json",
)
FIELD_MAPPING = {
    "pe": "claim_sources.locators.pe -> source.csv row/pe",
    "industry_pe": "claim_sources.locators.industry_pe -> same row/industry_pe",
}


def _index(rows: list, key: str) -> dict:
    if not isinstance(rows, list) or not rows:
        raise ValueError("B 规则或映射清单为空")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError("B 规则或映射缺少编号")
        if row[key] in result:
            raise ValueError("B 规则或映射编号重复")
        result[row[key]] = row
    return result


class BRuleBinding:
    """仅接受已核对的 b-pe-v1/b-cmp-v1 候选数据；C 的确认仍单独待办。"""

    def __init__(self, root: Path):
        root = root.resolve()
        checksum_bytes = (root / "PACKAGE_CHECKSUMS.json").read_bytes()
        checksums = parse_json(checksum_bytes.decode("utf-8-sig"))
        listed = _index(checksums["files"], "path")
        self.raw_files = {"PACKAGE_CHECKSUMS.json": checksum_bytes}
        # 先校验整批返回内容，仅读取，不执行附件中的代码。
        for name, item in listed.items():
            relative = PurePosixPath(name)
            path = (root / name).resolve()
            if (relative.is_absolute() or "\\" in name or ":" in name
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or not path.is_relative_to(root)):
                raise ValueError("B 清单含不安全的相对路径")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"] or len(raw) != item["bytes"]:
                raise ValueError(f"B 返回文件摘要或大小不匹配: {name}")
            if name in RULE_FILES:
                self.raw_files[name] = raw
        if not set(RULE_FILES) <= self.raw_files.keys():
            raise ValueError("B 校验清单缺少规则接入文件")
        docs = {name: parse_json(self.raw_files[name].decode("utf-8-sig")) for name in RULE_FILES}
        self.manifest = docs["RETURN_MANIFEST.json"]
        if (self.manifest.get("role") != "B" or self.manifest.get("batch") != "R1"
                or self.manifest.get("template_only") is not False
                or not self.manifest.get("return_version")):
            raise ValueError("需要 B 已填写的 R1 返回清单")
        self.created_at = datetime.fromisoformat(self.manifest["created_at_with_timezone"])
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("B 返回时间必须包含时区，不能借用旧采集时间")
        rules = docs["interfaces/rules.json"]
        compiled = docs["interfaces/compiled_rules.json"]
        self.rule_version = rules.get("rule_version")
        self.ir_version = rules.get("ir_version")
        if self.rule_version != "b-pe-v1" or self.ir_version != "b-cmp-v1":
            raise ValueError("B 规则/IR 版本尚未接入")
        alignment = docs["interfaces/C_alignment_request.json"]
        if (alignment.get("status") != "pending_C_confirmation"
                or alignment.get("ir_version") != self.ir_version
                or self.manifest.get("rules", {}).get("ir_version") != self.ir_version
                or self.manifest.get("rules", {}).get("aligned_with_C_version") is not None):
            raise ValueError("C 确认协议有变化，需要按实际回传重新接入")
        self.definitions = _index(rules["rules"], "rule_id")
        self.compiled = _index(compiled["results"], "rule_id")
        if self.definitions.keys() != self.compiled.keys():
            raise ValueError("规则与编译结果编号不一致")
        self.skill_version = compiled["skill_version"]
        self.field_mapping = docs["interfaces/field_mapping.json"]
        if any(self.field_mapping.get(k) != v for k, v in FIELD_MAPPING.items()):
            raise ValueError("字段映射有变化，需要明确新的来源绑定方式")
        for rule_id, definition in self.definitions.items():
            result = self.compiled[rule_id]
            # R1 只有这条数值比较；不把新格式默认为已支持。
            expected_ir = {"type": "cmp", "op": "<", "left": {"field": "pe"},
                           "right": {"field": "industry_pe"}, "source_text": "pe < industry_pe"}
            if (rule_id != "b-pe-less-than-industry@1" or definition.get("text") != "pe < industry_pe"
                    or definition.get("declared_fields") != ["pe", "industry_pe"]
                    or definition.get("uses_llm") is not False or definition.get("has_locator") is not True
                    or result.get("compilable") is not True or result.get("fail_code") is not None
                    or result.get("original_text") != definition["text"] or result.get("ir") != expected_ir):
                raise ValueError("B R1 规则与可执行 IR 不一致或不在当前接入范围")
        cases = docs["cases/fixtures.json"]
        indexed_cases = _index(cases, "kind")
        if set(indexed_cases) != {"positive", "negative", "boundary"}:
            raise ValueError("B 缺少正例、反例或边界样例")
        for item in cases:
            if (type(item.get("expected")) is not bool or item.get("actual", {}).get("status") != "EVALUATED"
                    or item["actual"].get("value") is not item["expected"]):
                raise ValueError("B 返回的参考样例未通过")
        self.mappings = _index(docs["interfaces/claim_rule_mapping.json"], "claim_id")
        for mapping in self.mappings.values():
            if mapping.get("rule_id") not in self.definitions or mapping.get("mapping_status") != "candidate_pending_C":
                raise ValueError("claim 的规则关联无效，或超出候选关联范围")
        self.provenance = {
            "return_version": self.manifest["return_version"],
            "rule_snapshot_timestamp": self.created_at.isoformat(),
            "timestamp_basis": "B return creation time; separate from A source capture time",
            "based_on_handoff_revision": self.manifest.get("based_on_handoff_revision"),
            "received_A_manifest_sha256": self.manifest.get("received_manifest_sha256"),
            "file_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in self.raw_files.items()},
            "checksum_is_authentication": False,
        }
        self.capture_run_id = docs["results/a_case.json"]["meta"]["source_run_id"]
        self.capture_source_digest = docs["results/report.json"]["ars"]["coord"]["data_digest"]
        self.provenance.update({"capture_run_id": self.capture_run_id,
                                "capture_source_digest": self.capture_source_digest})

    def validate_claims(self, claims: list[Claim], requested_ids: list[str], run_id: str) -> None:
        if self.capture_run_id != run_id or set(self.mappings) != set(requested_ids):
            raise ValueError("B claim 关联与本次请求任务集合不同，不能遗漏或串批")
        for claim in claims:
            if (claim.skill_version != self.skill_version
                    or self.mappings[claim.id].get("statement") != claim.statement):
                raise ValueError(f"B 关联的结论文本或 Skill 版本不匹配: {claim.id}")

    def snapshot(self, claim: Claim, record: dict) -> tuple[dict, list[str]]:
        mapping = self.mappings[claim.id]
        rule_id = mapping["rule_id"]
        errors = []
        bindings = {}
        expected = record.get("expected_values", {})
        locators = record.get("locators", {})
        for field in FIELD_MAPPING:
            try:
                loc = Locator.model_validate(locators[field])
                value = expected[field]
                if isinstance(value, bool) or value is None or not Decimal(str(value)).is_finite():
                    raise ValueError("finite numeric operand required")
                if (loc.file != "source.csv" or loc.field != field or loc.row is None or loc.row < 1
                        or any(getattr(loc, k) is not None for k in ("line", "section", "free_text"))):
                    raise ValueError("precise field locator required")
                bindings[field] = {"locator": loc.model_dump(mode="json"), "captured_value": value,
                                   "source_sha256": record.get("source_sha256")}
            except (KeyError, TypeError, ValueError, InvalidOperation):
                errors.append(f"RULE_FIELD_BINDING_INVALID:{field}")
        if len(bindings) == 2:
            if (bindings["pe"]["locator"] != (claim.locator.model_dump(mode="json") if claim.locator else None)
                    or bindings["pe"]["locator"]["row"] != bindings["industry_pe"]["locator"]["row"]):
                errors.append("RULE_FIELD_ROW_MISMATCH")
        snapshot = {
            "format": "assurance-rule-binding-v1", "rule_id": rule_id,
            "rule_version": self.rule_version, "ir_version": self.ir_version,
            "status": "candidate_pending_C", "scope": "numeric predicate; natural language mapping pending C",
            "skill_version": self.skill_version,
            "definition": deepcopy(self.definitions[rule_id]), "compiled_result": deepcopy(self.compiled[rule_id]),
            "claim_mapping": deepcopy(mapping), "claim_digest": digest_json(claim.model_dump(mode="json")),
            "field_mapping": deepcopy(self.field_mapping), "field_bindings": bindings,
            "provenance": deepcopy(self.provenance),
        }
        return snapshot, errors
