"""Speaker verification: is this snippet YOUR voice? Used so that only you can interrupt the agent.

A small speaker-embedding model (WeSpeaker ResNet34, ONNX, runs locally on the CPU in ~20-35 ms) turns a snippet into a
voiceprint vector; the cosine similarity to the voiceprint you enrolled (python main.py --enroll-voice) is the score.
No voiceprint, no model or SPEAKER_VERIFY=0 -> the gate is off and interrupting works exactly as before."""

import logging
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from room_agent import config

log = logging.getLogger("room-agent")
SR = 16000
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "wespeaker_en_voxceleb_resnet34_LM.onnx")
SHORT_S, LONG_S = 0.8, 1.4  # snippet lengths (s) that switch the threshold: first look / second look / final look


class SpeakerGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._extractor = None
        self._print = None  # enrolled voiceprint (unit vector)
        self._tried = False
        self.error = ""

    # ---------- loading ----------
    def _load(self):
        """Load the model and the voiceprint once. Any problem switches the gate off (never breaks interrupting)."""
        if self._tried:
            return self._extractor is not None and self._print is not None
        with self._lock:
            if self._tried:
                return self._extractor is not None and self._print is not None
            self._tried = True
            try:
                if not config.SPEAKER_VERIFY:
                    self.error = "SPEAKER_VERIFY=0"
                    return False
                if not config.VOICEPRINT_FILE.exists():
                    self.error = f"no voiceprint yet: run python main.py --enroll-voice"
                    return False
                saved = np.load(config.VOICEPRINT_FILE, allow_pickle=False)
                if str(saved["model"]) != config.SPEAKER_MODEL_FILE.name:
                    self.error = f"voiceprint was made with {saved['model']}, not {config.SPEAKER_MODEL_FILE.name}: enroll again"
                    return False
                self._extractor = make_extractor()
                self._print = saved["centroid"].astype(np.float32)
                log.info("speaker verification on (voiceprint of %.0f s, accept >= %.2f)", float(saved["seconds"]), config.SPEAKER_ACCEPT)
            except Exception as e:  # missing package, download failed, corrupt file...
                self._extractor, self._print = None, None
                self.error = f"{e.__class__.__name__}: {e}"
                log.warning("speaker verification off (%s); anyone's voice can interrupt", self.error)
            return self._extractor is not None and self._print is not None

    def active(self):
        return self._load()

    # ---------- scoring ----------
    def score(self, pcm):
        """Cosine similarity (-1..1) between this snippet and your voiceprint, or None when the gate is off."""
        if not self._load():
            return None
        return float(embed(self._extractor, pcm) @ self._print)

    def verdict(self, pcm):
        """-> (state, score). state: "you" | "unsure" (listen longer) | "not you" | "off" (gate not active)."""
        try:
            s = self.score(pcm)
        except Exception as e:
            self._extractor = None
            self.error = f"{e.__class__.__name__}: {e}"
            log.warning("speaker verification off (%s); anyone's voice can interrupt", self.error)
            return "off", None
        if s is None:
            return "off", None
        seconds = len(pcm) / SR
        thr = (config.SPEAKER_ACCEPT_FAST if seconds < SHORT_S else config.SPEAKER_ACCEPT if seconds < LONG_S
               else config.SPEAKER_ACCEPT_LONG)
        if s >= thr:
            log.info("speaker check: you (%.2f)", s)
            return "you", s
        if s < config.SPEAKER_REJECT or seconds >= LONG_S:
            log.info("speaker check: not you (%.2f), ignored", s)
            return "not you", s
        log.info("speaker check: unsure (%.2f), listening a bit longer", s)
        return "unsure", s


gate = SpeakerGate()


def verify_speaker_only(pcm, check_speaker=True):
    """Barge-in verifier for setups without the Whisper check (same return shape as stt.verify_barge)."""
    state, score = gate.verdict(pcm) if check_speaker else ("off", None)
    if state == "not you":
        return False, f"not your voice ({score:.2f})"
    if state == "unsure":
        return False, f"noise: not sure it's your voice yet ({score:.2f})"
    return True, "your voice" if score is None else f"your voice ({score:.2f})"


# ---------- model + embedding helpers (also used by enrollment and the tests) ----------
def ensure_model():
    path = config.SPEAKER_MODEL_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading the speaker model (%s, ~25 MB)...", path.name)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


def make_extractor():
    import sherpa_onnx

    return sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(ensure_model()), num_threads=1, debug=False, provider="cpu"))


def embed(extractor, pcm):
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=SR, waveform=np.asarray(pcm, dtype=np.float32) / 32768.0)
    stream.input_finished()
    e = np.array(extractor.compute(stream), dtype=np.float32)
    return e / (np.linalg.norm(e) + 1e-9)


def build_voiceprint(extractor, clips, win_s=3.0, hop_s=1.5):
    """Voiceprint from speech-only clips (int16 16 kHz): the mean of the embeddings of overlapping 3 s windows.
    -> (centroid, per-window similarity to it, number of windows)."""
    win, hop = int(win_s * SR), int(hop_s * SR)
    embs = [embed(extractor, c[a: a + win]) for c in clips for a in range(0, max(len(c) - win, 0) + 1, hop) if len(c) >= win * 0.6]
    if not embs:
        raise ValueError("not enough speech to build a voiceprint")
    centroid = np.mean(embs, axis=0)
    centroid /= np.linalg.norm(centroid)
    return centroid, np.array([float(e @ centroid) for e in embs]), len(embs)


def save_voiceprint(centroid, seconds, path=None):
    path = Path(path or config.VOICEPRINT_FILE)
    np.savez(path, centroid=centroid.astype(np.float32), model=config.SPEAKER_MODEL_FILE.name, seconds=float(seconds),
             created=time.strftime("%Y-%m-%d %H:%M:%S"))
    return path
