"""Offline checks - no API keys, no network, no call needed.

    python scripts/selftest.py

Verifies that the Retell -> LiveKit mapping produces the numbers it should,
that every agent builds with its tools, that must-have tracking still works,
and that the post-call schemas are well-formed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("OPENAI_API_KEY", "offline-selftest")
os.environ.setdefault("DEEPGRAM_API_KEY", "offline-selftest")
os.environ.setdefault("ELEVENLABS_API_KEY", "offline-selftest")
os.environ.setdefault("ELEVEN_VOICE_ID", "offline-selftest")

from src import prompts, settings  # noqa: E402
from src.agents import AGENTS_BY_CASE_TYPE, RouterAgent  # noqa: E402
from src.schemas import FIELDS_BY_CASE_TYPE, json_schema_for  # noqa: E402
from src.state import CallState, normalize_phone  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(' - ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def main() -> int:
    print("\nRetell -> LiveKit parameter mapping")
    check(
        "interruption_sensitivity 0.85 -> min_duration 0.247s",
        settings.interruption_min_duration(0.85) == 0.247,
        str(settings.interruption_min_duration(0.85)),
    )
    check(
        "responsiveness 0.95 -> endpointing 0.193s / 3.693s",
        settings.endpointing_delays(0.95) == (0.193, 3.693),
        f"{settings.endpointing_delays(0.95)[0]} / {settings.endpointing_delays(0.95)[1]}",
    )
    check(
        "voice_temperature 1.15 -> stability 0.425",
        settings.tts.stability == 0.425,
        str(settings.tts.stability),
    )
    check("voice_speed 1.12", settings.tts.speed == 1.12)
    check("max_call_duration_ms 664000", settings.call.max_call_duration_ms == 664_000)
    check(
        "end_call_after_silence_ms 261000",
        settings.call.end_call_after_silence_ms == 261_000,
    )
    check("reminder 10000ms x2", (settings.call.reminder_trigger_ms, settings.call.reminder_max_count) == (10_000, 2))

    print("\nLatency profiles")
    check(
        "parity STT endpointing default 450",
        settings.resolve_stt_endpointing_ms("parity", None) == 450,
    )
    check(
        "fast STT endpointing default 200",
        settings.resolve_stt_endpointing_ms("fast", None) == 200,
    )
    check(
        "explicit STT_ENDPOINTING_MS overrides profile",
        settings.resolve_stt_endpointing_ms("fast", "300") == 300,
    )
    check(
        "parity responsiveness default 0.95",
        settings.resolve_responsiveness("parity", None) == 0.95,
    )
    check(
        "fast responsiveness default 1.0",
        settings.resolve_responsiveness("fast", None) == 1.0,
    )
    parity_ep = settings.resolve_endpointing("parity", 0.95)
    check(
        "parity endpointing uncapped max",
        parity_ep == (0.193, 3.693),
        f"{parity_ep[0]} / {parity_ep[1]}",
    )
    fast_ep = settings.resolve_endpointing("fast", 1.0)
    # 0.8 floor / 2.5 ceiling: a caller pausing mid-sentence used to have the
    # turn committed under them, splitting one answer into four turns.
    check(
        "fast endpointing floors min_delay at 0.8s, caps max at 2.5s",
        fast_ep == (0.8, 2.5),
        f"{fast_ep[0]} / {fast_ep[1]}",
    )
    # Default flipped to `fast`: `parity` leaves turn detection on LiveKit's
    # cloud TurnDetector, which adds a 1-2s round-trip to every caller turn.
    check(
        "resolve_latency_profile falls back to fast",
        settings.resolve_latency_profile("nope") == "fast",
    )
    check(
        f"loaded profile={settings.call.latency_profile}",
        settings.call.latency_profile in ("parity", "fast"),
    )
    live_min, live_max = settings.call.endpointing
    check(
        "loaded endpointing matches resolve helpers",
        (live_min, live_max)
        == settings.resolve_endpointing(
            settings.call.latency_profile, settings.call.responsiveness
        ),
        f"{live_min} / {live_max}",
    )
    check(
        "loaded STT endpointing matches resolve helper",
        settings.stt.endpointing_ms
        == settings.resolve_stt_endpointing_ms(
            settings.call.latency_profile, os.getenv("STT_ENDPOINTING_MS")
        ),
        str(settings.stt.endpointing_ms),
    )

    print("\nPhone normalisation")
    # 214-555-0199 rather than 555-123-4567: NANP forbids an exchange starting
    # with 0 or 1, so "555 123 4567" is not a dialable US number and is now
    # rejected on purpose. See `is_valid_us_number`.
    check("'214 555 0199' -> 2145550199", normalize_phone("214 555 0199") == "2145550199")
    check("'1 214 555 0199' -> 2145550199", normalize_phone("1 (214) 555-0199") == "2145550199")
    check("8 digits rejected", normalize_phone("5551234") is None)
    check(
        "country code + 9 digits rejected",
        normalize_phone("plus 1 2 9 0 9 0 9 4 9 0") is None,
    )
    check("area code starting with 1 rejected", normalize_phone("1290909490") is None)

    print("\nAgents")
    router = RouterAgent()
    router_tools = sorted(t.info.name for t in router.tools)
    check(
        "router exposes no tools (speech-only)",
        router_tools == [],
        ", ".join(router_tools) or "(none)",
    )
    for case_type, cls in AGENTS_BY_CASE_TYPE.items():
        agent = cls()
        names = sorted(t.info.name for t in agent.tools)
        # Retell gave every intake agent exactly one tool, `end_call`, and let
        # the model choose the moment. Anything more or less is a divergence.
        check(
            f"{case_type}: end_call only, prompt {len(agent.instructions)} chars",
            names == ["end_call"],
            ", ".join(names) or "(none)",
        )
        check(
            f"{case_type}: end_call carries Retell's description verbatim",
            agent.tools[0].info.description == prompts.END_CALL_TOOL_DESCRIPTION,
        )
        check(
            f"{case_type}: prompt carries the Retell text",
            "Bush and Bush" in agent.instructions
            and prompts.DELIVERY_BLOCK in agent.instructions
            and prompts.OPERATING_BLOCK in agent.instructions,
        )

    print("\nmust-have tracking (speech capture + programmatic close)")
    state = CallState()
    check("fresh call lists outstanding must-haves", len(state.missing_must_haves()) == 6)
    state.record_name("Ahmed", "Khan")
    check("missing phone flagged", "a callback number they said out loud, with all 10 digits" in " | ".join(state.missing_must_haves()))
    check("8-digit number rejected", state.record_phone("5551234") is False)
    check("10-digit number accepted", state.record_phone("(214) 555-0199") is True)
    state.phone_read_back = True
    state.other_party_name = "I don't know"
    state.incident_summary = "Rear-ended at a red light on Preston Road last Tuesday."
    check("still incomplete before the callback promise", state.missing_must_haves() != [])
    state.callback_promised = True
    check("clear once everything is collected", state.missing_must_haves() == [])

    print("\nthe close is the caller's to make")
    ready = CallState()
    ready.record_name("Ahmed", "Khan")
    ready.record_phone("2145550199")
    ready.phone_read_back = True
    ready.other_party_name = "State Farm"
    ready.incident_summary = "Rear-ended at a red light."
    ready.callback_promised = True
    check(
        "must-haves complete but end_call still blocked",
        ready.may_end_call() != [] and "finished" in ready.may_end_call()[0],
    )
    ready.caller_done = True
    check("end_call unlocked once the caller says they're done", ready.may_end_call() == [])
    check(
        "guidance never tells the agent to hang up on its own",
        "Do NOT hang up" in CallState(
            first_name="A", last_name="B", phone="2145550199",
            phone_read_back=True, other_party_name="X",
            incident_summary="y", callback_promised=True,
        ).next_missing_prompt(),
    )

    print("\nharassment relaxes the conflict-check requirement")
    sh = CallState(other_party_required=False)
    sh.record_name("Sarah", "Nguyen")
    sh.record_phone("2145550199")
    sh.phone_read_back = True
    sh.incident_summary = "Ongoing harassment by a supervisor since March."
    sh.callback_promised = True
    check("no other-party block", sh.missing_must_haves() == [])

    print("\nPost-call analysis schemas")
    for case_type, fields in FIELDS_BY_CASE_TYPE.items():
        schema = json_schema_for(fields, f"t_{case_type}")
        body = schema["schema"]
        required_matches = set(body["required"]) == set(body["properties"])
        check(
            f"{case_type}: {len(fields)} fields, strict-compatible",
            schema["strict"] is True
            and body["additionalProperties"] is False
            and required_matches,
        )

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    raise SystemExit(main())
