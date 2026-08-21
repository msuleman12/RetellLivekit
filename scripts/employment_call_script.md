# Employment agent — manual mic test script

Run: `python -m src.worker console`
After the call: `Select-String "turn timing|routing call to" run.log`

Speak in normal American-ish English, one answer per turn, and **wait for
Claire to finish** before replying.

## Run 1 — the happy path

| # | Claire asks (roughly) | Say this |
|---|---|---|
| 1 | "How can I help you today?" | **"I was fired from my job last week after I reported a safety problem to HR."** |
| 2 | acknowledges, asks your name | **"Maria Gomez."** |
| 3 | asks for a callback number | **"Two one four, five five five, zero one nine nine."** |
| 4 | reads the number back | **"Yes, that's correct."** |
| 5 | asks who you worked for | **"I worked for Northline Logistics."** |
| 6 | asks your role | **"I was a warehouse supervisor there for about three years."** |
| 7 | asks if you still work there | **"No, they let me go on the fifth of August."** |
| 8 | asks who was involved / did you report it | **"I told my supervisor, Dan Ruiz, and then HR in writing."** |
| 9 | asks if you have anything in writing | **"Yes, I have the emails and my pay stubs."** |
| 10 | asks how this has affected you | **"I've lost about two months of income and I'm behind on rent."** |
| 11 | asks what you're hoping for | **"I want fair compensation for being pushed out."** |
| 12 | asks the best time to reach you | **"Weekday afternoons after two."** |
| 13 | "an attorney will review… anything else? any questions?" | **"No, that's everything. Thank you."** |

Claire should then say a short goodbye **with no question in it** and hang up.

### What to check in the log
- `routing call to employment` on turn 1 — **not** premises, **not** accident
- `auto-captured name: Maria Gomez`
- phone recorded as `2145550199` — ten digits, no leading 1
- employer recorded as the conflict-check party
- `end_call accepted (employment)` at the end

## Run 2 — the things that break

Restart the worker and try these instead. Each one is a rule the agent is
supposed to enforce.

| Test | Say this | Correct behaviour |
|---|---|---|
| Short number | **"It's five five five, zero one nine nine."** | Says it seems incomplete, asks again. Must NOT accept 8 digits. |
| Country code | **"It's plus one, two one four, five five five, zero one nine nine."** | Records `2145550199`, drops the leading 1. |
| Unknown employer | **"I honestly don't remember the company's legal name."** | Accepts "I don't know" and moves on — does not loop. |
| Early hang-up | Before giving your number: **"I have to go now, thanks."** | Does NOT hang up. Should still ask for a callback number. |
| Work injury | **"I got hurt at work when I slipped on some oil."** | employment — NOT premises. This is the misroute Retell's rules warn about. |
| Interrupt | Start talking while Claire is mid-sentence | She should stop and listen, not talk over you. |
| Silence | Say nothing for ~15 seconds | One gentle "are you still there?", at most twice — never a hang-up. |

## Run 3 — routing only (fast)

Say only the first line, confirm the log shows the right branch, then Ctrl+C.

| Say this | Expected |
|---|---|
| "I was fired last week." | employment |
| "My boss hasn't paid my overtime." | employment |
| "I had an accident at work." | employment |
| "I slipped and fell in a grocery store." | premises |
| "I was rear-ended at a red light." | accident |
| "My surgeon botched my knee operation." | malpractice |
| "My manager has been harassing me sexually." | harassment |
| "I need help with a divorce." | asks ONE clarifying question first, then politely declines. Must not decline on the first utterance. |
