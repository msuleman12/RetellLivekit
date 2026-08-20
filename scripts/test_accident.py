"""Offline walkthrough of a car-accident call, turn by turn.

    python scripts/test_accident.py

No LiveKit room, no mic, no API spend. It drives the same code the live agent
runs — `capture.auto_capture_from_utterance`, `CallState`, and the `end_call`
tool — through a realistic conversation, and asserts the three things that went
wrong on the recorded call this port was fixed against:

  1. "Yes. Moss Ali." was never captured, so Claire asked for the name again.
  2. "plus 1 2 9 0 9 0 9 4 9 0" was accepted as the number 1290909490.
  3. The call could hang up the moment the last must-have landed, mid-sentence.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import prompts  # noqa: E402
from src.agents.accident import AccidentAgent  # noqa: E402
from src.capture import auto_capture_from_utterance  # noqa: E402
from src.state import CallState  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {label}{f' - {detail}' if detail else ''}")
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
    print("\nAccident agent — offline conversation walkthrough\n")

    agent = AccidentAgent(greet=False)
    tool_names = sorted(t.info.name for t in agent.tools)

    print("Setup")
    check("case_type is accident", agent.case_type == "accident")
    check(
        "exactly one tool, as on Retell",
        tool_names == ["end_call"],
        ", ".join(tool_names) or "(none)",
    )
    check(
        "end_call description is Retell's, verbatim",
        agent.tools[0].info.description == prompts.END_CALL_TOOL_DESCRIPTION,
    )
    check(
        "prompt looks like Retell accident intake",
        "Bush and Bush" in agent.instructions
        and "other driver" in agent.instructions.lower(),
    )
    check(
        "prompt forbids a fixed question order",
        "There is NO fixed question order" in agent.instructions,
    )
    check(
        "begin message is the Claire greeting",
        "Claire" in agent.begin_message and "Bush and Bush" in agent.begin_message,
        agent.begin_message,
    )

    print("\nThe call")
    state = CallState(case_type="accident")
    state.other_party_required = True

    def say(text: str) -> list[str]:
        notes = auto_capture_from_utterance(state, text)
        print(f"       caller: {text}")
        if notes:
            print(f"       captured: {', '.join(notes)}")
        return notes

    check("fresh call has outstanding must-haves", len(state.missing_must_haves()) == 6)

    say("Hi, I was rear-ended at a red light on Preston Road last Tuesday.")

    # (1) The bare-name answer that the old regex set could not see.
    say("Yes. Moss Ali.")
    check("bare 'Yes. Moss Ali.' captured the name", state.full_name == "Moss Ali", state.full_name)

    # (2) Country code plus nine digits is not a phone number.
    say("It's plus 1 2 9 0 9 0 9 4 9 0.")
    check("country code + 9 digits is not accepted", state.phone == "", state.phone or "(empty)")

    say("Sorry — two one four, five five five, zero one nine nine.")
    check("full number accepted", state.phone == "2145550199", state.phone)
    say("Yes, that's right.")
    check("read-back confirmed", state.phone_read_back)

    say("The other driver was Alex Rivera.")
    check("other party captured", state.other_party_name == "Alex Rivera", state.other_party_name)

    print("\n  Must-haves are in. The caller keeps talking.")
    state.callback_promised = True
    check(
        "end_call still blocked while the caller is talking",
        state.may_end_call() != [],
        "; ".join(state.may_end_call())[:70],
    )

    blocked = await agent.end_call(
        _fake_ctx(state), goodbye="Thanks for calling Bush and Bush. Take care."
    )
    check(
        "the tool refuses and tells the model to keep going",
        bool(blocked) and "Do not end the call" in blocked,
        (blocked or "")[:70],
    )
    check("nothing was said to the caller", not state.call_ended)

    print("\n  Claire offers the close, and the caller takes it.")
    state.closing_offered = True
    say("No, that's everything. Thank you.")
    check("caller_done recorded", state.caller_done)
    check("end_call unlocked", state.may_end_call() == [])

    questioned = await agent.end_call(
        _fake_ctx(state), goodbye="Take care — anything else I can help with?"
    )
    check(
        "a goodbye containing a question is rejected",
        bool(questioned) and "forbidden" in questioned.lower(),
        (questioned or "")[:70],
    )
    check("still not hung up", not state.call_ended)

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
    print('  Then say: "I was in a car accident yesterday on I-35."\n')

    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("all accident checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
