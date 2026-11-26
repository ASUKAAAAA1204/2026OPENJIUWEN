"""Run the registered, centrally guarded file tool in openJiuwen's ReActAgent."""
from __future__ import annotations

import json
from importlib.util import find_spec

from .file_turn import PARAMETERS, TOOL, Choose, Write, configured

UNAVAILABLE_REASON = "openJiuwen SDK 未安装"


def sdk_installed() -> bool:
    return find_spec("openjiuwen") is not None


async def dispatch(turn_id: str, choose: Choose, write: Write):
    from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
    from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig

    receipts = []

    class TurnModel:
        async def invoke(self, *, messages, **kwargs):
            for message in messages:
                item = message if isinstance(message, dict) else message.model_dump()
                if item["role"] == "tool":
                    receipts.append((item["tool_call_id"], item["content"]))
            if receipts:
                # End this bounded turn with the real tool response, without requesting a second inference.
                return AssistantMessage(content=receipts[-1][1])
            step = await choose()
            if step.call_id is None:
                return AssistantMessage(content=step.text)
            return AssistantMessage(content="", tool_calls=[ToolCall(
                id=step.call_id, name=TOOL, type="function",
                arguments=json.dumps(step.arguments, ensure_ascii=False),
            )])

    agent = ReActAgent(AgentCard(id=turn_id, name="workspace_file_agent", description="受控文件助手"))
    agent.configure(ReActAgentConfig().configure_model("workspace-file-model").configure_max_iterations(3))
    agent.set_llm(TurnModel())
    registered = LocalFunction(ToolCard(id=TOOL, name=TOOL, description="写入授权工作目录中的文件",
                                        input_params=PARAMETERS), write)
    agent.ability_manager.add_ability(registered.card, registered)
    try:
        result = await agent.invoke({"query": "Process the current task.", "conversation_id": turn_id})
        if not receipts:
            return None, result.get("output")
        item, = receipts
        if item[1] != result.get("output"):
            raise RuntimeError("openJiuwen 最终输出与工具回执不同")
        return item
    finally:
        agent.ability_manager.teardown_tools()


def create_adapter():
    if not sdk_installed():
        return None
    from openjiuwen.core.single_agent import ReActAgent  # noqa: F401
    return configured("openJiuwen", "openjiuwen", "0.1.20.post1", dispatch)
