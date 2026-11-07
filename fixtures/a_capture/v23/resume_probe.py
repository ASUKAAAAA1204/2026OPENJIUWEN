"""N4: observe three actual native_v2 turns and two warm pause/resume cycles."""
import asyncio
from pathlib import Path

from openjiuwen.agent_teams.harness.protocol_adapter import NativeV2HarnessProvider
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.harness_protocol import (
    AfterToolResult, BeforePromptResult, HarnessCapability, HarnessContext, HarnessInput, HarnessState, HostCapability,
    ToolDecision, ToolDecisionKind, TurnEventKind, TurnLifecycleEvent,
)


class PromptObserver:
    def __init__(self):
        self.prompts = []

    async def before_prompt(self, context):
        self.prompts.append({"turn_id": context.turn_id, "prompt": context.prompt})
        return BeforePromptResult(prompt=context.prompt)

    async def before_tool(self, context):
        return ToolDecision(decision=ToolDecisionKind.PROVIDER_POLICY)

    async def after_tool(self, context):
        return AfterToolResult()

    async def on_stop(self, context):
        pass


class LocalModel:
    """Wait inside one model call so the host can pause a genuinely running turn."""

    def __init__(self):
        self.pause_next_call = False
        self.entered = asyncio.Event()

    async def stream(self, **kwargs):
        if self.pause_next_call:
            self.pause_next_call = False
            self.entered.set()
            await asyncio.Future()  # The SDK cancels the pending model call on pause.
        yield AssistantMessageChunk(content="probe completed")


async def observe_resume(directory: Path):
    task_id = "work-order-17"
    observer = PromptObserver()
    context = HarnessContext(agent_name="local-observer", agent_id="a-n4", host_session_id="n4-session",
                             system_prompt="", cwd=str(directory.resolve()), hooks=observer,
                             host_capabilities=frozenset({HostCapability.HOOKS}))
    harness = NativeV2HarnessProvider.create({"deep_agent": {
        "card": {"id": "a-n4", "name": "a-n4", "description": "offline resume probe"},
        "enable_sys_operation": False, "auto_create_workspace": False,
    }})
    model = LocalModel()
    turns = []
    await harness.start(context)
    try:
        session_id = harness.provider_session_id
        harness.native_harness.react_agent.set_llm(model)
        segment = 0
        for number in range(1, 4):
            pause = number <= 2
            model.pause_next_call = pause
            model.entered.clear()
            request = HarnessInput(content=f"{task_id} / turn {number}", metadata={"task_id": task_id})
            receipt = await harness.send(request)
            if pause:
                await model.entered.wait()
                await harness.pause()
                if harness.state != HarnessState.PAUSED:
                    raise RuntimeError("The SDK did not reach PAUSED")
                await harness.resume()
            events = []
            final_output = None
            async for envelope in harness.turn_events(receipt.turn_id):
                event = envelope.event
                if isinstance(event, TurnLifecycleEvent):
                    if event.result is not None:
                        final_output = event.result.final_output
                    if event.kind == TurnEventKind.RESUMED:
                        segment += 1
                    events.append({"sequence": envelope.sequence, "kind": event.kind.value,
                                   "provider_session_id": envelope.provider_session_id,
                                   "turn_id": envelope.turn_id, "segment": segment})
            turns.append({"host_task_id": request.metadata["task_id"], "turn_id": receipt.turn_id,
                          "events": events, "final_output": final_output})
    finally:
        await harness.stop()
    expected = [["started", "paused", "resumed", "finished"]] * 2 + [["started", "finished"]]
    phases = [[event["kind"] for event in turn["events"]] for turn in turns]
    sessions = {e["provider_session_id"] for t in turns for e in t["events"]}
    valid = (phases == expected and len({t["turn_id"] for t in turns}) == 3
             and all(e["turn_id"] == t["turn_id"] for t in turns for e in t["events"])
             and session_id is not None and sessions == {session_id}
             and all(t["final_output"] == "probe completed" for t in turns))
    return {"provider": harness.card.name, "mode": "warm resume", "turns": turns,
            "host_offers_hooks": HostCapability.HOOKS in context.host_capabilities,
            "provider_advertises_hooks": harness.card.supports(HarnessCapability.HOOKS),
            "before_prompt_calls": observer.prompts, "resume_cycles": segment,
            "lifecycle_verified": valid}
