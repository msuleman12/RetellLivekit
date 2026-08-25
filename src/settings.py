"""Configuration + the Retell -> LiveKit parameter mapping.

Every value that existed on the Retell agents is represented here, either
as a direct setting or as an explicit conversion function.  The conversion
functions are deliberately written out (rather than hard-coded numbers) so
that if you change `INTERRUPTION_SENSITIVITY` in `.env` the behaviour moves
the same way it would have on Retell.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# small env helpers
# ---------------------------------------------------------------------------
def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


# ---------------------------------------------------------------------------
# Retell -> LiveKit conversions
# ---------------------------------------------------------------------------
def interruption_min_duration(sensitivity: float) -> float:
    """Retell `interruption_sensitivity` (0..1, higher = interrupts more
    readily) -> LiveKit `InterruptionOptions.min_duration` in seconds
    (lower = interrupts more readily).

    0.85 -> 0.248 s
    """
    sensitivity = max(0.0, min(1.0, sensitivity))
    return round(0.15 + (1.0 - sensitivity) * 0.65, 3)


def endpointing_delays(responsiveness: float) -> tuple[float, float]:
    """Retell `responsiveness` (0..1, higher = replies faster) ->
    LiveKit `EndpointingOptions` min/max delay in seconds.

    0.95 -> (0.193, 3.693)
    """
    responsiveness = max(0.0, min(1.0, responsiveness))
    min_delay = round(0.15 + (1.0 - responsiveness) * 0.85, 3)
    return min_delay, round(min_delay + 3.5, 3)


def resolve_latency_profile(raw: str | None = None) -> str:
    """Return ``parity`` or ``fast``. Unknown values fall back to fast.

    Default flipped from ``parity`` to ``fast``: parity leaves turn detection on
    LiveKit's cloud TurnDetector, which adds a 1-2s transport round-trip to the
    end of every caller turn. Set ``LATENCY_PROFILE=parity`` in .env to get the
    old timing back.
    """
    value = (raw if raw is not None else _str("LATENCY_PROFILE", "fast")).strip().lower()
    return value if value in ("parity", "fast") else "fast"

def resolve_stt_endpointing_ms(profile: str, env_raw: str | None = None) -> int:
    """STT endpointing: explicit env wins; otherwise 200ms (fast) or 450ms (parity)."""
    if env_raw is None:
        env_raw = os.getenv("STT_ENDPOINTING_MS")
    if env_raw not in (None, ""):
        try:
            return int(env_raw)
        except ValueError:
            pass
    return 200 if profile == "fast" else 450


def resolve_responsiveness(profile: str, env_raw: str | None = None) -> float:
    """Responsiveness: explicit env wins; otherwise 1.0 (fast) or 0.95 (parity)."""
    if env_raw is None:
        env_raw = os.getenv("RESPONSIVENESS")
    if env_raw not in (None, ""):
        try:
            return float(env_raw)
        except ValueError:
            pass
    return 1.0 if profile == "fast" else 0.95


def resolve_endpointing(profile: str, responsiveness: float) -> tuple[float, float]:
    """Endpointing delays for the fast profile.

    min 0.1 / max 1.2 was too eager. A caller telling their story pauses to
    think, and at 1.2s the turn was committed regardless — one sentence became
    four turns, four turns became four queued LLM replies, and the queue is what
    timed out. The floor was raised to 0.4s, and 0.4s was still not enough: a
    live call chopped single answers into fragments like

        "I can"  ->  "it's happened in California and the intersection..."
        "I"      ->  "know about the other person name because he's go away..."

    with `end_of_turn` logged as 0ms and several turns committed while the
    caller was mid-sentence (`last_final_transcript_time` earlier than
    `last_speaking_time`). Every fragment costs another LLM round-trip and
    another chance to speak over the caller, so the floor is now 0.8s — still
    prompt on a finished sentence, but it rides out the pause in the middle of
    one. 2.5s remains the ceiling for a long thinking pause.
    """
    min_delay, max_delay = endpointing_delays(responsiveness)
    if profile == "fast":
        # `max`, not `min` — this is a floor that lets a chopped-up utterance
        # settle into one turn, not a ceiling that makes the agent twitchier.
        min_delay = max(min_delay, 0.8)
        max_delay = min(max_delay, 2.5)
    return min_delay, max_delay


def stability_from_voice_temperature(temperature: float) -> float:
    """Retell `voice_temperature` (0..2, higher = more variation) ->
    ElevenLabs `stability` (0..1, higher = LESS variation).

    1.15 -> 0.425
    """
    return round(max(0.0, min(1.0, 1.0 - (temperature / 2.0))), 3)


# ---------------------------------------------------------------------------
# settings objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LiveKitSettings:
    url: str = field(default_factory=lambda: _str("LIVEKIT_URL"))
    api_key: str = field(default_factory=lambda: _str("LIVEKIT_API_KEY"))
    api_secret: str = field(default_factory=lambda: _str("LIVEKIT_API_SECRET"))
    agent_name: str = field(default_factory=lambda: _str("AGENT_NAME", "bush-bush-intake"))


@dataclass(frozen=True)
class STTSettings:
    """Retell: stt_mode=custom, provider=deepgram, endpointing_ms=450.

    With ``LATENCY_PROFILE=fast`` and no explicit ``STT_ENDPOINTING_MS``, uses 200.
    """

    api_key: str = field(default_factory=lambda: _str("DEEPGRAM_API_KEY"))
    model: str = field(default_factory=lambda: _str("STT_MODEL", "nova-3"))
    language: str = field(default_factory=lambda: _str("STT_LANGUAGE", "en-US"))
    endpointing_ms: int = field(
        default_factory=lambda: resolve_stt_endpointing_ms(resolve_latency_profile())
    )


@dataclass(frozen=True)
class LLMSettings:
    """Retell: practice agents gpt-4.1-mini @0.55, router flow gpt-4.1-nano."""

    api_key: str = field(default_factory=lambda: _str("OPENAI_API_KEY"))
    model: str = field(default_factory=lambda: _str("LLM_MODEL", "gpt-4.1-mini"))
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.55))
    router_model: str = field(default_factory=lambda: _str("ROUTER_LLM_MODEL", "gpt-4.1-nano"))
    router_temperature: float = field(
        default_factory=lambda: _float("ROUTER_LLM_TEMPERATURE", 0.3)
    )
    post_call_model: str = field(
        default_factory=lambda: _str("POST_CALL_ANALYSIS_MODEL", "gpt-5-mini")
    )
    # Retell filled `post_call_analysis_data` with a second model after the call,
    # which is why its speaking model never carried a checklist. `src/extract.py`
    # runs that same idea during the call, in the background, so Claire can stay
    # conversational and still never re-ask something she was already told.
    live_extract_model: str = field(
        default_factory=lambda: _str("LIVE_EXTRACT_MODEL", "gpt-4.1-mini")
    )
    live_extract_timeout_ms: int = field(
        default_factory=lambda: _int("LIVE_EXTRACT_TIMEOUT_MS", 6_000)
    )
    #: Off by default. The live extractor is a second gpt-4.1-mini request, with
    #: the full 26-field JSON schema, fired after each caller turn — and it
    #: shares one connection pool with the reply the caller is waiting on. On a
    #: home uplink it never finished inside its 6s budget anyway, so it bought
    #: nothing and cost `llm_ttft`. Nothing is lost that matters: `capture.py`
    #: still records phone, name, read-back and the conflict check inline with
    #: no network at all, and `postcall.py` extracts every field once the call
    #: has ended, where latency is nobody's problem.
    #:
    #: Set LIVE_EXTRACT_ENABLED=true once the worker runs somewhere with a fast
    #: link to OpenAI — it is genuinely useful there, because it stops Claire
    #: re-asking something the caller already said.
    live_extract_enabled: bool = field(
        default_factory=lambda: _bool("LIVE_EXTRACT_ENABLED", False)
    )
    #: Router case-type classification. Retell's flow ran gpt-4.1-nano on an
    #: `extract_dynamic_variables` node; same model, same enum description.
    classify_timeout_ms: int = field(
        default_factory=lambda: _int("ROUTER_CLASSIFY_TIMEOUT_MS", 2_500)
    )
    #: How long the caller is made to wait for that classification before the
    #: router speaks anyway.
    #:
    #: These are two different budgets and conflating them was a mistake. On a
    #: slow uplink the classifier takes 0.7-1.5s; a single 1.2s cap meant the
    #: request was cancelled *and* the caller still sat through 1.2s of silence,
    #: which is the worst of both. Now the request gets the full
    #: `classify_timeout_ms` to finish, but the caller only waits
    #: `classify_inline_budget_ms` for it. If it lands late, the router has
    #: already asked its clarifying question and the verdict is applied at the
    #: start of the next turn instead of being thrown away.
    classify_inline_budget_ms: int = field(
        default_factory=lambda: _int("ROUTER_CLASSIFY_INLINE_BUDGET_MS", 700)
    )


@dataclass(frozen=True)
class TTSSettings:
    """Retell: voice 11labs-Nico / retell-Nico, model eleven_flash_v2_5,
    voice_speed 1.12, voice_temperature 1.15."""

    api_key: str = field(default_factory=lambda: _str("ELEVENLABS_API_KEY"))
    voice_id: str = field(default_factory=lambda: _str("ELEVEN_VOICE_ID"))
    model: str = field(default_factory=lambda: _str("ELEVEN_MODEL", "eleven_flash_v2_5"))
    # BCP-47; en-US biases ElevenLabs toward American English pronunciation.
    language: str = field(default_factory=lambda: _str("ELEVEN_LANGUAGE", "en-US"))
    speed: float = field(default_factory=lambda: _float("ELEVEN_SPEED", 1.12))
    stability: float = field(
        default_factory=lambda: _float(
            "ELEVEN_STABILITY", stability_from_voice_temperature(1.15)
        )
    )
    similarity_boost: float = field(
        default_factory=lambda: _float("ELEVEN_SIMILARITY_BOOST", 0.75)
    )
    style: float = field(default_factory=lambda: _float("ELEVEN_STYLE", 0.0))
    speaker_boost: bool = field(default_factory=lambda: _bool("ELEVEN_USE_SPEAKER_BOOST", True))
    pronunciation_dict_id: str = field(
        default_factory=lambda: _str("ELEVEN_PRONUNCIATION_DICT_ID")
    )
    pronunciation_dict_version_id: str = field(
        default_factory=lambda: _str("ELEVEN_PRONUNCIATION_DICT_VERSION_ID")
    )


@dataclass(frozen=True)
class CallSettings:
    """Everything Retell exposed as agent-level call behaviour."""

    latency_profile: str = field(default_factory=lambda: resolve_latency_profile())
    interruption_sensitivity: float = field(
        default_factory=lambda: _float("INTERRUPTION_SENSITIVITY", 0.85)
    )
    responsiveness: float = field(
        default_factory=lambda: resolve_responsiveness(resolve_latency_profile())
    )
    max_call_duration_ms: int = field(
        default_factory=lambda: _int("MAX_CALL_DURATION_MS", 664_000)
    )
    end_call_after_silence_ms: int = field(
        default_factory=lambda: _int("END_CALL_AFTER_SILENCE_MS", 261_000)
    )
    reminder_trigger_ms: int = field(default_factory=lambda: _int("REMINDER_TRIGGER_MS", 10_000))
    reminder_max_count: int = field(default_factory=lambda: _int("REMINDER_MAX_COUNT", 2))
    begin_message_delay_ms: int = field(
        default_factory=lambda: _int("BEGIN_MESSAGE_DELAY_MS", 0)
    )
    # Retell ring_duration_ms = 17000
    ring_duration_ms: int = field(default_factory=lambda: _int("RING_DURATION_MS", 17_000))
    noise_cancellation: str = field(
        default_factory=lambda: _str("NOISE_CANCELLATION", "BVCTelephony")
    )
    use_turn_detector: bool = field(default_factory=lambda: _bool("USE_TURN_DETECTOR", True))

    # Retell `boosted_keywords` -> Deepgram nova-3 keyterms.
    #
    # These are the only words the recogniser is told to expect, so the list has
    # to contain the phrases the routing decision actually hangs on — not just
    # the firm's own names. Deepgram was returning "quad accident", "call
    # accident" and "call ex" for "car accident", which meant the router had
    # nothing to match and asked the caller the same question again.
    #
    # "Bush & Bush Law Group" was also wrong as a keyterm: keyterms are matched
    # against speech, and nobody says "ampersand".
    boosted_keywords: tuple[str, ...] = (
        "Bush and Bush",
        "Bush and Bush Law Group",
        "Claire",
        # routing vocabulary - one keyterm per phrase the classifier keys on
        "car accident",
        "auto accident",
        "hit and run",
        "rear ended",
        "fender bender",
        "slip and fall",
        "slipped and fell",
        "premises liability",
        "medical malpractice",
        "misdiagnosed",
        "wrongful termination",
        "workers comp",
        "sexual harassment",
    )

    @property
    def is_fast(self) -> bool:
        return self.latency_profile == "fast"

    @property
    def interruption_min_duration(self) -> float:
        return interruption_min_duration(self.interruption_sensitivity)

    @property
    def endpointing(self) -> tuple[float, float]:
        return resolve_endpointing(self.latency_profile, self.responsiveness)


@dataclass(frozen=True)
class PostCallSettings:
    webhook_url: str = field(default_factory=lambda: _str("POST_CALL_WEBHOOK_URL"))
    webhook_timeout_ms: int = field(
        default_factory=lambda: _int("POST_CALL_WEBHOOK_TIMEOUT_MS", 10_000)
    )
    analysis_timeout_ms: int = field(
        default_factory=lambda: _int("POST_CALL_ANALYSIS_TIMEOUT_MS", 8_000)
    )
    records_dir: Path = field(
        default_factory=lambda: Path(_str("CALL_RECORDS_DIR", "./call_records"))
    )


@dataclass(frozen=True)
class APISettings:
    host: str = field(default_factory=lambda: _str("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("API_PORT", 8000))
    api_key: str = field(default_factory=lambda: _str("API_KEY", "change-me"))


livekit = LiveKitSettings()
stt = STTSettings()
llm = LLMSettings()
tts = TTSSettings()
call = CallSettings()
post_call = PostCallSettings()
api = APISettings()


def validate() -> list[str]:
    """Return a list of human-readable problems with the current config."""
    problems: list[str] = []
    if not livekit.url:
        problems.append("LIVEKIT_URL is not set")
    if not livekit.api_key or not livekit.api_secret:
        problems.append("LIVEKIT_API_KEY / LIVEKIT_API_SECRET are not set")
    if not stt.api_key:
        problems.append("DEEPGRAM_API_KEY is not set")
    if not llm.api_key:
        problems.append("OPENAI_API_KEY is not set")
    if not tts.api_key:
        problems.append("ELEVENLABS_API_KEY is not set")
    if not tts.voice_id:
        problems.append("ELEVEN_VOICE_ID is not set (the ElevenLabs voice to speak with)")
    if not post_call.webhook_url:
        problems.append(
            "POST_CALL_WEBHOOK_URL is not set - post-call analysis will only be saved locally"
        )
    return problems
