"""Offline regression tests for the evidence boundary of the opt-in live test."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from adapters import REQUESTED, TOOL
from controlled import WriteTrial
from online import parse_choice, sample


def completion(*calls, reason="tool_calls"):
    return NS(choices=[NS(message=NS(tool_calls=calls), finish_reason=reason)])


def action(name=TOOL, target=REQUESTED, call_id="test-id"):
    return NS(type="function", id=call_id, function=NS(name=name, arguments=json.dumps({"target": target})))


class Boundaries(unittest.IsolatedAsyncioTestCase):
    def test_tool_choices_are_bound_before_dispatch(self):
        accepted = parse_choice(completion(action()))
        self.assertEqual((accepted.call_id, accepted.name, accepted.arguments), ("test-id", TOOL, {"target": REQUESTED}))
        for rejected in (completion(), completion(action(), action()), completion(action(target="../other.txt")),
                         completion(action(name="other")), completion(action(call_id="bad\n")),
                         completion(action(), reason="stop")):
            with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                parse_choice(rejected)

    async def test_refusal_never_records_exception_text_or_disk_receipt(self):
        async def dispatch(run_id, operation, *, choose):
            await choose()
            self.fail("provider error should have stopped dispatch")

        client = NS(chat=NS(completions=NS(create=AsyncMock(side_effect=RuntimeError("private-provider-canary")))))
        with TemporaryDirectory() as temp, patch.dict("online.ADAPTERS", {"openjiuwen": dispatch}), patch("online.metadata.version", return_value="test"):
            path = Path(temp)
            result = await sample("openjiuwen", path, {"model": "test", "max_tokens": 10, "timeout_seconds": 1}, client)
            self.assertFalse(result["passed"])
            self.assertEqual(result["provider_requests"], 1)
            self.assertEqual(result["failure"]["stage"], "provider_connection")
            self.assertNotIn("private-provider-canary", json.dumps(result))
            self.assertEqual(list(path.iterdir()), [])

    async def test_classified_tool_cannot_opt_into_unhooked_policy(self):
        with TemporaryDirectory() as temp:
            trial = WriteTrial(Path(temp), "UNHOOKED")
            with self.assertRaises(ValueError):
                await trial.guarded_call(REQUESTED)
            self.assertEqual(trial.events, [])
            self.assertEqual(list(Path(temp).iterdir()), [])
