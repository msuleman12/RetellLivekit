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
class APIConfig:
    host: str = _str("API_HOST", "0.0.0.0")
    port: int = _int("API_PORT", 8000)
    # Required by api/server.py only; the worker does not use it.
    api_key: str = _str("API_KEY", "")


LIVEKIT = LiveKitConfig()
DEEPGRAM = DeepgramConfig()
OPENAI = OpenAIConfig()
ELEVENLABS = ElevenLabsConfig()
CALL = CallConfig()
POST_CALL = PostCallConfig()
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
