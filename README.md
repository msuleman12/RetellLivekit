# Bush & Bush Law Group - Phone Intake Agent

Inbound phone intake agent on LiveKit Agents 1.8. A caller dials the firm's
number, Twilio sends the call to a LiveKit SIP trunk, the dispatch rule starts
this worker, and Claire answers.

**Pipeline:** Deepgram Flux STT (with its own end-of-turn detection) → OpenAI
→ ElevenLabs custom voice, with Krisp BVCTelephony noise cancellation.

## How a call flows

1. `RouterAgent` greets the caller and works out the matter. It calls
   `route_call` to hand off, or `end_call` to politely decline once a
   clarifying question shows the matter is out of scope.
2. A practice-area agent (accident, employment, premises liability, medical
   malpractice, sexual harassment) takes over with the conversation so far and
   runs intake. Employment can switch to sexual harassment mid-call.
3. The agent calls `end_call` once intake is complete and the caller signs off.
   Closing the session deletes the room, which hangs up the phone line.
4. After hangup, `services/post_call.py` extracts intake fields from the
   transcript while the call recording finishes uploading. It then scores
   priority (HIGH / MEDIUM / LOW), assesses the 3 I's, and writes
   `call_records/<start>_<call_id>.json`. Each of these then runs if
   configured, independently of the others:
   - Postgres save (`DATABASE_URL`) in the old build's layout: `user_data`,
     one table per practice area, `session_tracking`, `error_events`, plus
     the full record in `intake_calls`
   - `POST_CALL_WEBHOOK_URL` (full record) and `ZAPIER_WEBHOOK_URL`
   - SendGrid email to the intake team (`ENABLE_LEGAL_EMAILS`), with the
     priority PDF, the record as JSON and the MP3 recording attached

   Calls from `TEST_PHONE_NUMBERS` go through the same steps, tagged as tests.
   The recording is a LiveKit audio egress to S3 (`ENABLE_CALL_RECORDING`).

Silent callers get `SILENCE_REMINDERS` check-ins, then a goodbye. Calls are cut
off after `MAX_CALL_DURATION_S`.

## Layout

```
main.py                     worker entrypoint (AgentServer, session, call timers)
config.py                   environment settings, required keys checked at startup
agents/
  router/agent.py           greeting + routing
  base/agent.py             shared intake behavior (end_call, takeover turn)
  accident/ employment/ premises_liability/ medical_malpractice/ sexual_harassment/
  user_data.py              per-call state (session.userdata)
prompts/                    every prompt, one file per agent
services/
  voice_pipeline.py         Deepgram / OpenAI / ElevenLabs / VAD / turn handling
  post_call.py              post-call pipeline: analysis, record, deliveries
  analysis_fields.py        fields extracted per practice area
  priority.py               keyword priority scoring
  case_assessment.py        3 I's (liability, insurance, injuries) + red flags
  priority_pdf.py           priority assessment PDF for the email
  intake_email.py           SendGrid intake email
  recording.py              LiveKit egress call recording (S3)
  database.py               Postgres tables (old ai-receptionist layout)
  zapier_webhook.py         payload shape the firm's existing Zap expects
utils/phone.py              US phone normalization
api/server.py               control API (test sessions, call records)
livekit/                    SIP trunk / dispatch rule record
deployment/                 systemd unit + server bootstrap
```

## Run

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env              # fill in the keys

python main.py download-files     # VAD + noise cancellation models
python main.py console            # talk to it in the terminal
python main.py dev                # connect to LiveKit for phone testing
python main.py start              # production
```

Control API: `uvicorn api.server:app --host 0.0.0.0 --port 8000` (needs `API_KEY`).

## Deploy

Pushing to `dev` runs `.github/workflows/deploy.yaml`, which syncs the code to
`/var/www/bblg-livekit-agent`, installs requirements and restarts
`bblg-livekit-agent.service` (`python main.py start`). The deploy fails unless
the worker logs `registered worker`.
