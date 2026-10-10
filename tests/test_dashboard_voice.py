"""Dashboard voice selection, using isolated settings and mocked speech providers."""
import unittest
from unittest.mock import Mock, patch

from tests.harness import setup_env

setup_env(TTS_PROVIDER="elevenlabs", ELEVENLABS_API_KEY="fake-not-used")

from room_agent import control, runtime as rt
from room_agent.audio import voices
from room_agent.tools import voice
from UI.server import app


class DashboardVoiceTests(unittest.TestCase):
    def setUp(self):
        voices.current.fallback = False
        voices.current.eleven = voices.ELEVEN_VOICES["Thomas"][0]
        rt.tts_enabled = True

    def test_catalogue_and_actual_fallback(self):
        data = control._voice_settings()
        self.assertEqual(data["provider"], "elevenlabs")
        self.assertEqual(data["selected"], voices.current.eleven)
        self.assertEqual(len(data["providers"]), 2)
        voices.current.fallback = True
        data = control._voice_settings()
        self.assertEqual(data["provider"], "piper")
        self.assertEqual(data["selected"], voices.current.piper)
        rt.tts_enabled = False
        self.assertFalse(control._voice_settings()["enabled"])

    def test_offline_catalogue_has_no_credentials(self):
        response = app.test_client().get("/api/voices", headers={"Host": "127.0.0.1:8765"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["enabled"])
        self.assertEqual({p["id"] for p in data["providers"]}, {"piper", "elevenlabs"})
        self.assertNotIn("fake-not-used", response.get_data(as_text=True))

    def test_invalid_and_changed_provider_never_execute(self):
        with patch("room_agent.actions.executor.execute") as execute:
            for body in ({"name": "missing", "provider": "elevenlabs"}, {"name": "Brian", "provider": []},
                         {"name": [], "provider": "elevenlabs"}, {"name": "Ryan", "provider": "piper"}):
                self.assertFalse(control.do({"do": "set_voice", **body})["ok"])
            execute.assert_not_called()

    def test_valid_selection_uses_executor(self):
        with patch("room_agent.actions.executor.execute", return_value=Mock(success=True, message="OK: switched")) as execute, patch.object(rt, "new_turn"):
            result = control.do({"do": "set_voice", "provider": "elevenlabs", "name": "Brian"})
            self.assertTrue(result["ok"])
            execute.assert_called_once_with("set_voice", {"name": "Brian"})

    def test_verified_voice_is_saved_and_failed_voice_keeps_previous(self):
        import requests

        with patch.object(voice.tts.el, "post") as post, patch.object(voice.tts, "clear_clips"), patch.object(voice.tts, "record_in_background"):
            post.return_value = Mock()
            result = voice.set_voice("Brian")
            self.assertTrue(result.startswith("OK:"))
            self.assertEqual(voices.current.eleven, voices.ELEVEN_VOICES["Brian"][0])
            self.assertEqual(voices._load_saved()["elevenlabs_voice"], voices.current.eleven)
            previous = voices.current.eleven
            post.side_effect = requests.RequestException("test unavailable")
            result = voice.set_voice("George")
            self.assertTrue(result.startswith("FAILED:"))
            self.assertEqual(voices.current.eleven, previous)
            self.assertEqual(voices._load_saved()["elevenlabs_voice"], previous)

    def test_local_voice_verified_before_install_and_save(self):
        voices.current.fallback = True
        previous = voices.current.piper
        with patch.object(voice.tts, "open_piper", side_effect=RuntimeError("test unavailable")), patch.object(voice.tts, "install_piper_voice") as install:
            self.assertTrue(voice.set_voice("Ryan").startswith("FAILED:"))
            self.assertEqual(voices.current.piper, previous)
            install.assert_not_called()
        local = Mock()
        local.synthesize.return_value = iter([b"audio"])
        wanted = voices.PIPER_VOICES["Amy"][0]
        def install_voice(vid, instance):
            voices.current.piper = vid
        with patch.object(voice.tts, "open_piper", return_value=local), patch.object(voice.tts, "install_piper_voice", side_effect=install_voice) as install, patch.object(voice.tts, "clear_clips"), patch.object(voice.tts, "record_in_background"):
            self.assertTrue(voice.set_voice("Amy").startswith("OK:"))
            install.assert_called_once_with(wanted, local)
            self.assertEqual(voices._load_saved()["piper_voice"], wanted)


if __name__ == "__main__":
    unittest.main()
