"""只读配置快照：身份、策略、白名单、框架接入状态"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...auth import rbac
from ...ledger.canonical import canonical_bytes
from ...policy import catalog as policy_catalog
from ...policy.whitelist import parse_whitelist
from ...runs import lifecycle
from ...workers.registry import framework_states
from ..deps import platform_state, read_db, require_auth

router = APIRouter(prefix="/api/v27", tags=["settings"])


@router.get("/settings")
def read_settings(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    policy = lifecycle.active_policy(db)
    frameworks = framework_states(db, state.config)
    identity = rbac.identity_payload(context.user, state.config.security)
    policy_payload = None
    whitelist = None
    if policy is not None:
        policy_payload = {
            "version": policy.version,
            "content_hash": policy.content_hash,
            "rules": policy.configuration["rules"],
            "history": policy_catalog.history(db),
        }
        whitelist = parse_whitelist(policy.configuration["whitelist"]).payload()
    revision = hashlib.sha256(canonical_bytes({
        "identity": identity,
        "policy": policy.content_hash if policy else None,
        "frameworks": [[f["name"], f["version"], f["available"]]
                       for f in frameworks],
        "catalog": policy_catalog.catalog_revision(db),
    })).hexdigest()[:32]
    body = {
        "source": "ledger",
        "revision": revision,
        "identity": identity,
        "policy": policy_payload,
        "whitelist": whitelist,
        "frameworks": frameworks,
    }
    return JSONResponse(content=body, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
