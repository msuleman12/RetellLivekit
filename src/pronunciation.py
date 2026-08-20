"""Retell's `pronunciation_dictionary`, reproduced for ElevenLabs.

Retell shipped IPA entries with the agent:

    Claire  -> /klɛɹ/
    Sawana  -> /səˈwɑnə/

ElevenLabs handles this two ways, and this module supports both:

1. **Pronunciation dictionary (preferred).** Create a dictionary in the
   ElevenLabs dashboard with the same IPA entries, then set
   `ELEVEN_PRONUNCIATION_DICT_ID` / `ELEVEN_PRONUNCIATION_DICT_VERSION_ID`.
   The ids are passed straight to the TTS request and nothing here runs.

2. **Phonetic respelling fallback.** If no dictionary is configured, the text
   is rewritten before it reaches TTS so the model says the word correctly
   anyway. The transcript the caller sees is unaffected - only the audio path
   is rewritten.
"""

from __future__ import annotations

import re

# word -> (IPA as configured on Retell, respelling used for the fallback)
PRONUNCIATIONS: dict[str, tuple[str, str]] = {
    "Claire": ("klɛɹ", "Clair"),
    "Sawana": ("səˈwɑnə", "suh-WAH-nuh"),
}

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE), respelling)
    for word, (_ipa, respelling) in PRONUNCIATIONS.items()
]


def apply_pronunciation(text: str) -> str:
    """Rewrite known words to a phonetic respelling for the TTS engine."""
    for pattern, respelling in _PATTERNS:
        text = pattern.sub(respelling, text)
    return text
