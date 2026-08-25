# Bush & Bush Law Group — LiveKit intake agent

A code rebuild of the Retell setup: the same Claire, the same routing, the same
prompts, the same call behaviour — running on LiveKit Agents with ElevenLabs
voice, driven entirely by config and an HTTP API instead of a dashboard.

Prompts are copied **verbatim** from the Retell agents. Every numeric setting
Retell exposed is either carried over directly or converted with an explicit
formula in `src/settings.py`, so nothing was silently dropped.

---

## What it does

```
  +1 682 564 1506 (Twilio)
            │  SIP
            ▼
  LiveKit inbound trunk ── dispatch rule ──▶ room ──▶ this worker
                                                        │
                                                   RouterAgent  (gpt-4.1-nano)
                                                   "Claire", one question,
                                                   works out the case type
                                                        │
       ┌────────────┬──────────────┬────────────┬───────┴──────┐
       ▼            ▼              ▼            ▼              ▼
   Accident    Employment      Premises    Malpractice    Harassment      (gpt-4.1-mini)
       └────────────┴──────────────┴────────────┴──────────────┘
                                  │
                       end of call ▼
                 post-call analysis (gpt-5-mini)
                 → JSON on disk + POST to your webhook
```

The router carries the conversation across the handoff, so the specialist picks
up mid-conversation and never re-greets — exactly like Retell's `agent_swap`.

---

## Layout

```
src/
  settings.py     all config + the Retell→LiveKit conversion formulas
  prompts.py      the Retell prompts, verbatim, with their source ids
  schemas.py      post_call_analysis_data for all five agents
  models.py       Deepgram STT / OpenAI LLM / ElevenLabs TTS / VAD / turn-taking
  state.py        per-call state, the must-have rules, the end_call gate
  extract.py      live model-driven field capture (Retell's analysis, during the call)
  capture.py      instant regex fast-path for phone digits and read-backs
  routing.py      case-type classification: keyword pass, then the model
  lifecycle.py    reminders, max duration, silence hangup
  postcall.py     post-call extraction + webhook delivery
  pronunciation.py  the Retell pronunciation dictionary
  worker.py       the LiveKit worker (entry point for calls)
  api.py          the control API
  agents/
    base.py        the end_call tool and its gate, plus background capture
    router.py      the conversation-flow equivalent
    accident.py employment.py premises.py malpractice.py harassment.py
scripts/
  setup_sip.py    one-time trunk + dispatch rule creation
  list_voices.py  list ElevenLabs voices and write ELEVEN_VOICE_ID
```

---

## Setup

Python 3.10+.

```powershell
cd D:\LivekitAgent
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
notepad .env
```

### Keys you need

| Variable | Where from |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | LiveKit Cloud project settings |
| `DEEPGRAM_API_KEY` | Deepgram console |
| `OPENAI_API_KEY` | OpenAI platform |
| `ELEVENLABS_API_KEY`, `ELEVEN_VOICE_ID` | ElevenLabs — **the voice id is the one thing I could not carry over**, see below |
| `POST_CALL_WEBHOOK_URL` | your Zapier catch hook (or anything else) |

**About the voice.** Retell referenced the voice as `11labs-Nico` / `retell-Nico`
— that is Retell's internal alias, not an ElevenLabs voice id. Open your
ElevenLabs voice library, pick Nico (or whichever voice you licensed), copy its
voice id, and put it in `ELEVEN_VOICE_ID`. Everything else about the voice —
model, speed, stability — is already set to the Retell equivalents.

---

## Running

Two processes.

```powershell
# the voice worker - answers calls
python -m src.worker dev        # development, hot reload
python -m src.worker start      # production

# the control API
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

Talk to it without a phone: `POST /sessions` returns a room and a join token you
can paste into the LiveKit Agents Playground.

---

## Wiring the phone number

Retell held `+1 682 564 1506` as a custom number on your Twilio SIP trunk
(`hanzalatesting.pstn.twilio.com`). LiveKit needs the mirror of that. There are
two halves, and they are independent.

### Half 1 — LiveKit side (safe; does not move the number)

```powershell
setup_number.bat
```

or `python scripts\setup_sip.py`. It creates the inbound trunk and the dispatch
rule, generates SIP credentials for you, saves them to `sip_config.json` and
`.env`, and prints the Twilio settings for later. Your live number keeps
answering wherever it answers today — this only prepares the destination.

Flags: `--list` (show what exists), `--dry-run` (plan only), `--replace`
(recreate), `--number +1...`, `--username` / `--password` to supply your own.

### Half 2 — Twilio side (this is the actual cutover)

Only when you are ready. In Twilio → Elastic SIP Trunking → your trunk:

- **Origination**: add `sip:<your-project>.sip.livekit.cloud;transport=tcp`
- **Authentication**: a credential list with the username/password from
  `sip_config.json`
- **Numbers**: `+1 682 564 1506` assigned to that trunk

The exact values are printed at the end of Half 1. The moment the Origination
URI changes the number leaves Retell — one number can only ring one place.

**Suggestion:** buy a second Twilio number for a few dollars and point that at
LiveKit first. Test Claire end to end on it, then move the real line.

---

## Control API

Every route needs `x-api-key: <API_KEY from .env>`.

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/config` | the effective settings, with the Retell values traceable |
| GET | `/agents` | the router and the five specialists |
| GET | `/agents/{case_type}/prompt` | the exact prompt an agent is running |
| POST | `/sessions` | new room + agent dispatch + a join token |
| POST | `/calls/outbound` | dial a number into the intake flow |
| GET | `/calls` | saved post-call records, newest first |
| GET | `/calls/{call_id}` | one full record incl. transcript and analysis |

```bash
curl -H "x-api-key: change-me" http://localhost:8000/config
curl -X POST -H "x-api-key: change-me" -H "content-type: application/json" \
     -d '{}' http://localhost:8000/sessions
```

Interactive docs are at `http://localhost:8000/docs`.

---

## Retell → LiveKit parity

| Retell | Here |
|---|---|
| conversation flow `..._87ebb53291b2` | `src/agents/router.py` |
| `extract_dynamic_variables` (`case_type`) | `routing.classify_case_type_llm` — same model, enum description verbatim from `prompts.ROUTER_CASE_TYPE_RULES`, with a keyword fast-path in front of it |
| `agent_swap` node | `session.update_agent` — carries `chat_ctx`, no re-greeting |
| `clarify-before-decline` node | `router.py` asks again; decline needs the model to return `other` *after* a clarifying question |
| `Polite Decline` end node | `_DECLINE_FAREWELL` in `router.py` |
| `retell-llm` gpt-4.1-mini @ 0.55 | `openai.LLM(model="gpt-4.1-mini", temperature=0.55)` |
| flow model gpt-4.1-nano | agent-level llm override on the router |
| `11labs-Nico` / `eleven_flash_v2_5` | `elevenlabs.TTS(model="eleven_flash_v2_5")` |
| `voice_speed` 1.12 | `VoiceSettings.speed` |
| `voice_temperature` 1.15 | `VoiceSettings.stability` 0.425 (inverted scale — see `settings.stability_from_voice_temperature`) |
| `interruption_sensitivity` 0.85 | `InterruptionOptions.min_duration` 0.247 s |
| `responsiveness` 0.95 | `EndpointingOptions` 0.193 s / 3.693 s, fixed mode |
| `custom_stt_config` deepgram 450 ms | `deepgram.STT(endpointing_ms=450)` |
| `boosted_keywords` | Deepgram `keyterm` |
| `pronunciation_dictionary` (IPA) | ElevenLabs dictionary if configured, else phonetic respelling in `src/pronunciation.py` |
| `denoising_mode` | LiveKit `BVCTelephony` noise cancellation |
| `max_call_duration_ms` 664000 | `lifecycle.py` watchdog |
| `end_call_after_silence_ms` 261000 | `lifecycle.py` watchdog |
| `reminder_trigger_ms` / `reminder_max_count` | `lifecycle.py` silence nudges |
| `ring_duration_ms` 17000 | trunk `ringing_timeout` |
| `handbook_config` toggles | `prompts.HANDBOOK_BLOCK`, appended to every agent |
| `expressive_mode_prompt` | `prompts.EXPRESSIVE_BLOCK` |
| `post_call_analysis_data` | `src/schemas.py` (26/26/23/24/22 fields) |
| `post_call_analysis_model` gpt-5-mini | `POST_CALL_ANALYSIS_MODEL` |
| webhook `call_analyzed` | `postcall.py` — same event name and payload shape |
| `end_call` tool | the agent's only tool, description verbatim, now genuinely enforced (below) |
| the second model that filled `post_call_analysis_data` | `src/extract.py`, run during the call instead of after it |

### Deliberate differences

1. **`end_call` is enforced, not just requested.** Retell's prompt said
   "FORBIDDEN — never end_call while you are still asking for anything", and the
   model mostly complied. Here the tool checks `CallState.may_end_call()` and
   refuses, telling the model exactly what is still outstanding — the caller
   hears an ordinary question instead of a hang-up. Same contract, no longer
   optional. Nothing else in the codebase can end a call.

2. **The caller decides when the call is over.** `may_end_call()` blocks the
   tool until the caller has actually said they are finished, and `extract.py`
   revokes that the moment they start talking again. A complete intake is not a
   reason to hang up; it only means Claire can stop asking.

3. **Fields are captured by a model, not by patterns.** Retell's speaking model
   never carried a checklist — a second model read the transcript afterwards.
   `src/extract.py` runs that second model during the call, in the background,
   so it costs no reply latency, using the same field names as `schemas.py`.
   `capture.py` stays in front of it as an instant fast-path for the things that
   are genuinely deterministic: phone digits, read-back confirmations, "I don't
   know", and a caller signing off. If the extraction model is unreachable it
   disables itself after three failures and the fast-path carries the call.

4. **Sexual harassment relaxes the conflict check.** Retell marked
   `other_party_name` required on four agents but optional on that one, with
   "do not push hard if the caller is distressed". That agent's `end_call` does
   not block on it.

5. **`volume` (1.7) has no equivalent.** It was a Retell playback gain. Set the
   level on the Twilio trunk if the line is quiet; ElevenLabs has no volume
   parameter and boosting it in code would clip.

The outbound booking agent was skipped on your instruction. Its Retell tools
were placeholder URLs pointing at `example.com` anyway.

---

## Post-call data

Every call writes `call_records/<timestamp>_<call_id>.json` and POSTs the same
body to `POST_CALL_WEBHOOK_URL`:

```json
{
  "event": "call_analyzed",
  "call": {
    "call_id": "...", "case_type": "employment",
    "agent_name": "Bush & Bush Law Group - Employment",
    "from_number": "+1...", "duration_ms": 214000,
    "transcript": "Agent: ...\nUser: ...",
    "transcript_object": [ ... ],
    "call_analysis": {
      "custom_analysis_data": { "user_fname": "...", "...": "..." },
      "call_summary": "...", "user_sentiment": "Neutral",
      "call_successful": true, "in_voicemail": false
    }
  }
}
```

Precedence, highest first: the four must-haves captured live (heard in context,
and for the phone number validated against the NANP), then the post-call model's
reading of the whole transcript, then anything `extract.py` picked up live that
the post-call pass left null.

A note on phone numbers: Retell's field says "must be a valid 10-digit US
number", but a transcript-only reading will happily return something like
`1290909490` when the caller says "plus one" and then nine digits. Both the live
and post-call paths now check the NANP rules — area code and exchange must start
2-9 — and drop the value rather than send the firm a number that cannot be
dialed.

---

## Notes

- **Never pass `parallel_tool_calls` or `tool_choice` to an agent that has no
  tools.** OpenAI rejects both with a 400 (`'parallel_tool_calls' is only
  allowed when 'tools' are specified`), and LiveKit forwards them without
  checking — see `livekit/agents/inference/llm.py`, which the OpenAI plugin's
  `LLMStream` subclasses. The router is the tool-less agent here. When this was
  wired up, every router reply 400'd and the agent went completely silent: the
  greeting still played, because `session.say()` is TTS only and never reaches
  the LLM, so the call sounded connected but never answered. Do not reintroduce
  either parameter on `build_router_llm()` or any `tools=[]` agent.

- `USE_TURN_DETECTOR=true` uses LiveKit's semantic turn detector. It needs
  LiveKit Cloud reachability; if it can't start, the agent falls back to VAD and
  logs a warning.
- `livekit-plugins-noise-cancellation` ships as a platform wheel. If pip can't
  install it on your machine, everything still runs — you just lose background
  noise cancellation. Set `NOISE_CANCELLATION=off` to silence the warning.
- Changing a prompt means editing `src/prompts.py` and restarting the worker.
  There is no draft/publish split, so nothing can sit unpublished the way the
  Retell router and intake agent currently are.
