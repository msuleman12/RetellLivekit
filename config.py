"""Environment configuration.

Every value is read once at import. Required keys raise immediately so a
misconfigured worker fails at startup instead of on the first call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to .env (see .env.example).")
    return value


def _str(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in ("1", "true", "yes", "on") if raw else default


@dataclass(frozen=True)
class LiveKitConfig:
    url: str = _required("LIVEKIT_URL")
    api_key: str = _required("LIVEKIT_API_KEY")
    api_secret: str = _required("LIVEKIT_API_SECRET")
    # Must match the agentName on the SIP dispatch rule, or calls ring and drop.
    agent_name: str = _str("AGENT_NAME", "bush-bush-intake")
    # Worker health-check HTTP port. The older ai-receptionist services on this
    # server already hold 8081 and 8082, so this one defaults elsewhere.
    http_port: int = _int("AGENT_HTTP_PORT", 8083)


@dataclass(frozen=True)
class DeepgramConfig:
    api_key: str = _required("DEEPGRAM_API_KEY")
    model: str = _str("STT_MODEL", "flux-general-en")
    eager_eot_threshold: float = _float("STT_EAGER_EOT_THRESHOLD", 0.4)
    eot_threshold: float = _float("STT_EOT_THRESHOLD", 0.7)
    eot_timeout_ms: int = _int("STT_EOT_TIMEOUT_MS", 5000)


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str = _required("OPENAI_API_KEY")
    model: str = _str("LLM_MODEL", "gpt-4.1-mini")
    temperature: float = _float("LLM_TEMPERATURE", 0.55)
    router_model: str = _str("ROUTER_LLM_MODEL", "gpt-4.1-mini")
    router_temperature: float = _float("ROUTER_LLM_TEMPERATURE", 0.3)
    post_call_model: str = _str("POST_CALL_ANALYSIS_MODEL", "gpt-5-mini")


@dataclass(frozen=True)
class GroqConfig:
    """Groq's OpenAI-compatible endpoint, used to cut time-to-first-token.

    Off by default. Turning it on moves the conversation and router models onto
    Groq; post-call analysis stays on OpenAI either way, because it runs after
    the caller has hung up and its latency costs nobody anything.
    """

    enabled: bool = _bool("ENABLE_GROQ_LLM", False)
    api_key: str = _str("GROQ_API_KEY", "")
    # Deliberately not one of the gpt-oss reasoning models: their hidden
    # thinking tokens land before the first spoken word, which is the only
    # latency the caller actually hears.
    model: str = _str("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
    # Routing is a single classification, so the smallest model is enough.
    router_model: str = _str("GROQ_ROUTER_LLM_MODEL", "llama-3.1-8b-instant")

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        if not self.api_key:
            raise RuntimeError(
                "ENABLE_GROQ_LLM is on but GROQ_API_KEY is not set. "
                "Add it to .env (see .env.example)."
            )
        # Groq keys start with "gsk_". An xAI key ("xai-") is a different
        # company -- Groq serves open models fast, Grok is xAI's own model --
        # and the endpoint only rejects it once a caller is already on the line.
        if not self.api_key.startswith("gsk_"):
            raise RuntimeError(
                f"GROQ_API_KEY does not look like a Groq key (starts with "
                f"{self.api_key[:4]!r}, expected 'gsk_'). Groq keys come from "
                f"console.groq.com; an 'xai-' key belongs to xAI/Grok, which "
                f"this pipeline does not use."
            )


@dataclass(frozen=True)
class ElevenLabsConfig:
    api_key: str = _required("ELEVENLABS_API_KEY")
    voice_id: str = _required("ELEVEN_VOICE_ID")
    model: str = _str("ELEVEN_MODEL", "eleven_flash_v2_5")
    language: str = _str("ELEVEN_LANGUAGE", "en")
    speed: float = _float("ELEVEN_SPEED", 1.12)
    stability: float = _float("ELEVEN_STABILITY", 0.425)
    similarity_boost: float = _float("ELEVEN_SIMILARITY_BOOST", 0.75)
    style: float = _float("ELEVEN_STYLE", 0.0)
    use_speaker_boost: bool = _bool("ELEVEN_USE_SPEAKER_BOOST", True)
    pronunciation_dict_id: str = _str("ELEVEN_PRONUNCIATION_DICT_ID", "")
    pronunciation_dict_version_id: str = _str("ELEVEN_PRONUNCIATION_DICT_VERSION_ID", "")


@dataclass(frozen=True)
class CallConfig:
    max_duration_s: int = _int("MAX_CALL_DURATION_S", 664)
    # Seconds of mutual silence before the caller is marked "away".
    user_away_timeout_s: float = _float("USER_AWAY_TIMEOUT_S", 10.0)
    # "Are you still there?" nudges before hanging up on a silent line.
    silence_reminders: int = _int("SILENCE_REMINDERS", 2)
    interruption_min_duration_s: float = _float("INTERRUPTION_MIN_DURATION_S", 0.25)


@dataclass(frozen=True)
class PostCallConfig:
    webhook_url: str = _str("POST_CALL_WEBHOOK_URL", "")
    zapier_webhook_url: str = _str("ZAPIER_WEBHOOK_URL", "")
    webhook_timeout_s: float = _float("POST_CALL_WEBHOOK_TIMEOUT_S", 10.0)
    records_dir: Path = Path(_str("CALL_RECORDS_DIR", "./call_records"))


@dataclass(frozen=True)
class OfficeHoursConfig:
    """When the firm's attorneys are reachable. Outside these hours nothing transfers."""

    start_hour: int = _int("OFFICE_HOURS_START", 8)
    end_hour: int = _int("OFFICE_HOURS_END", 17)
    # 0 = Monday ... 6 = Sunday.
    days: tuple[int, ...] = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class TransferConfig:
    # Master switch: off means the agent promises a callback instead of transferring.
    enabled: bool = _bool("ENABLE_LIVE_TRANSFERS", False)
    attorney_line: str = _str("ATTORNEY_LINE", "")
    reception_line: str = _str("RECEPTION_LINE", "")
    # How long the caller waits while the target line rings. LiveKit's own
    # default is 30s, which is a long silence on a phone call.
    ringing_timeout_s: float = _float("TRANSFER_RINGING_TIMEOUT_S", 18.0)


@dataclass(frozen=True)
class APIConfig:
    host: str = _str("API_HOST", "0.0.0.0")
    port: int = _int("API_PORT", 8000)
    # Required by api/server.py only; the worker does not use it.
    api_key: str = _str("API_KEY", "")


LIVEKIT = LiveKitConfig()
DEEPGRAM = DeepgramConfig()
OPENAI = OpenAIConfig()
GROQ = GroqConfig()
ELEVENLABS = ElevenLabsConfig()
CALL = CallConfig()
POST_CALL = PostCallConfig()
OFFICE_HOURS = OfficeHoursConfig()
TRANSFER = TransferConfig()
API = APIConfig()

# Phrases Deepgram must hear correctly for routing.
KEYTERMS = [
    "Bush and Bush",
    "Bush and Bush Law Group",
    "Claire",
    "car accident",
    "hit and run",
    "rear ended",
    "slip and fall",
    "premises liability",
    "medical malpractice",
    "misdiagnosed",
    "wrongful termination",
    "workers comp",
    "sexual harassment",
]
