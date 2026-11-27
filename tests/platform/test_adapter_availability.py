"""适配器可用性：缺 SDK 时显式不可用，不注册虚假能力"""
from __future__ import annotations

import pytest

from assurance_ledger.adapters import agents, langgraph, openjiuwen
from assurance_ledger.adapters.base import AdapterUnavailable, load_adapter


@pytest.mark.parametrize("module", [openjiuwen, langgraph, agents],
                         ids=["openJiuwen", "LangGraph", "AgentsSDK"])
def test_missing_sdk_returns_none_instead_of_a_fake_adapter(module):
    try:
        adapter = module.create_adapter()
    except AdapterUnavailable:
        # SDK 已安装但版本或模型配置不满足时显式不可用
        assert module.sdk_installed()
        return
    if not module.sdk_installed():
        assert adapter is None
    else:
        assert adapter is not None


def test_unknown_framework_is_rejected():
    with pytest.raises(AdapterUnavailable, match="未知框架"):
        load_adapter("not-a-framework")
