"""Run a ReAct agent through real SDK approval, MCP stdio and A's existing Rail."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail

from assurance_ledger.rail.collector import RailCollector
from assurance_ledger.rail.jiuwen_adapter import JiuwenCaptureRail


class ToolFirstModel:
    """Only the model response is scripted. The SDK dispatches and resumes the tool."""

    def __init__(self, name: str, ticket: str):
        self.name = name
        self.ticket = ticket

    async def invoke(self, *, messages, **kwargs):
        if any((m.get("role") if isinstance(m, dict) else m.role) == "tool" for m in messages):
            return AssistantMessage(content="completed")
        return AssistantMessage(content="", tool_calls=[ToolCall(
            id=f"call-{self.ticket}", name=self.name, type="function",
            arguments=json.dumps({"ticket": self.ticket}),
        )])


def read_writes(directory: Path):
    database = directory / "tickets.sqlite"
    updates = []
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            updates = [{"ticket": ticket, "write_id": write_id}
                       for ticket, write_id in db.execute("SELECT ticket, write_id FROM updates ORDER BY rowid")]
    notes_file = directory / "notes.jsonl"
    notes = [json.loads(row) for row in notes_file.read_text(encoding="utf-8").splitlines()] if notes_file.exists() else []
    return {"update_ticket": updates, "append_note": notes}


async def exercise_tool(tool, ticket: str, approve: bool | None, directory: Path):
    agent = ReActAgent(AgentCard(id=f"a-{ticket}", name=f"a-{ticket}", description="local MCP probe"))
    agent.configure(ReActAgentConfig().configure_model("scripted-local").configure_max_iterations(3))
    agent.set_llm(ToolFirstModel(tool.card.name, ticket))
    agent.ability_manager.add_ability(tool.card, tool)
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id=ticket, local_stub=True)
    await agent.register_rail(rail)
    if approve is not None:
        await agent.register_rail(ConfirmInterruptRail(tool_names=[tool.card.name]))
    conversation_id = f"a-probe-{ticket}"
    result = await agent.invoke({"query": ticket, "conversation_id": conversation_id})
    waiting = None
    unanswered = None
    waiting_hooks = []
    interrupt_id = None
    if approve is not None:
        if result.get("result_type") != "interrupt":
            raise RuntimeError(f"{ticket}: the SDK did not request approval")
        state, = result["state"]
        request = state.payload.value
        if request.tool_name != tool.card.name or request.tool_call_id != f"call-{ticket}" or not request.interrupt_id:
            raise RuntimeError(f"{ticket}: interrupt is not bound to this tool call")
        interrupt_id = request.interrupt_id
        waiting = sum(row["ticket"] == ticket for row in read_writes(directory)[tool.card.name])
        waiting_hooks = [hook for hook in rail.hooks_seen if "tool" in hook]
        if not approve:
            # Observe the bare SDK beyond the PRD's 30s deadline before the host replies.
            clock = asyncio.get_running_loop()
            started = clock.time()
            await asyncio.sleep(31)
            rows = read_writes(directory)[tool.card.name]
            unanswered = {"elapsed_seconds": round(clock.time() - started, 3),
                          "writes": sum(row["ticket"] == ticket for row in rows)}
        answer = InteractiveInput()
        answer.answer(interrupt_id, {"approved": approve, "feedback": "", "auto_confirm": False})
        result = await agent.invoke({"query": answer, "conversation_id": conversation_id})

    writes = [row for row in read_writes(directory)[tool.card.name] if row["ticket"] == ticket]
    observed = [event.model_dump(mode="json") for event in collector.get_stream().events if event.kind == "tool"]
    return {
        "ticket": ticket, "tool": tool.card.name, "request_id": interrupt_id, "call_id": f"call-{ticket}",
        "decision": {True: "approved", False: "rejected", None: "direct"}[approve],
        "waiting_writes": waiting, "waiting_hooks": waiting_hooks, "unanswered_observation": unanswered,
        "final_writes": len(writes),
        "write_ids": [row["write_id"] for row in writes], "result": result.get("output"),
        "rail_events": [{"tool": event["tool"], "call_id": event["extra"].get("tool_call_id")} for event in observed],
        "rail_hooks": [hook for hook in rail.hooks_seen if "tool" in hook],
        "internal_write_ids_seen": [row["write_id"] for row in writes if row["write_id"] in json.dumps(observed)],
    }


async def observe_tools(directory: Path):
    config = McpServerConfig(server_name="a-probe", server_path="local", client_type="stdio",
                             params={"command": sys.executable, "args": [
                                 str(Path(__file__).with_name("mcp_probe_server.py").resolve()), str(directory)]})
    client = StdioClient(config)
    if not await client.connect():
        raise RuntimeError("MCP stdio session failed to initialize")
    try:
        cards = {card.name: MCPTool(client, card) for card in await client.list_tools()}
        approved = await exercise_tool(cards["update_ticket"], "approved-17", True, directory)
        rejected = await exercise_tool(cards["update_ticket"], "rejected-17", False, directory)
        direct = await exercise_tool(cards["append_note"], "direct-17", None, directory)
    finally:
        await client.disconnect()
    cases = [approved, rejected, direct]
    performed = [approved, direct]
    store = read_writes(directory)
    visible = [c["tool"] for c in performed if {"tool": c["tool"], "call_id": c["call_id"]} in c["rail_events"]]
    extra_tools = {event["tool"] for c in performed for event in c["rail_events"] if event["tool"] != c["tool"]}
    return {
        "transport": "stdio", "cases": cases, "writes_read_back": store,
        "outer_tools_seen": visible, "additional_rail_tool_names": sorted(extra_tools),
        "unanswered_window_measured": rejected["unanswered_observation"]["elapsed_seconds"] >= 30,
        "approval_verified": (approved["waiting_writes"] == rejected["waiting_writes"] == 0
                              and approved["final_writes"] == 1 and rejected["final_writes"] == 0
                              and all(c["result"] == "completed" for c in cases)),
        "mcp_writes_verified": (len(store["update_ticket"]) == len(store["append_note"]) == 1
                                and len(visible) == 2 and all(c["final_writes"] == 1 for c in performed)),
    }
