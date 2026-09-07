# LiveKit SIP configuration

A record of how phone calls reach this agent, and the commands that change it.
Nothing here is applied automatically — LiveKit is the source of truth, these
files are a written record so the deployed topology is not knowledge that
lives only in someone's chat history.

## How a call reaches the agent

    caller dials a number
      -> Twilio Elastic SIP Trunk        (Origination URI + Credential List)
      -> LiveKit inbound trunk           (matches on the dialled number)
      -> LiveKit SIP dispatch rule       (names an agent)
      -> a worker registered under exactly that AGENT_NAME

All four must line up. The usual failure is the fourth: the trunk and rule are
fine, and the worker is running under a different `AGENT_NAME`, so calls ring
and drop with nothing in the agent log.

## Current setup

| | |
|---|---|
| LiveKit project | `wss://bblg-qupolenx.livekit.cloud` |
| SIP host (Twilio Origination) | `sip:se3a0fflguh.sip.livekit.cloud;transport=tcp` |

| Number | Trunk | Dispatch rule | Agent |
|---|---|---|---|
| `+13254425883` | `ST_hhBgddNfqAoz` Clare | `SDR_XnpUqwKWzoWm` | `bush-bush-intake` |
| `+16825641506` | `ST_qD9aNUmTJdHU` Bush & Bush intake line | `SDR_9y6h3GUqxsf9` | `bush-bush-intake` |
| `+15183333606` | `ST_bAvybk9MTjj9` My inbound trunk | `SDR_9q5gxakgCuLi` | `bblg-agent-en` |
| `+15183333620` | `ST_e4YDr4WSECY2` My Inbound Spanish | `SDR_t2innYgfbB68` | `bblg-agent-es` |

This repo's worker must register as `bush-bush-intake` (`AGENT_NAME` in `.env`).
`+13254425883` is the Clare test line for this agent. Do not move
`+16825641506` until that test number answers correctly.

The Twilio trunk for the Bush & Bush intake line requires a **Credential List**
— that LiveKit inbound trunk was created with `auth_username` / `auth_password`
by `scripts/setup_sip.py`, so LiveKit answers every INVITE with a 401 challenge.
The Clare trunk has no SIP auth. Without matching credentials on an
authenticated trunk, every call fails.

## Inspecting the live configuration

Run on the server, from the project directory:

    source .venv/bin/activate
    set -a; . ./.env; set +a
    python - <<'PY'
    import asyncio
    from livekit.api import LiveKitAPI, ListSIPInboundTrunkRequest, ListSIPDispatchRuleRequest

    async def main():
        async with LiveKitAPI() as lk:
            for t in (await lk.sip.list_sip_inbound_trunk(ListSIPInboundTrunkRequest())).items:
                print("trunk", t.sip_trunk_id, t.name, list(t.numbers))
            for r in (await lk.sip.list_sip_dispatch_rule(ListSIPDispatchRuleRequest())).items:
                print("rule ", r.sip_dispatch_rule_id, list(r.trunk_ids), r.room_config)

    asyncio.run(main())
    PY

## Adding a number to an existing trunk

When the new number is already on the same Twilio trunk, no Twilio change is
needed — LiveKit just has to be told the number belongs to the trunk.

    python - <<'PY'
    import asyncio
    from livekit.api import LiveKitAPI, ListUpdate

    async def main():
        async with LiveKitAPI() as lk:
            t = await lk.sip.update_inbound_trunk_fields(
                "ST_e4YDr4WSECY2",
                numbers=ListUpdate(add=["+1XXXXXXXXXX"]),
            )
            print("numbers now:", list(t.numbers))

    asyncio.run(main())
    PY

Use `ListUpdate(add=[...])`, not a plain list. **A plain list replaces the
whole set** and will silently drop the numbers already on the trunk.
`ListUpdate(remove=[...])` takes one off again.

Do not run `scripts/setup_sip.py` to add a number to a trunk that already
exists: it creates a *new* trunk with *new* SIP credentials, and the shared
Twilio Credential List then matches neither trunk — every number on it breaks
at once.

## A note on secrets

The SIP `auth_password` is **not** recorded here and must never be committed.
The old `ai-receptionist` repo checked a trunk password into
`livekit/outbound-trunk.json`; that credential should be treated as exposed
and rotated.
