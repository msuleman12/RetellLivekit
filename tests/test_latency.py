"""Unit tests for LATENCY_PROFILE=parity|fast behaviour.

Run:

    python -m unittest tests.test_latency -v
"""

from __future__ import annotations

import logging
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import settings  # noqa: E402
from src.lifecycle import CallLifecycle  # noqa: E402
from src.state import CallState  # noqa: E402


class ResolveLatencyHelpersTests(unittest.TestCase):
    def test_resolve_latency_profile_accepts_parity_and_fast(self) -> None:
        self.assertEqual(settings.resolve_latency_profile("parity"), "parity")
        self.assertEqual(settings.resolve_latency_profile("FAST"), "fast")
        self.assertEqual(settings.resolve_latency_profile("  Fast  "), "fast")

    def test_resolve_latency_profile_unknown_falls_back_to_fast(self) -> None:
        """Default flipped to `fast`: `parity` leaves turn detection on
        LiveKit's cloud TurnDetector, which costs a 1-2s round-trip per turn."""
        self.assertEqual(settings.resolve_latency_profile("nope"), "fast")
        self.assertEqual(settings.resolve_latency_profile(""), "fast")

    def test_parity_stt_endpointing_default(self) -> None:
        self.assertEqual(settings.resolve_stt_endpointing_ms("parity", None), 450)
        self.assertEqual(settings.resolve_stt_endpointing_ms("parity", ""), 450)

    def test_fast_stt_endpointing_default(self) -> None:
        self.assertEqual(settings.resolve_stt_endpointing_ms("fast", None), 200)
        self.assertEqual(settings.resolve_stt_endpointing_ms("fast", ""), 200)

    def test_explicit_stt_endpointing_overrides_profile(self) -> None:
        self.assertEqual(settings.resolve_stt_endpointing_ms("fast", "300"), 300)
        self.assertEqual(settings.resolve_stt_endpointing_ms("parity", "200"), 200)

    def test_invalid_stt_endpointing_falls_back_to_profile(self) -> None:
        self.assertEqual(settings.resolve_stt_endpointing_ms("fast", "abc"), 200)
        self.assertEqual(settings.resolve_stt_endpointing_ms("parity", "abc"), 450)

    def test_responsiveness_defaults(self) -> None:
        self.assertEqual(settings.resolve_responsiveness("parity", None), 0.95)
        self.assertEqual(settings.resolve_responsiveness("fast", None), 1.0)

    def test_explicit_responsiveness_overrides_profile(self) -> None:
        self.assertEqual(settings.resolve_responsiveness("fast", "0.8"), 0.8)

    def test_parity_endpointing_uncapped(self) -> None:
        self.assertEqual(settings.resolve_endpointing("parity", 0.95), (0.193, 3.693))

    def test_fast_endpointing_caps_max_delay(self) -> None:
        self.assertEqual(settings.resolve_endpointing("fast", 1.0), (0.8, 2.5))
        # Even with low responsiveness, fast still caps the max.
        min_d, max_d = settings.resolve_endpointing("fast", 0.5)
        self.assertLessEqual(max_d, 2.5)
        # A floor, not a ceiling: never commit a turn faster than 0.8s, so a
        # pause in the middle of a sentence does not become a new turn.
        self.assertGreaterEqual(min_d, 0.8)


class BuildTurnHandlingTests(unittest.TestCase):
    def test_fast_enables_preemptive_tts(self) -> None:
        from src import models

        fake_call = SimpleNamespace(
            is_fast=True,
            endpointing=(0.1, 1.2),
            interruption_min_duration=0.247,
            use_turn_detector=False,
        )
        with patch.object(models.settings, "call", fake_call):
            opts = models.build_turn_handling()

        self.assertTrue(opts["preemptive_generation"]["enabled"])
        self.assertTrue(opts["preemptive_generation"]["preemptive_tts"])
        self.assertEqual(opts["endpointing"]["min_delay"], 0.1)
        self.assertEqual(opts["endpointing"]["max_delay"], 1.2)
        self.assertEqual(opts["turn_detection"], "vad")
        self.assertEqual(opts["interruption"]["mode"], "vad")
        self.assertEqual(opts["interruption"]["false_interruption_timeout"], 1.0)

    def test_parity_disables_preemptive_tts(self) -> None:
        from src import models

        fake_call = SimpleNamespace(
            is_fast=False,
            endpointing=(0.193, 3.693),
            interruption_min_duration=0.247,
            use_turn_detector=False,
        )
        with patch.object(models.settings, "call", fake_call):
            opts = models.build_turn_handling()

        self.assertTrue(opts["preemptive_generation"]["enabled"])
        self.assertFalse(opts["preemptive_generation"]["preemptive_tts"])
        self.assertEqual(opts["endpointing"]["min_delay"], 0.193)
        self.assertEqual(opts["endpointing"]["max_delay"], 3.693)

    def test_fast_forces_vad_turn_detection_even_when_enabled(self) -> None:
        from src import models

        fake_call = SimpleNamespace(
            is_fast=True,
            endpointing=(0.1, 1.2),
            interruption_min_duration=0.247,
            use_turn_detector=True,
        )
        with (
            patch.object(models.settings, "call", fake_call),
            patch.dict("sys.modules", {"livekit.agents.inference": MagicMock()}),
        ):
            opts = models.build_turn_handling()

        # Must be explicit "vad" — omitting the key lets AgentSession default
        # to inference.TurnDetector() and reintroduce ~1–2s cloud RTT.
        self.assertEqual(opts["turn_detection"], "vad")
        self.assertEqual(opts["interruption"]["mode"], "vad")

    def test_parity_uses_cloud_turn_detector_when_enabled(self) -> None:
        from src import models

        fake_call = SimpleNamespace(
            is_fast=False,
            endpointing=(0.193, 3.693),
            interruption_min_duration=0.247,
            use_turn_detector=True,
        )
        with patch.object(models.settings, "call", fake_call):
            opts = models.build_turn_handling()

        # Constructed when parity + USE_TURN_DETECTOR; may be None only if import fails.
        self.assertIn("turn_detection", opts)
        self.assertIsNotNone(opts["turn_detection"])


class BuildSttTests(unittest.TestCase):
    def test_fast_disables_smart_format_and_filler_words(self) -> None:
        from src import models

        captured: dict = {}

        class FakeSTT:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

        fake_call = SimpleNamespace(is_fast=True, boosted_keywords=("Claire",))
        fake_stt = SimpleNamespace(
            model="nova-3",
            language="en-US",
            api_key="test-key",
            endpointing_ms=200,
        )
        with (
            patch.object(models.settings, "call", fake_call),
            patch.object(models.settings, "stt", fake_stt),
            patch.object(models.deepgram, "STT", FakeSTT),
        ):
            models.build_stt()

        self.assertTrue(captured["interim_results"])
        self.assertFalse(captured["smart_format"])
        self.assertFalse(captured["filler_words"])
        self.assertTrue(captured["numerals"])
        self.assertEqual(captured["endpointing_ms"], 200)

    def test_parity_keeps_smart_format_and_filler_words(self) -> None:
        from src import models

        captured: dict = {}

        class FakeSTT:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

        fake_call = SimpleNamespace(is_fast=False, boosted_keywords=("Claire",))
        fake_stt = SimpleNamespace(
            model="nova-3",
            language="en-US",
            api_key="test-key",
            endpointing_ms=450,
        )
        with (
            patch.object(models.settings, "call", fake_call),
            patch.object(models.settings, "stt", fake_stt),
            patch.object(models.deepgram, "STT", FakeSTT),
        ):
            models.build_stt()

        self.assertTrue(captured["smart_format"])
        self.assertTrue(captured["filler_words"])
        self.assertEqual(captured["endpointing_ms"], 450)


class BuildVadTests(unittest.TestCase):
    def test_fast_uses_stt_silence_without_point_four_floor(self) -> None:
        from src import models

        captured: dict = {}

        class FakeVAD:
            @staticmethod
            def load(**kwargs):
                captured.update(kwargs)
                return MagicMock()

        fake_call = SimpleNamespace(is_fast=True)
        fake_stt = SimpleNamespace(endpointing_ms=200)
        with (
            patch.object(models.settings, "call", fake_call),
            patch.object(models.settings, "stt", fake_stt),
            patch.object(models.silero, "VAD", FakeVAD),
        ):
            models.build_vad()

        self.assertEqual(captured["min_silence_duration"], 0.2)

    def test_parity_applies_point_four_floor(self) -> None:
        from src import models

        captured: dict = {}

        class FakeVAD:
            @staticmethod
            def load(**kwargs):
                captured.update(kwargs)
                return MagicMock()

        fake_call = SimpleNamespace(is_fast=False)
        fake_stt = SimpleNamespace(endpointing_ms=250)
        with (
            patch.object(models.settings, "call", fake_call),
            patch.object(models.settings, "stt", fake_stt),
            patch.object(models.silero, "VAD", FakeVAD),
        ):
            models.build_vad()

        self.assertEqual(captured["min_silence_duration"], 0.4)


class ReplyLatencyLogTests(unittest.TestCase):
    def test_logs_delta_from_user_final_to_agent_speaking(self) -> None:
        session = MagicMock()
        lifecycle = CallLifecycle(session, CallState())

        with self.assertLogs("bushbush.lifecycle", level="INFO") as cm:
            lifecycle._on_user_input(
                SimpleNamespace(transcript="hello there", is_final=True)
            )
            # Simulate ~50ms of model/TTS work before the agent starts speaking.
            time.sleep(0.05)
            lifecycle._on_agent_state(SimpleNamespace(new_state="speaking"))

        joined = "\n".join(cm.output)
        self.assertIn("reply_latency_ms=", joined)
        self.assertIn("profile=", joined)
        self.assertIsNone(lifecycle._user_final_at)

    def test_interim_transcript_does_not_start_timer(self) -> None:
        lifecycle = CallLifecycle(MagicMock(), CallState())
        lifecycle._on_user_input(
            SimpleNamespace(transcript="hel", is_final=False)
        )
        self.assertIsNone(lifecycle._user_final_at)

    def test_agent_speaking_without_user_final_is_noop(self) -> None:
        lifecycle = CallLifecycle(MagicMock(), CallState())
        with self.assertLogs("bushbush.lifecycle", level="INFO") as cm:
            # Force at least one log record so assertLogs does not fail empty.
            logging.getLogger("bushbush.lifecycle").info("probe")
            lifecycle._on_agent_state(SimpleNamespace(new_state="speaking"))
        self.assertTrue(all("reply_latency_ms=" not in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
