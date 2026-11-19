"""Versioned deterministic redaction for JSON snapshots; no model/network calls."""
import re
class RedactionError(ValueError):pass
class Redactor:
    def __init__(self,version,patterns=(),sensitive_keys=('password','api_key','token','authorization','secret')):
        if not isinstance(version,str) or not version.strip():raise RedactionError('rule version required')
        self.version=version
        self.patterns=tuple(re.compile(p) for p in patterns)
        self.sensitive_keys=frozenset(k.casefold() for k in sensitive_keys)
    def redact(self,value):
        def visit(item,depth=0):
            if depth>64:raise RedactionError('structure exceeds depth limit')
            if isinstance(item,str):
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
                    # Redact sensitive values and pattern matches in keys as well.
                    safe_key=visit(key,depth+1)
                    if safe_key in result:raise RedactionError('redaction causes key collision')
                    result[safe_key]='[REDACTED]' if key.casefold() in self.sensitive_keys else visit(val,depth+1)
                return result
            raise RedactionError('unsupported JSON type')
        return {'redaction_version':self.version,'value':visit(value)}
