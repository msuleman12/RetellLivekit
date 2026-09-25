"""STT / LLM / TTS and turn handling for the phone pipeline.

Deepgram Flux decides when the caller has finished speaking
(`turn_detection="stt"`); the session's bundled VAD handles barge-in.
"""

from livekit.agents import TurnHandlingOptions
from livekit.plugins import deepgram, elevenlabs, groq, openai

from config import DEEPGRAM, ELEVENLABS, GROQ, KEYTERMS, OPENAI, CALL


def build_stt() -> deepgram.STTv2:
    return deepgram.STTv2(
        model=DEEPGRAM.model,
        api_key=DEEPGRAM.api_key,
        eager_eot_threshold=DEEPGRAM.eager_eot_threshold,
        eot_threshold=DEEPGRAM.eot_threshold,
        eot_timeout_ms=DEEPGRAM.eot_timeout_ms,
        keyterm=KEYTERMS,
    )


def build_llm(
    model: str = OPENAI.model,
    temperature: float = OPENAI.temperature,
    *,
    groq_model: str = GROQ.model,
) -> openai.LLM:
    """Each caller names the model it wants on both providers; ENABLE_GROQ_LLM
    picks between them. Groq's LLM subclasses the OpenAI one against Groq's
    OpenAI-compatible endpoint, so the session wiring is identical either way.
    """
    if GROQ.enabled:
        return groq.LLM(model=groq_model, temperature=temperature, api_key=GROQ.api_key)
    return openai.LLM(model=model, temperature=temperature, api_key=OPENAI.api_key)


def build_tts() -> elevenlabs.TTS:
    dictionaries = []
    if ELEVENLABS.pronunciation_dict_id:
        dictionaries.append(
            elevenlabs.PronunciationDictionaryLocator(
                pronunciation_dictionary_id=ELEVENLABS.pronunciation_dict_id,
                version_id=ELEVENLABS.pronunciation_dict_version_id,
            )
        )
    return elevenlabs.TTS(
        api_key=ELEVENLABS.api_key,
        voice_id=ELEVENLABS.voice_id,
        model=ELEVENLABS.model,
        language=ELEVENLABS.language,
        voice_settings=elevenlabs.VoiceSettings(
            stability=ELEVENLABS.stability,
            similarity_boost=ELEVENLABS.similarity_boost,
            style=ELEVENLABS.style,
            speed=ELEVENLABS.speed,
            use_speaker_boost=ELEVENLABS.use_speaker_boost,
        ),
        pronunciation_dictionary_locators=dictionaries,
    )


def build_turn_handling() -> TurnHandlingOptions:
    return TurnHandlingOptions(
        turn_detection="stt",
        # Flux already waited for end of turn; do not pad it.
        endpointing={"mode": "fixed", "min_delay": 0.0, "max_delay": 2.0},
        interruption={
            # Set explicitly: LiveKit turns adaptive off by default in
            # production, leaving plain VAD, which treats "okay" and "mm-hmm"
            # as interruptions.
            "mode": "adaptive",
            "min_duration": CALL.interruption_min_duration_s,
            "resume_false_interruption": True,
        },
        preemptive_generation={"enabled": True},
    )
