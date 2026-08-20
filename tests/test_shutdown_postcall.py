"""Shutdown / post-call timeout behaviour.

Run:

    python -m unittest tests.test_shutdown_postcall -v
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import postcall, settings  # noqa: E402
from src.state import CallState  # noqa: E402


class PostCallTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_returns_partial_payload_on_analysis_timeout(self) -> None:
        state = CallState(call_id="timeout-test", case_type="accident")
        state.record_name("Ada", "Lovelace")
        session = MagicMock()

        async def _slow_analyze(*_args, **_kwargs):
            await asyncio.sleep(5)
            raise AssertionError("analyze should have been cancelled by wait_for")

        with tempfile.TemporaryDirectory() as tmp:
            fake_post = SimpleNamespace(
                webhook_url="",
                webhook_timeout_ms=1000,
                analysis_timeout_ms=50,
                records_dir=Path(tmp),
            )
            with (
                patch.object(settings, "post_call", fake_post),
                patch.object(postcall, "analyze", side_effect=_slow_analyze),
            ):
                payload = await postcall.run(
                    session,
                    state,
                    transcript="User: hello\nAgent: hi",
                    transcript_object=[
                        {"role": "user", "content": "hello"},
                        {"role": "agent", "content": "hi"},
                    ],
                )

            files = list(Path(tmp).glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(payload["call"]["call_id"], "timeout-test")
            self.assertEqual(payload["call"]["transcript"], "User: hello\nAgent: hi")
            self.assertEqual(
                payload["call"]["call_analysis"]["custom_analysis_data"]["user_fname"],
                "Ada",
            )
            self.assertIsNone(payload["call"]["call_analysis"]["call_summary"])

    async def test_webhook_unset_logs_at_debug_not_info(self) -> None:
        state = CallState(call_id="no-webhook")
        payload = postcall._base_payload(state, "", [])

        with tempfile.TemporaryDirectory() as tmp:
            fake_post = SimpleNamespace(
                webhook_url="",
                webhook_timeout_ms=1000,
                analysis_timeout_ms=8000,
                records_dir=Path(tmp),
            )
            with patch.object(settings, "post_call", fake_post):
                with self.assertLogs("bushbush.postcall", level="DEBUG") as cm:
                    await postcall.deliver(payload)

        joined = "\n".join(cm.output)
        self.assertIn("POST_CALL_WEBHOOK_URL not set", joined)
        # Must not appear as INFO (assertLogs includes DEBUG+; check level tag).
        self.assertTrue(
            any(
                "DEBUG:bushbush.postcall:POST_CALL_WEBHOOK_URL not set" in line
                for line in cm.output
            )
        )


if __name__ == "__main__":
    unittest.main()
