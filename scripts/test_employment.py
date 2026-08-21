"""Offline walkthrough of an employment call, turn by turn.

    python scripts/test_employment.py

Companion to test_accident.py: no LiveKit room, no mic, no API spend. It drives
the real EmploymentAgent, the real capture path and the real end_call gate
through a workplace conversation, and checks the things that are specific to
this branch rather than the accident one:

  * "hurt at work" routes to employment, not premises or accident
  * the conflict-check field is the EMPLOYER, not another driver
  * end_call stays locked until the employer name has landed
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.employment import EmploymentAgent  # noqa: E402
from src.capture import auto_capture_from_utterance  # noqa: E402
from src.routing import classify_case_type  # noqa: E402
from src.state import CallState  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{f' - {detail}' if detail else ''}")
    if not ok:
        failures += 1


def _fake_ctx(state: CallState):
    handle = MagicMock()
    handle.wait_for_playout = AsyncMock()
    session = MagicMock()
    session.say = MagicMock(return_value=handle)
    session.aclose = AsyncMock()
    session.userdata = state
    return SimpleNamespace(session=session, userdata=state)


async def main() -> int:
    print("\nEmployment agent - offline conversation walkthrough\n")

    agent = EmploymentAgent(greet=False)

    print("Setup")
    check("case_type is employment", agent.case_type == "employment")
    check(
        "conflict check asks for the employer, not a driver",
        agent.other_party_label == "the employer or company",
        agent.other_party_label,
    )
    check(
        "exactly one tool, as on Retell",
        sorted(t.info.name for t in agent.tools) == ["end_call"],
    )
    check(
        "prompt is the employment intake",
        "workplace" in agent.instructions.lower()
        and "Bush and Bush" in agent.instructions,
    )

    print("\nRouting - these must reach employment, not premises or accident")
    for utterance in (
        "I was fired last week",
        "I got hurt at work",
        "I had an accident at work",
        "my boss hasn't paid my overtime wages",
        "I slipped on oil on the job",
    ):
        got = classify_case_type(utterance)
        check(f'"{utterance}"', got == "employment", got or "no match")

    print("\nThe call")
    state = CallState(case_type="employment")
    state.other_party_required = True

    def say(text: str) -> None:
        notes = auto_capture_from_utterance(state, text)
        print(f"       caller: {text}")
        if notes:
            print(f"       captured: {', '.join(notes)}")

    check("fresh call has outstanding must-haves", len(state.missing_must_haves()) == 6)

    say("I was let go after I reported a safety problem to HR.")
    say("Sure - Maria Gomez.")
    check("name captured", state.full_name == "Maria Gomez", state.full_name or "(empty)")

    say("My number is two one four, five five five, zero one nine nine.")
    check("full number accepted", state.phone == "2145550199", state.phone or "(empty)")
    say("Yes, that's correct.")
    check("read-back confirmed", state.phone_read_back)

    print("\n  Employer is still missing - end_call must stay locked.")
    state.callback_promised = True
    state.closing_offered = True
    state.caller_done = True
    blockers = state.may_end_call()
    check("end_call still blocked", blockers != [], "; ".join(blockers)[:70])

    blocked = await agent.end_call(
        _fake_ctx(state), goodbye="Thanks for calling Bush and Bush. Take care."
    )
    check(
        "the tool refuses and tells the model to keep going",
        bool(blocked) and "Do not end the call" in blocked,
    )
    check("nothing was said to the caller", not state.call_ended)

    say("I worked for Northline Logistics.")
    check(
        "employer captured for the conflict check",
        state.other_party_name == "Northline Logistics",
        state.other_party_name or "(empty)",
    )
    check("end_call unlocked", state.may_end_call() == [], "; ".join(state.may_end_call())[:70])

    ctx = _fake_ctx(state)
    done = await agent.end_call(ctx, goodbye="Thanks for calling Bush and Bush. Take care.")
    check("clean goodbye accepted", done is None)
    check("call marked ended", state.call_ended and state.disconnect_reason == "agent_hangup")
    check("farewell was spoken", ctx.session.say.called)

    print("\nWhat the firm receives")
    collected = state.to_dict()["collected"]
    for key in ("user_fname", "user_lname", "user_phone", "other_party_name"):
        check(f"{key} present", bool(collected.get(key)), str(collected.get(key)))

    print("\nLive mic check (optional):")
    print("  python -m src.worker console")
    print('  Then say: "I was fired after I reported a safety issue."\n')

    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("all employment checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
