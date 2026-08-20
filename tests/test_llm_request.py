"""Guards against the 400 that made the agent go silent on every turn.

    livekit.agents._exceptions.APIStatusError: Error code: 400 -
    "Invalid value for 'parallel_tool_calls': 'parallel_tool_calls' is only
     allowed when 'tools' are specified."

The router carries no tools. LiveKit's `inference/llm.py` copies whatever the
LLM was constructed with into every request without checking whether tools are
present, so `parallel_tool_calls=False` — and `tool_choice="none"` for the same
reason — made every router reply fail. The greeting still played because
`session.say()` is TTS only, which is why the call looked half-alive.

These tests assert on the request dict the plugin actually builds, so they fail
if anyone reintroduces either parameter.

Run:

    python -m unittest tests.test_llm_request -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OPENAI_API_KEY", "offline-test")

from src import models  # noqa: E402
from src.agents import AGENTS_BY_CASE_TYPE, RouterAgent  # noqa: E402


async def _request_extra(llm_obj, tools):
    """The keyword arguments the plugin would actually send to OpenAI."""
    from livekit.agents import llm as lkllm

    stream = llm_obj.chat(chat_ctx=lkllm.ChatContext.empty(), tools=tools)
    try:
        return dict(getattr(stream, "_extra_kwargs", {}) or {})
    finally:
        await stream.aclose()


class NoIllegalParamsOnToollessRequestsTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_llm_does_not_set_parallel_tool_calls(self) -> None:
        extra = await _request_extra(models.build_router_llm(), [])
        self.assertNotIn("parallel_tool_calls", extra)

    async def test_router_llm_does_not_set_tool_choice(self) -> None:
        extra = await _request_extra(models.build_router_llm(), [])
        self.assertNotIn("tool_choice", extra)

    async def test_intake_llm_does_not_set_parallel_tool_calls(self) -> None:
        extra = await _request_extra(models.build_llm(), [])
        self.assertNotIn("parallel_tool_calls", extra)

    async def test_temperature_still_reaches_the_request(self) -> None:
        """Proves the assertions above are reading a populated dict, not an
        empty one that would pass no matter what."""
        extra = await _request_extra(models.build_llm(), [])
        self.assertIn("temperature", extra)

    async def test_the_old_config_would_still_fail(self) -> None:
        """Documents the regression: constructing the LLM the old way puts the
        rejected parameter straight into a request that has no tools."""
        from livekit.plugins import openai

        bad = openai.LLM(model="gpt-4.1-nano", parallel_tool_calls=False)
        extra = await _request_extra(bad, [])
        self.assertEqual(extra.get("parallel_tool_calls"), False)


class AgentToolShapeTests(unittest.TestCase):
    def test_router_has_no_tools_and_no_llm_node_override(self) -> None:
        router = RouterAgent()
        self.assertEqual(list(router.tools), [])
        # An llm_node override is what used to force tool_choice="none" onto a
        # request that carried no tools. The base implementation must be used.
        from livekit.agents import Agent

        self.assertIs(type(router).llm_node, Agent.llm_node)

    def test_every_intake_agent_carries_a_tool(self) -> None:
        """`parallel_tool_calls` would be legal for these, but the rule is that
        no agent may reach OpenAI with an empty tool list and a tool parameter."""
        for case_type, cls in AGENTS_BY_CASE_TYPE.items():
            with self.subTest(case_type=case_type):
                names = sorted(t.info.name for t in cls().tools)
                self.assertEqual(names, ["end_call"])


if __name__ == "__main__":
    unittest.main()
