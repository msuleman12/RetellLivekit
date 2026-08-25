"""STT / LLM / TTS / VAD / turn-detection factories.

Every constructor argument here traces back to a Retell setting; see the
comment above each one.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx as _httpx
from livekit.agents import NOT_GIVEN, EndpointingOptions, InterruptionOptions, TurnHandlingOptions
from livekit.plugins import deepgram, elevenlabs, openai, silero

from . import settings

logger = logging.getLogger("bushbush.models")


# ---------------------------------------------------------------------------
# STT - Retell: stt_mode "custom", provider deepgram, endpointing_ms 450
# ---------------------------------------------------------------------------
def build_stt() -> deepgram.STT:
    fast = settings.call.is_fast
    return deepgram.STT(
        model=settings.stt.model,
        language=settings.stt.language,
        api_key=settings.stt.api_key or NOT_GIVEN,
        endpointing_ms=settings.stt.endpointing_ms,
        interim_results=True,
        # Fast profile skips light post-processing; keep numerals for phone capture.
        punctuate=True,
        smart_format=not fast,
        filler_words=not fast,
        numerals=True,
        # NOTE - do not set `utterance_end_ms` here. It was tried as an
        # end-of-turn backstop and made things worse: it forces END_OF_SPEECH
        # after a 1s gap between words, so a caller thinking mid-sentence had
        # "I was" / "someone hit my car" / "at the intersection" / "and ran"
        # committed as four separate turns. Each turn is its own LLM reply, and
        # four queued replies on a slow uplink is what produced
        # "failed to generate LLM completion: Request timed out".
        # Deepgram's own endpoint signal decides the turn; leave it alone.
        # Retell `boosted_keywords`
        keyterm=list(settings.call.boosted_keywords),
    )


# ---------------------------------------------------------------------------
# LLM - Retell: gpt-4.1-mini @ 0.55 (practice agents), gpt-4.1-nano (router)
#
# NOTE - do not reintroduce `parallel_tool_calls` here.
#
# OpenAI rejects that parameter outright when a request carries no `tools`:
#
#     400 Invalid value for 'parallel_tool_calls': 'parallel_tool_calls' is
#         only allowed when 'tools' are specified.
#
# LiveKit sets it on every request without checking whether tools are present
# (livekit/agents/inference/llm.py, which the OpenAI plugin's LLMStream
# subclasses), so a single agent with no tools — the router — turned every
# reply into a 400 and the agent went silent for the whole call. The greeting
# still played because `session.say()` is TTS only and never touches the LLM.
#
# It was never much of a Retell mapping anyway: Retell's `tool_call_strict_mode`
# is about strict JSON schema adherence, not about parallel calls. The intake
# agents carry exactly one tool, `end_call`, and calling it twice is harmless —
# `finish_session` returns immediately once `state.call_ended` is set.
# ---------------------------------------------------------------------------
def build_llm() -> openai.LLM:
    return openai.LLM(
        model=settings.llm.model,
        temperature=settings.llm.temperature,
        api_key=settings.llm.api_key or NOT_GIVEN,
        # Without this the plugin waits on the SDK default before it reports
        # "Request timed out" and retries, which on a bad link means the caller
        # sits in silence for the whole window. Fail fast, retry sooner.
        timeout=_httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
    )


def build_router_llm() -> openai.LLM:
    return openai.LLM(
        model=settings.llm.router_model,
        temperature=settings.llm.router_temperature,
        api_key=settings.llm.api_key or NOT_GIVEN,
    )


def build_async_openai(*, max_retries: int | None = None) -> Any:
    """Shared AsyncOpenAI client for routing, live extract, and post-call.

    Do not construct this per caller turn — each instance opens its own TLS
    pool, which is hundreds of ms of dead air. Callers that need a singleton
    should keep the return value.
    """
    from openai import AsyncOpenAI

    kwargs: dict[str, Any] = {"api_key": settings.llm.api_key or None}
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# TTS - Retell: 11labs-Nico, eleven_flash_v2_5, speed 1.12, temperature 1.15
# ---------------------------------------------------------------------------
def build_tts() -> elevenlabs.TTS:
    cfg = settings.tts

    # Without this the plugin builds a URL like /v1/text-to-speech//stream-input
    # and ElevenLabs answers 403 with a stack trace that says nothing useful.
    if not cfg.voice_id.strip():
        raise RuntimeError(
            "ELEVEN_VOICE_ID is empty. Set it in .env to the ElevenLabs voice id "
            "you want Claire to speak with - the id, not the voice name. Find it "
            "at elevenlabs.io under Voices -> the voice -> Copy voice ID, or run: "
            'curl -H "xi-api-key: $ELEVENLABS_API_KEY" https://api.elevenlabs.io/v1/voices'
        )
    if not cfg.api_key.strip():
        raise RuntimeError("ELEVENLABS_API_KEY is empty. Set it in .env.")

    voice_settings = elevenlabs.VoiceSettings(
        stability=cfg.stability,               # <- voice_temperature 1.15
        similarity_boost=cfg.similarity_boost,
        style=cfg.style,
        speed=cfg.speed,                       # <- voice_speed 1.12
        use_speaker_boost=cfg.speaker_boost,
    )

    locators = NOT_GIVEN
    if cfg.pronunciation_dict_id and cfg.pronunciation_dict_version_id:
        locators = [
            elevenlabs.PronunciationDictionaryLocator(
                pronunciation_dictionary_id=cfg.pronunciation_dict_id,
                version_id=cfg.pronunciation_dict_version_id,
            )
        ]

    fast = settings.call.is_fast

    # NOTE - do not add `chunk_length_schedule` here. The plugin documents
    # `auto_mode` as "reduces latency by disabling chunk schedule and buffers",
    # and it only defaults to True when no schedule is given. Passing a schedule
    # alongside auto_mode=True is at best a no-op and at worst turns the buffer
    # back on. auto_mode is already the lowest-latency setting available.
    return elevenlabs.TTS(
        voice_id=cfg.voice_id,
        model=cfg.model,
        api_key=cfg.api_key or NOT_GIVEN,
        voice_settings=voice_settings,
        pronunciation_dictionary_locators=locators,
        # en-US keeps American pronunciation; fast skips forced text-norm latency.
        language=cfg.language or "en-US",
        apply_text_normalization="auto" if fast else "on",
        apply_language_text_normalization=not fast,
        auto_mode=True,
    )


def uses_elevenlabs_dictionary() -> bool:
    return bool(
        settings.tts.pronunciation_dict_id and settings.tts.pronunciation_dict_version_id
    )


# ---------------------------------------------------------------------------
# VAD
# ---------------------------------------------------------------------------
def build_vad() -> silero.VAD:
    silence_s = settings.stt.endpointing_ms / 1000
    if not settings.call.is_fast:
        # Parity: keep the VAD from cutting the caller off before Deepgram finalises.
        silence_s = max(0.4, silence_s)
    return silero.VAD.load(
        min_silence_duration=silence_s,
        min_speech_duration=0.05,
        activation_threshold=0.5,
    )
# ---------------------------------------------------------------------------
# Turn handling
#   Retell: interruption_sensitivity 0.85, responsiveness 0.95,
#           enable_dynamic_responsiveness false
# ---------------------------------------------------------------------------
def build_turn_handling() -> TurnHandlingOptions:
    min_delay, max_delay = settings.call.endpointing
    fast = settings.call.is_fast

    # CRITICAL: AgentSession defaults turn_detection to inference.TurnDetector()
    # when the key is omitted. Fast MUST set "vad" explicitly or the cloud
    # detector still runs (~1–2s transport RTT on many networks).
    turn_detection: object
    if fast or not settings.call.use_turn_detector:
        turn_detection = "vad"
        if fast:
            logger.info(
                "LATENCY_PROFILE=fast: turn_detection=vad "
                "(explicit; avoids default cloud TurnDetector)"
            )
    else:
        try:
            from livekit.agents import inference

            turn_detection = inference.TurnDetector()
        except Exception as exc:  # pragma: no cover - depends on cloud access
            logger.warning("turn detector unavailable, falling back to VAD: %s", exc)
            turn_detection = "vad"

    interruption: InterruptionOptions = {
        "enabled": True,
        # Retell interruption_sensitivity -> min_duration (see settings.py)
        "min_duration": settings.call.interruption_min_duration,
        "min_words": 0,
        # Retell: allow_dtmf_interruption false, and the prompts insist the
        # agent must never talk over the caller.
        "resume_false_interruption": True,
        # Fast: shorter false-interruption hold + local VAD (no adaptive cloud).
        "false_interruption_timeout": 1.0 if fast else 2.0,
    }
    if fast:
        interruption["mode"] = "vad"

    endpointing = EndpointingOptions(
        # Retell enable_dynamic_responsiveness = false -> fixed
        mode="fixed",
        min_delay=min_delay,
        max_delay=max_delay,
    )

    return {
        "endpointing": endpointing,
        "interruption": interruption,
        "turn_detection": turn_detection,  # type: ignore[typeddict-item]
        "preemptive_generation": {
            "enabled": True,
            # Fast profile starts TTS before the turn is confirmed.
            "preemptive_tts": fast,
        },
    }


# ---------------------------------------------------------------------------
# Noise cancellation
#   Retell: denoising_mode "noise-and-background-speech-cancellation"
# ---------------------------------------------------------------------------
def build_noise_cancellation():
    mode = settings.call.noise_cancellation.strip()
    if not mode or mode.lower() == "off":
        return None
    try:
        from livekit.plugins import noise_cancellation
    except Exception as exc:  # pragma: no cover - optional platform wheel
        logger.warning("noise cancellation plugin not installed (%s); continuing without", exc)
        return None

    factory = getattr(noise_cancellation, mode, None)
    if factory is None:
        logger.warning("unknown NOISE_CANCELLATION=%r; continuing without", mode)
        return None
    try:
        return factory()
    except Exception as exc:  # pragma: no cover
        logger.warning("could not start noise cancellation: %s", exc)
        return None
