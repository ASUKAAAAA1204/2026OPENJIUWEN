"""Versioned deterministic redaction for JSON snapshots; no model/network calls.

结构化字段（ID、哈希、引用、时间戳、枚举）保持原值，避免脱敏规则改写
账本绑定字段；自由文本仍按正则脱敏。
"""
import re
class RedactionError(ValueError):pass
_SENSITIVE_FRAGMENTS=('password','api_key','apikey','token','authorization','secret','credential')
_STRUCTURAL_EXACT=frozenset({
 'seq','ts','at','ref','hash','type','gate','status','class','decision_class',
 'risk','version','weight','opened_seq','event_type','record_kind','registry_event',
 'exec_policy','whitelist_version','policy_version','args_after','content_hash',
 'prev_hash','record_hash','gate_latency_ms','source_level','original_severity',
 'display_severity','mark_type','method','result','approve_result','model_id','pid',
})
_STRUCTURAL_SUFFIX=('_id','_ref','_digest','_hash','_at','_ms','_seq')
class Redactor:
    def __init__(self,version,patterns=(),sensitive_keys=('password','api_key','token','authorization','secret')):
        if not isinstance(version,str) or not version.strip():raise RedactionError('rule version required')
        self.version=version
        self.patterns=tuple(re.compile(p) for p in patterns)
        self.sensitive_keys=frozenset(k.casefold() for k in sensitive_keys)
    def _sensitive(self,key):
        lowered=key.casefold()
        return lowered in self.sensitive_keys or any(
            fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)
    @staticmethod
    def _structural(key):
        lowered=key.casefold()
        return lowered in _STRUCTURAL_EXACT or lowered.endswith(_STRUCTURAL_SUFFIX)
    def redact(self,value):
        def visit(item,depth=0,structural=False):
            if depth>64:raise RedactionError('structure exceeds depth limit')
            if isinstance(item,str):
                if structural:return item
                for pattern in self.patterns:item=pattern.sub('[REDACTED]',item)
                return item
            if item is None or type(item) in (bool,int):return item
            if type(item) is float:
                import math
                if not math.isfinite(item):raise RedactionError('non-finite number')
                return item
            if isinstance(item,list):return [visit(v,depth+1) for v in item]
            if isinstance(item,dict):
                result={}
                for key,val in item.items():
                    if not isinstance(key,str):raise RedactionError('non-string JSON key')
                    # 键名仍过正则，保持既有碰撞检测语义
                    safe_key=visit(key,depth+1)
                    if safe_key in result:raise RedactionError('redaction causes key collision')
                    if self._sensitive(key):result[safe_key]='[REDACTED]'
                    else:result[safe_key]=visit(val,depth+1,self._structural(key))
                return result
            raise RedactionError('unsupported JSON type')
        return {'redaction_version':self.version,'value':visit(value)}
