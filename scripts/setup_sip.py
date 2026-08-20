"""Complete the LiveKit side of the phone-number setup. One command.

    python scripts\\setup_sip.py

What it does, in your LiveKit project:

  1. creates an **inbound SIP trunk** that accepts calls for the number,
     generating a strong SIP username/password for you if you did not supply
     one;
  2. creates a **dispatch rule** so every inbound call lands in its own room
     with the intake agent dispatched into it;
  3. writes the resulting ids and SIP credentials to `sip_config.json` and
     appends them to `.env`;
  4. prints the exact Twilio settings you will need *later* - it does not
     touch Twilio, and it does not move your live number.

Nothing here affects the number while it is still pointed at Retell. The
switch only happens when you change the Origination URI in Twilio, which this
script deliberately leaves to you.

Useful flags:

    --list              show what already exists, change nothing
    --number +1...      inbound number (default: the firm's intake line)
    --username / --password   supply your own SIP credentials
    --replace           delete an existing trunk/rule for this number first
    --dry-run           print the plan without creating anything
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.protobuf.duration_pb2 import Duration  # noqa: E402
from livekit import api as lkapi  # noqa: E402

from src import settings  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "sip_config.json"
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_NUMBER = "+16825641506"


# ---------------------------------------------------------------------------
def sip_host(livekit_url: str) -> str:
    """wss://foo-bar.livekit.cloud -> foo-bar.sip.livekit.cloud"""
    host = re.sub(r"^\w+://", "", livekit_url.strip()).rstrip("/")
    if host.endswith(".livekit.cloud"):
        project = host[: -len(".livekit.cloud")]
        return f"{project}.sip.livekit.cloud"
    return f"<your-livekit-project>.sip.livekit.cloud   (could not derive from {livekit_url!r})"


def env_upsert(values: dict[str, str]) -> None:
    """Add or update KEY=value lines in .env without disturbing the rest."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        line = f"{key}={value}"
        for i, existing in enumerate(lines):
            if existing.strip().startswith(f"{key}="):
                lines[i] = line
                break
        else:
            lines.append(line)
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


async def show(client: lkapi.LiveKitAPI) -> tuple[list, list]:
    trunks = await client.sip.list_sip_inbound_trunk(lkapi.ListSIPInboundTrunkRequest())
    rules = await client.sip.list_sip_dispatch_rule(lkapi.ListSIPDispatchRuleRequest())
    print("\nInbound trunks in this LiveKit project:")
    if not trunks.items:
        print("  (none)")
    for t in trunks.items:
        print(f"  {t.sip_trunk_id}  {t.name!r}  numbers={list(t.numbers)}")
    print("Dispatch rules:")
    if not rules.items:
        print("  (none)")
    for r in rules.items:
        agents = [a.agent_name for a in r.room_config.agents] if r.room_config else []
        print(
            f"  {r.sip_dispatch_rule_id}  {r.name!r}  trunks={list(r.trunk_ids)}  agents={agents}"
        )
    return list(trunks.items), list(rules.items)


async def remove_for_number(client: lkapi.LiveKitAPI, number: str) -> None:
    trunks, rules = await show(client)
    doomed = {t.sip_trunk_id for t in trunks if number in list(t.numbers)}
    for r in rules:
        if set(r.trunk_ids) & doomed:
            await client.sip.delete_sip_dispatch_rule(
                lkapi.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=r.sip_dispatch_rule_id)
            )
            print(f"  deleted dispatch rule {r.sip_dispatch_rule_id}")
    for trunk_id in doomed:
        await client.sip.delete_sip_trunk(lkapi.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id))
        print(f"  deleted trunk {trunk_id}")


def twilio_instructions(number: str, username: str, password: str, host: str) -> str:
    return f"""
==============================================================================
  LiveKit side is done. Twilio side - when you are ready to cut over.
==============================================================================

  Your number {number} is still answering wherever it answers today.
  It moves to this agent only when you change the setting below.

  Twilio Console -> Elastic SIP Trunking -> your trunk

  1) Termination      (leave as-is; that is the outbound side)

  2) Origination      Add an Origination URI:

         sip:{host};transport=tcp

     Priority 10, Weight 10, Enabled.

  3) Authentication   Credential Lists -> create one (or edit yours) with:

         username: {username}
         password: {password}

     Attach that credential list to the trunk.

  4) Numbers          Make sure {number} is assigned to this trunk.

  Then place a test call. The worker must be running:

         python -m src.worker start

==============================================================================
"""


# ---------------------------------------------------------------------------
async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number", default=DEFAULT_NUMBER)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--room-prefix", default="intake")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    missing = [p for p in settings.validate() if p.startswith("LIVEKIT")]
    if missing:
        print("Cannot continue - fill these in .env first:")
        for m in missing:
            print(f"  - {m}")
        return 1

    host = sip_host(settings.livekit.url)
    username = args.username or f"bushbush{secrets.token_hex(3)}"
    password = args.password or secrets.token_urlsafe(24)

    if args.dry_run:
        print("Dry run - nothing will be created.\n")
        print(f"  number       {args.number}")
        print(f"  agent        {settings.livekit.agent_name}")
        print(f"  room prefix  {args.room_prefix}")
        print(f"  sip host     {host}")
        print(f"  ring timeout {settings.call.ring_duration_ms // 1000}s")
        print(f"  max duration {settings.call.max_call_duration_ms // 1000}s")
        return 0

    async with lkapi.LiveKitAPI(
        url=settings.livekit.url,
        api_key=settings.livekit.api_key,
        api_secret=settings.livekit.api_secret,
    ) as client:
        if args.list:
            await show(client)
            return 0

        if args.replace:
            print("Removing existing config for this number...")
            await remove_for_number(client, args.number)

        existing_trunks, _ = await show(client)
        clash = [t for t in existing_trunks if args.number in list(t.numbers)]
        if clash:
            print(
                f"\n{args.number} is already on trunk {clash[0].sip_trunk_id}. "
                "Re-run with --replace to recreate it."
            )
            return 1

        print("\nCreating inbound trunk...")
        trunk = await client.sip.create_sip_inbound_trunk(
            lkapi.CreateSIPInboundTrunkRequest(
                trunk=lkapi.SIPInboundTrunkInfo(
                    name="Bush & Bush intake line",
                    numbers=[args.number],
                    auth_username=username,
                    auth_password=password,
                    krisp_enabled=True,
                    # Retell ring_duration_ms / max_call_duration_ms
                    ringing_timeout=Duration(
                        seconds=settings.call.ring_duration_ms // 1000
                    ),
                    max_call_duration=Duration(
                        seconds=settings.call.max_call_duration_ms // 1000
                    ),
                )
            )
        )
        print(f"  trunk {trunk.sip_trunk_id}")

        print("Creating dispatch rule...")
        rule = await client.sip.create_sip_dispatch_rule(
            lkapi.CreateSIPDispatchRuleRequest(
                name="Bush & Bush intake",
                trunk_ids=[trunk.sip_trunk_id],
                rule=lkapi.SIPDispatchRule(
                    dispatch_rule_individual=lkapi.SIPDispatchRuleIndividual(
                        room_prefix=args.room_prefix
                    )
                ),
                room_config=lkapi.RoomConfiguration(
                    agents=[
                        lkapi.RoomAgentDispatch(agent_name=settings.livekit.agent_name)
                    ]
                ),
            )
        )
        print(f"  rule  {rule.sip_dispatch_rule_id}")

    config = {
        "number": args.number,
        "sip_trunk_id": trunk.sip_trunk_id,
        "sip_dispatch_rule_id": rule.sip_dispatch_rule_id,
        "sip_username": username,
        "sip_password": password,
        "sip_host": host,
        "agent_name": settings.livekit.agent_name,
        "room_prefix": args.room_prefix,
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    env_upsert(
        {
            "INTAKE_NUMBER": args.number,
            "SIP_TRUNK_ID": trunk.sip_trunk_id,
            "SIP_DISPATCH_RULE_ID": rule.sip_dispatch_rule_id,
            "SIP_USERNAME": username,
            "SIP_PASSWORD": password,
        }
    )
    print(f"\nSaved to {CONFIG_PATH.name} and appended to .env")
    print(twilio_instructions(args.number, username, password, host))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
