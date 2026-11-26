"""Dispatch the workspace tool through an OpenAI Agents SDK function_tool and Runner."""
from __future__ import annotations

import json
from importlib.util import find_spec

from .file_turn import TOOL, Choose, Write, configured

UNAVAILABLE_REASON = "OpenAI Agents SDK 未安装"


def sdk_installed() -> bool:
    return find_spec("agents") is not None


async def dispatch(turn_id: str, choose: Choose, write: Write):
    from agents import Agent, Model, ModelResponse, RunConfig, Runner, Usage, function_tool
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    class TurnModel(Model):
        async def get_response(self, *args, **kwargs):
            step = await choose()
            if step.call_id is None:
                output = ResponseOutputMessage(id=turn_id, role="assistant", type="message", status="completed",
                                               content=[ResponseOutputText(text=step.text, annotations=[], type="output_text")])
            else:
                output = ResponseFunctionToolCall(call_id=step.call_id, name=TOOL, type="function_call",
                                                  arguments=json.dumps(step.arguments, ensure_ascii=False))
            return ModelResponse(output=[output], usage=Usage(), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError("This worker uses Runner.run")

    registered = function_tool(name_override=TOOL, failure_error_function=None)(write)
    agent = Agent(name="workspace_file_agent", model=TurnModel(), tools=[registered],
                  tool_use_behavior="stop_on_first_tool")
    result = await Runner.run(agent, input="Process the current task.", max_turns=2,
                              run_config=RunConfig(tracing_disabled=True))
    receipts = [(item.raw_item["call_id"], item.output) for item in result.new_items
                if item.type == "tool_call_output_item"]
    if not receipts:
        return None, result.final_output
    item, = receipts
    if item[1] != result.final_output:
        raise RuntimeError("Agents SDK 最终输出与工具回执不同")
    return item


def create_adapter():
    if not sdk_installed():
        return None
    from agents import Runner  # noqa: F401
    return configured("OpenAI Agents SDK", "openai-agents", "0.25.0", dispatch)
