"""Real SDK dispatch. Offline fixtures and live providers supply the same tool-call shape."""
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

TOOL = "write_probe"
REQUESTED = "requested.txt"
Tool = Callable[[str], str | Awaitable[str]]
Replies = list[tuple[str, str]]
Pause = Callable[[str, str], None]


@dataclass(frozen=True)
class ToolRequest:
    call_id: str
    name: str
    arguments: dict


Choose = Callable[[], Awaitable[ToolRequest]]


def scripted(call_id: str, name: str, target: str) -> Choose:
    async def choose():
        return ToolRequest(call_id, name, {"target": target})
    return choose


async def openjiuwen_call(call_id: str, guarded_call: Tool, *, target=REQUESTED, name=TOOL,
                          on_pause: Pause | None = None, choose: Choose | None = None) -> Replies:
    from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
    from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
    from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig

    select = choose or scripted(call_id, name, target)
    replies: Replies = []

    class ToolModel:
        async def invoke(self, *, messages, **kwargs):
            for message in messages:
                item = message if isinstance(message, dict) else message.model_dump()
                if item["role"] == "tool":
                    replies.append((item["tool_call_id"], item["content"]))
            if replies:
                return AssistantMessage(content="finished")
            action = await select()
            return AssistantMessage(content="", tool_calls=[ToolCall(
                id=action.call_id, name=action.name, type="function",
                arguments=json.dumps(action.arguments),
            )])

    agent = ReActAgent(AgentCard(id=call_id, name="write_probe_agent", description="M0 SDK probe"))
    agent.configure(ReActAgentConfig().configure_model("probe-provider").configure_max_iterations(3))
    agent.set_llm(ToolModel())
    card = ToolCard(id=name, name=name, description="Execute the registered probe",
                    input_params={"type": "object", "properties": {"target": {"type": "string"}},
                                  "required": ["target"]})
    tool = LocalFunction(card, guarded_call)
    agent.ability_manager.add_ability(tool.card, tool)
    try:
        if on_pause:
            from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail
            await agent.register_rail(ConfirmInterruptRail(tool_names=[name]))
        answer = await agent.invoke({"query": "Run the requested probe.", "conversation_id": call_id})
        if on_pause:
            from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
            if answer.get("result_type") != "interrupt":
                raise ValueError("openJiuwen did not suspend")
            state, = answer["state"]
            request = state.payload.value
            if request.tool_name != name or request.tool_call_id != call_id or not request.interrupt_id:
                raise ValueError("openJiuwen interrupt does not match the original call")
            on_pause(request.interrupt_id, request.tool_call_id)
            response = InteractiveInput()
            response.answer(request.interrupt_id, {"approved": True, "feedback": "", "auto_confirm": False})
            answer = await agent.invoke({"query": response, "conversation_id": call_id})
        if answer.get("output") != "finished":
            raise ValueError("openJiuwen did not complete the tool turn")
    finally:
        agent.ability_manager.teardown_tools()
    return replies


async def langgraph_call(call_id: str, guarded_call: Tool, *, target=REQUESTED, name=TOOL,
                         on_pause: Pause | None = None, choose: Choose | None = None) -> Replies:
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import tool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    select = choose or scripted(call_id, name, target)

    async def model_node(state):
        action = await select()
        message = AIMessage(content="", tool_calls=[{"id": action.call_id, "type": "tool_call",
                                                      "name": action.name, "args": action.arguments}])
        return {"messages": [message]}

    builder = StateGraph(MessagesState)
    builder.add_node("model", model_node)
    builder.add_node("tools", ToolNode([tool(name)(guarded_call)]))
    builder.add_edge(START, "model")
    builder.add_edge("tools", END)
    checkpointer = None
    if on_pause:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command, interrupt

        def hold(state):
            action, = state["messages"][-1].tool_calls
            approved = interrupt({"call_id": action["id"], "tool_name": action["name"]})
            if approved != action["id"]:
                raise ValueError("LangGraph resumed a different call")
            return {}

        checkpointer = InMemorySaver()
        builder.add_node("hold", hold)
        builder.add_edge("model", "hold")
        builder.add_edge("hold", "tools")
    else:
        builder.add_edge("model", "tools")

    graph = builder.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": call_id}}
    result = await graph.ainvoke({"messages": []}, config)
    if on_pause:
        interruption, = result["__interrupt__"]
        if interruption.value != {"call_id": call_id, "tool_name": name}:
            raise ValueError("LangGraph interrupt binding mismatch")
        on_pause(interruption.id, interruption.value["call_id"])
        result = await graph.ainvoke(Command(resume={interruption.id: call_id}), config)
    return [(msg.tool_call_id, msg.content) for msg in result["messages"]
            if isinstance(msg, ToolMessage) and msg.name == name and msg.status == "success"]


def agents_model(select: Choose):
    from agents import Model, ModelResponse, Usage
    from openai.types.responses import ResponseFunctionToolCall

    class ToolModel(Model):
        async def get_response(self, *args, **kwargs):
            action = await select()
            return ModelResponse(output=[ResponseFunctionToolCall(
                call_id=action.call_id, name=action.name, arguments=json.dumps(action.arguments),
                type="function_call",
            )], usage=Usage(), response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError("The probe uses Runner.run")

    return ToolModel()


async def agents_call(call_id: str, guarded_call: Tool, *, target=REQUESTED, name=TOOL,
                      on_pause: Pause | None = None, choose: Choose | None = None) -> Replies:
    from agents import Agent, RunConfig, Runner, function_tool

    tool = function_tool(name_override=name, failure_error_function=None,
                         needs_approval=on_pause is not None)(guarded_call)
    agent = Agent(name="write_probe_agent", model=agents_model(choose or scripted(call_id, name, target)),
                  tools=[tool], tool_use_behavior="stop_on_first_tool")
    config = RunConfig(tracing_disabled=True)
    result = await Runner.run(agent, "Run the requested probe.", max_turns=2, run_config=config)
    if on_pause:
        item, = result.interruptions
        if item.raw_item.name != name or item.raw_item.call_id != call_id:
            raise ValueError("Agents SDK interruption binding mismatch")
        on_pause(item.raw_item.call_id, item.raw_item.call_id)
        state = result.to_state()
        state.approve(item)
        result = await Runner.run(agent, state, run_config=config)
    replies = [(item.raw_item["call_id"], item.output) for item in result.new_items
               if item.type == "tool_call_output_item"]
    if len(replies) != 1 or result.final_output != replies[0][1]:
        raise ValueError("Agents SDK did not return exactly one function tool result")
    return replies


async def agents_handoff(parent_id: str, child_id: str, child_target: str,
                         delegate: Tool, child_tool: Tool) -> dict:
    from agents import Agent, RunConfig, Runner, function_tool, handoff
    from pydantic import BaseModel

    class DelegateArgs(BaseModel):
        target: str

    async def transfer(context, args: DelegateArgs):
        await delegate(args.target)

    child = Agent(name="probe_child", model=agents_model(scripted(child_id, TOOL, child_target)),
                  tools=[function_tool(name_override=TOOL, failure_error_function=None)(child_tool)],
                  tool_use_behavior="stop_on_first_tool")
    route = handoff(child, tool_name_override="delegate_probe", input_type=DelegateArgs, on_handoff=transfer)
    parent = Agent(name="probe_parent", model=agents_model(scripted(parent_id, "delegate_probe", "delegate.txt")),
                   handoffs=[route])
    result = await Runner.run(parent, "Delegate to the child.", max_turns=3,
                              run_config=RunConfig(tracing_disabled=True))
    handoff_call, = (i for i in result.new_items if i.type == "handoff_call_item")
    transferred, = (i for i in result.new_items if i.type == "handoff_output_item")
    child_output, = (i for i in result.new_items if i.type == "tool_call_output_item")
    return {"parent_call_id": handoff_call.raw_item.call_id, "from_agent": transferred.source_agent.name,
            "to_agent": transferred.target_agent.name, "result_agent": child_output.agent.name,
            "parent_replies": [(transferred.raw_item["call_id"], transferred.raw_item["output"])],
            "child_replies": [(child_output.raw_item["call_id"], child_output.output)]}


ADAPTERS = {"openjiuwen": openjiuwen_call, "langgraph": langgraph_call, "agents": agents_call}
DISTRIBUTIONS = {"openjiuwen": "openjiuwen", "langgraph": "langgraph", "agents": "openai-agents"}
