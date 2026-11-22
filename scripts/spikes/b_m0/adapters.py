"""Dispatch one scripted tool request through each real SDK. Imports stay isolated."""
import json
from collections.abc import Awaitable, Callable

TOOL = "write_probe"
REQUESTED = "requested.txt"
Tool = Callable[[str], str | Awaitable[str]]
Replies = list[tuple[str, str]]


async def openjiuwen_call(call_id: str, guarded_call: Tool) -> Replies:
    from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
    from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig

    replies: Replies = []

    class ToolFirstModel:
        # PR8's two-turn driver. Read back the SDK's tool messages on the second turn.
        async def invoke(self, *, messages, **kwargs):
            for message in messages:
                item = message if isinstance(message, dict) else message.model_dump()
                if item["role"] == "tool":
                    replies.append((item["tool_call_id"], item["content"]))
            if replies:
                return AssistantMessage(content="finished")
            return AssistantMessage(content="", tool_calls=[ToolCall(
                id=call_id, name=TOOL, type="function",
                arguments=json.dumps({"target": REQUESTED}),
            )])

    agent = ReActAgent(AgentCard(id=call_id, name="write_probe_agent", description="M0 SDK probe"))
    agent.configure(ReActAgentConfig().configure_model("scripted").configure_max_iterations(3))
    agent.set_llm(ToolFirstModel())
    card = ToolCard(id=TOOL, name=TOOL, description="Write a local test receipt",
                    input_params={"type": "object", "properties": {"target": {"type": "string"}},
                                  "required": ["target"]})
    tool = LocalFunction(card, guarded_call)
    agent.ability_manager.add_ability(tool.card, tool)
    try:
        answer = await agent.invoke({"query": "Write a test receipt.", "conversation_id": call_id})
        if answer.get("output") != "finished":
            raise ValueError("openJiuwen did not complete the tool turn")
    finally:
        agent.ability_manager.teardown_tools()
    return replies


async def langgraph_call(call_id: str, guarded_call: Tool) -> Replies:
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import tool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode([tool(TOOL)(guarded_call)]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    request = AIMessage(content="", tool_calls=[
        {"type": "tool_call", "id": call_id, "name": TOOL, "args": {"target": REQUESTED}},
    ])
    result = await builder.compile().ainvoke({"messages": [request]})
    return [(msg.tool_call_id, msg.content) for msg in result["messages"]
            if isinstance(msg, ToolMessage) and msg.name == TOOL and msg.status == "success"]


async def agents_call(call_id: str, guarded_call: Tool) -> Replies:
    from agents import Agent, Model, ModelResponse, RunConfig, Runner, Usage, function_tool
    from openai.types.responses import ResponseFunctionToolCall

    class ToolFirstModel(Model):
        async def get_response(self, *args, **kwargs):
            return ModelResponse(output=[ResponseFunctionToolCall(
                call_id=call_id, name=TOOL, arguments=json.dumps({"target": REQUESTED}),
                type="function_call",
            )], usage=Usage(), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError("This probe uses Runner.run")

    tool = function_tool(name_override=TOOL, failure_error_function=None)(guarded_call)
    agent = Agent(name="write_probe_agent", model=ToolFirstModel(), tools=[tool],
                  tool_use_behavior="stop_on_first_tool")
    result = await Runner.run(agent, "Write a test receipt.", max_turns=2,
                              run_config=RunConfig(tracing_disabled=True))
    replies = [(item.raw_item["call_id"], item.output) for item in result.new_items
               if item.type == "tool_call_output_item"]
    if len(replies) != 1 or result.final_output != replies[0][1]:
        raise ValueError("Agents SDK did not return exactly one function tool result")
    return replies


ADAPTERS = {"openjiuwen": openjiuwen_call, "langgraph": langgraph_call, "agents": agents_call}
DISTRIBUTIONS = {"openjiuwen": "openjiuwen", "langgraph": "langgraph", "agents": "openai-agents"}
