"""Build the speaker-verification test cases and compare the ONNX speaker models: python tests/speaker_bench.py [--rebuild] [model filter]
Genuine = your recorded speech (tests/stt_session.npz), enrolled on one half and tested on the other half.
Not you = 8 TTS voices (Jarvis's own Piper voice, 5 other Piper voices, Windows David and Zira) and Jarvis's own echo.
Room = the real AudioEngine echo canceller fed your speech plus the agent's playback delayed 400 ms and 4x louder than your voice."""
import os
import pathlib
import pickle
import subprocess
import sys
import tempfile

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.chdir(ROOT)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

import speaker_eval as ev  # noqa: E402

SR = 16000
B = 1280
MODELS = {
    "wespeaker ResNet34-LM": "models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx",
    "wespeaker ResNet152-LM": "models/speaker/wespeaker_en_voxceleb_resnet152_LM.onnx",
    "wespeaker ResNet221-LM": "models/speaker/wespeaker_en_voxceleb_resnet221_LM.onnx",
    "wespeaker ResNet293-LM": "models/speaker/wespeaker_en_voxceleb_resnet293_LM.onnx",
    "wespeaker CAM++-LM": "models/speaker/wespeaker_en_voxceleb_CAM++_LM.onnx",
    "3D-Speaker CAM++": "models/speaker/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
    "NeMo TitaNet-small": "models/speaker/nemo_en_titanet_small.onnx",
    "NeMo TitaNet-large": "models/speaker/nemo_en_titanet_large.onnx",
}
SENTENCES = ["Can you motivate me to get up and go for a run this morning?", "What alarm are you going to set for me tomorrow?",
             "Stop, I have a question about the weather in San Francisco.", "Tell me a joke, and then remind me to call my mom.",
             "Switch to a different voice and talk a little slower please.", "Wake me up by tomorrow at seven and keep calling until I answer."]


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float32) ** 2))) + 1e-6


def build_cases():
    from piper import PiperVoice

    from room_agent import config
    from room_agent.audio.engine import AudioEngine
    from room_agent.audio.tts import open_piper

    d = np.load(ROOT / "tests" / "stt_session.npz")
    RAW, CLEAN, VOICE, STAMPS, MARKS = d["raw"], d["clean"], d["voice"], d["stamps"], d["marks"]

    user = {}
    for i in range(len(MARKS)):
        lo, hi = int(np.searchsorted(STAMPS, MARKS[i][0] - 0.2)), int(np.searchsorted(STAMPS, MARKS[i][1] + 0.2))
        voiced = [b for b in range(lo, hi) if VOICE[b] >= 0.5]
        if len(voiced) >= 8:
            a, z = max(voiced[0] - 6, lo), min(voiced[-1] + 8, hi)
            user[i] = (RAW[a * B: z * B], CLEAN[a * B: z * B], voiced[0] - a)
    print(f"your speech: {len(user)} items, {sum(len(v[1]) for v in user.values()) / SR:.0f} s")

    def synth(voice, text):
        parts = []
        for c in voice.synthesize(text):
            a = c.audio_int16_array.astype(np.float32)
            g = np.gcd(SR, c.sample_rate)
            parts.append(resample_poly(a, SR // g, c.sample_rate // g))
        return np.concatenate(parts).astype(np.int16)

    voices = {"jarvis(ryan)": open_piper("en_US-ryan-high")}
    voices.update({p.stem: PiperVoice.load(str(p)) for p in (ROOT / "models" / "tts_impostors").glob("*.onnx")})
    other = {n: [synth(v, s) for s in SENTENCES] for n, v in voices.items()}
    tmp = pathlib.Path(tempfile.mkdtemp())
    for voice in ("Microsoft David Desktop", "Microsoft Zira Desktop"):
        short = voice.split()[1]
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.SelectVoice('{voice}'); " + "".join(
                      f"$s.SetOutputToWaveFile('{tmp}\\{short}{i}.wav'); $s.Speak('{s}'); $s.SetOutputToNull(); "
                      for i, s in enumerate(SENTENCES)))
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
        clips = []
        for i in range(len(SENTENCES)):
            a, sr = sf.read(tmp / f"{short}{i}.wav", dtype="float32")
            a = a if a.ndim == 1 else a[:, 0]
            g = np.gcd(SR, sr)
            clips.append((resample_poly(a, SR // g, sr // g) * 32767).astype(np.int16))
        other[short] = clips
    print("other voices:", ", ".join(other))

    text = " ".join(SENTENCES * 3)
    far24 = np.concatenate([resample_poly(c.audio_int16_array.astype(np.float32), config.OUT_SR // np.gcd(config.OUT_SR, c.sample_rate),
                                          c.sample_rate // np.gcd(config.OUT_SR, c.sample_rate))
                            for c in voices["jarvis(ryan)"].synthesize(text)])
    far24 = np.clip(far24, -32768, 32767).astype(np.int16)

    def room(near, ratio=4.0, delay_s=0.4, lead_s=4.0, tail_s=1.0, near_rms=None):
        """The real echo canceller: the agent plays far24 all the time, its echo arrives delay_s late and `ratio` times
        louder than near_rms (your speech level). Returns (cleaned, raw mic, sample where `near` starts)."""
        eng = AudioEngine(config.OUT_SR, aec=True, noise_suppression=True, barge_in=False)
        eng.full_duplex = True
        n_blocks = int((lead_s + len(near) / SR + tail_s) / (B / SR)) + 1
        far16 = np.concatenate([resample_poly(far24.astype(np.float32), 2, 3)] * 3)[: n_blocks * B]
        gain = ratio * (near_rms or rms(near[np.abs(near) > 50])) / rms(far16[np.abs(far16) > 200])
        echo = np.concatenate([np.zeros(int(delay_s * SR), dtype=np.float32), far16])[: n_blocks * B] * gain
        start = int(lead_s * SR)
        mic = echo.copy()
        mic[start: start + len(near)] += near.astype(np.float32)
        mic = np.clip(mic, -32768, 32767).astype(np.int16)
        far = np.concatenate([far24] * 4)
        clean = np.zeros(n_blocks * B, dtype=np.int16)
        for b in range(n_blocks):
            for k in range(8):
                a = (b * 8 + k) * 240
                eng._render_q.put(far[a: a + 240].copy())
            clean[b * B:(b + 1) * B] = eng._clean(mic[b * B:(b + 1) * B])
        return clean, mic, start

    print("building the simulated room cases...", flush=True)
    pre = int(0.16 * SR)  # the 2-frame pre-roll barge-in keeps before the first confident voice frame
    cases = {"user": {i: v[1] for i, v in user.items()}, "genuine_quiet": {}, "genuine_room": {}, "other_room": {},
             "other_quiet": {}, "agent_only": {}}
    for i, (raw, clean, lead) in user.items():
        onset = max(lead * B - pre, 0)
        cases["genuine_quiet"][i] = (clean, onset)
        c, m, start = room(raw)
        cases["genuine_room"][i] = (c, start + onset)
    for name, clips in other.items():
        for k, clip in enumerate(clips[:3]):
            scaled = (clip.astype(np.float32) * (350.0 / rms(clip[np.abs(clip) > 200]))).astype(np.int16)  # about as loud as you
            cases["other_quiet"][(name, k)] = (scaled, 0)
            if name != "jarvis(ryan)":
                c, m, start = room(scaled, near_rms=350.0)
                cases["other_room"][(name, k)] = (c, start)
    c, m, start = room(np.zeros(8 * SR, dtype=np.int16), near_rms=350.0)
    cases["agent_only"] = {"cleaned": (c, start), "raw echo": (m, start)}
    ev.CASES.parent.mkdir(exist_ok=True)
    with open(ev.CASES, "wb") as f:
        pickle.dump(cases, f)
    return cases


if __name__ == "__main__":
    cases = ev.load_cases() if ev.CASES.exists() and "--rebuild" not in sys.argv else build_cases()
    import sherpa_onnx

    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = []
    for name, path in MODELS.items():
        if only and not any(o.lower() in name.lower() for o in only):
            continue
        ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(ROOT / path), num_threads=1, debug=False, provider="cpu"))

        def embed(pcm, ex=ex):
            s = ex.create_stream()
            s.accept_waveform(sample_rate=SR, waveform=np.asarray(pcm, dtype=np.float32) / 32768.0)
            s.input_finished()
            e = np.array(ex.compute(s), dtype=np.float32)
            return e / (np.linalg.norm(e) + 1e-9)

        r, _ = ev.report(name, embed, cases)
        rows += r
    ev.summary(rows)
    sys.stdout.flush()
    os._exit(0)
