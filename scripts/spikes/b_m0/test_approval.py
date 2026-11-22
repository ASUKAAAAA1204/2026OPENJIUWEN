"""Boundary cases outside the 27 measured SDK approval trials."""
import asyncio
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from adapters import REQUESTED
from approval import PendingApproval, args_digest
from run import check_jiuwen_lock
from worker import APPROVED, WriteTrial


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_bound_response_can_release_one_write(self):
        with TemporaryDirectory() as directory:
            approval = PendingApproval()
            trial = WriteTrial(Path(directory), "ASK", approval=approval)
            call = asyncio.create_task(trial.guarded_call(REQUESTED))
            request = await asyncio.wait_for(approval.opened, 1)
            for other in (replace(request, approval_id="another-approval"),
                          replace(request, call_id="another-call"),
                          replace(request, args_digest=args_digest(APPROVED))):
                self.assertFalse(approval.respond(other, "approve"))
            self.assertFalse(approval.respond(request, "timeout"))
            self.assertFalse(call.done())
            self.assertEqual(list(trial.directory.iterdir()), [])
            self.assertTrue(approval.respond(request, "approve"))
            self.assertFalse(approval.respond(request, "approve"))
            receipt = await asyncio.wait_for(call, 1)
            with self.assertRaises(RuntimeError):
                await trial.guarded_call(REQUESTED)
            self.assertEqual(trial.executions, [REQUESTED])
            self.assertEqual((trial.directory / REQUESTED).read_text().splitlines(), [receipt])

    async def test_at_deadline_and_after_timeout_cannot_release_a_write(self):
        with TemporaryDirectory() as directory:
            approval = PendingApproval(timeout_seconds=0.05)
            trial = WriteTrial(Path(directory), "ASK", approval=approval)
            call = asyncio.create_task(trial.guarded_call(REQUESTED))
            request = await asyncio.wait_for(approval.opened, 1)
            # Test a reply at the deadline before the waiting task has processed its timer.
            with patch.object(approval.loop, "time", return_value=approval.deadline):
                self.assertFalse(approval.respond(request, "approve"))
            self.assertEqual(await asyncio.wait_for(call, 1), f"timeout_denied:{trial.call_id}")
            self.assertFalse(approval.respond(request, "approve"))
            self.assertEqual(trial.executions, [])
            self.assertEqual(list(trial.directory.iterdir()), [])
            self.assertEqual(len(trial.terminals), 1)


class LockTests(unittest.TestCase):
    def test_version_drift_missing_and_extra_packages_fail_together(self):
        with TemporaryDirectory() as directory:
            lock = Path(directory) / "lock.txt"
            manifest = Path(directory) / "installed.json"
            lock.write_text("Some_Package==1\nother==2\n")
            manifest.write_text(json.dumps({"some-package": "9", "extra": "1", "pip": "1"}))
            result = check_jiuwen_lock(lock, manifest)
            self.assertFalse(result["passed"])
            self.assertEqual(set(result["differences"]), {"some-package", "other"})
            self.assertEqual(result["unexpected"], ["extra"])
