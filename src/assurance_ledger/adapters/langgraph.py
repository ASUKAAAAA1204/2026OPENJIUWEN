"""Route a real model tool request through LangGraph's registered ToolNode."""
from __future__ import annotations

from importlib.util import find_spec

from .file_turn import TOOL, Choose, Write, configured

UNAVAILABLE_REASON = "LangGraph SDK 未安装"


def sdk_installed() -> bool:
    return find_spec("langgraph") is not None


async def dispatch(turn_id: str, choose: Choose, write: Write):
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import tool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    async def model_node(state):
        step = await choose()
        calls = [] if step.call_id is None else [{"id": step.call_id, "type": "tool_call",
                                                 "name": TOOL, "args": step.arguments}]
        return {"messages": [AIMessage(content=step.text, tool_calls=calls)]}

    def next_node(state):
        return "tools" if state["messages"][-1].tool_calls else END

    graph = StateGraph(MessagesState)
    graph.add_node("model", model_node)
    graph.add_node("tools", ToolNode([tool(TOOL)(write)], handle_tool_errors=False))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", next_node, ["tools", END])
    graph.add_edge("tools", END)
    result = await graph.compile().ainvoke({"messages": []}, {"configurable": {"thread_id": turn_id}})
    last = result["messages"][-1]
    if isinstance(last, ToolMessage) and last.name == TOOL and last.status == "success":
        return last.tool_call_id, last.content
    if isinstance(last, AIMessage) and not last.tool_calls:
        return None, last.content
    raise RuntimeError("LangGraph 没有返回预期的文件工具回执")


def create_adapter():
    if not sdk_installed():
        return None
    from langgraph.prebuilt import ToolNode  # noqa: F401
    return configured("LangGraph", "langgraph", "1.2.22", dispatch)
