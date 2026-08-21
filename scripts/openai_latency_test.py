"""Measure OpenAI time-to-first-token from this machine.

    python scripts/openai_latency_test.py

Reads OPENAI_API_KEY from .env - never type or print the key yourself.

Reading the result:
  * steady 300-900ms      -> OpenAI is fine, look elsewhere for the latency
  * swings 1s <-> 10s     -> the network path or account queueing, not the agent
  * uniformly 3s+         -> same conclusion, just consistently bad
"""

from __future__ import annotations

import os
import statistics
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

key = os.getenv("OPENAI_API_KEY", "").strip()
if not key:
    raise SystemExit("OPENAI_API_KEY missing from .env")

model = os.getenv("LLM_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
client = OpenAI(api_key=key)
runs = 10

print(f"model={model}  runs={runs}  (key loaded from .env, not shown)\n")

samples: list[float] = []
for i in range(1, runs + 1):
    start = time.perf_counter()
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            stream=True,
        )
        for _ in stream:          # first chunk = first token
            break
        ttft = (time.perf_counter() - start) * 1000
        samples.append(ttft)
        print(f"  {i:2d}. {ttft:8.0f} ms")
    except Exception as exc:
        print(f"  {i:2d}. FAILED  {type(exc).__name__}: {exc}")
    time.sleep(0.5)

if samples:
    print(
        f"\nmin {min(samples):.0f} ms | median {statistics.median(samples):.0f} ms | "
        f"max {max(samples):.0f} ms | spread {max(samples) - min(samples):.0f} ms"
    )
    if max(samples) - min(samples) > 2000:
        print("\n-> Wildly variable. The agent is not the problem; the path to "
              "OpenAI is. Deploying the worker to a cloud region near OpenAI is "
              "the fix.")
    elif statistics.median(samples) > 2000:
        print("\n-> Consistently slow. Same conclusion: this machine is too far "
              "from OpenAI to hit Retell-like latency.")
    else:
        print("\n-> OpenAI looks healthy from here. If calls are still slow, the "
              "next suspect is Deepgram/ElevenLabs or local audio handling.")
