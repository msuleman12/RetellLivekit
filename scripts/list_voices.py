"""List the ElevenLabs voices on your account, so you can pick a voice id.

    python scripts\\list_voices.py
    python scripts\\list_voices.py --set <voice_id>     writes it into .env

Reads ELEVENLABS_API_KEY from .env - no need to paste keys on the command line.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.request  # noqa: E402
import json  # noqa: E402

from src import settings  # noqa: E402

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def set_voice(voice_id: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith("ELEVEN_VOICE_ID="):
            lines[i] = f"ELEVEN_VOICE_ID={voice_id}"
            break
    else:
        lines.append(f"ELEVEN_VOICE_ID={voice_id}")
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"ELEVEN_VOICE_ID set to {voice_id} in .env")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", dest="voice_id", default="", help="write this id to .env")
    args = parser.parse_args()

    if args.voice_id:
        set_voice(args.voice_id)
        return 0

    key = settings.tts.api_key
    if not key:
        print("ELEVENLABS_API_KEY is not set in .env")
        return 1

    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"Could not reach ElevenLabs: {exc}")
        print("If this says 401, the key in .env is wrong.")
        return 1

    voices = data.get("voices", [])
    if not voices:
        print("No voices on this account.")
        return 1

    print(f"\n{len(voices)} voice(s):\n")
    print(f"  {'NAME':<28} {'VOICE ID':<24} CATEGORY")
    print(f"  {'-' * 28} {'-' * 24} --------")
    for v in voices:
        name = (v.get("name") or "")[:27]
        print(f"  {name:<28} {v.get('voice_id',''):<24} {v.get('category','')}")

    current = settings.tts.voice_id
    print(f"\nCurrent ELEVEN_VOICE_ID: {current or '(not set)'}")
    print("Pick one and run:  python scripts\\list_voices.py --set <voice_id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
