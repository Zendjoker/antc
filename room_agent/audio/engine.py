"""
Full-duplex audio for the room agent.

  speaker  <- play() buffer -> output callback --(copy of exactly what was played)--> echo reference
  mic      -> input callback -> processing thread:
                 WebRTC APM (echo cancellation + noise suppression + high-pass), fed the reference
                 Silero VAD (is this a human voice?)
                 echo delay estimate (how long the agent's own voice takes to come back, e.g. Bluetooth)
                 barge-in detection (you talking over the agent)
              -> mic_q as (cleaned int16 frame, voice probability)

Barge-in needs strong evidence, because a voice-like sound while the agent talks is usually the
agent's own echo (with a loud speaker, its echo can be louder than you are):
  1. candidate frames: confident voice that survived echo cancellation, loud enough
  2. confirmation: several candidate frames close together (~250 ms), not one spike
  3. verification (when a verifier is set): transcribe the snippet and reject it if it's the agent's
     own words, a Whisper hallucination or noise. Without a verifier, a level check against the
     expected leftover echo is used instead.
Only then is playback cancelled. Each episode logs one line: candidate -> confirmed / rejected.

Without livekit installed it falls back to plain capture and the agent ignores the mic while it
talks (half duplex), which is still loop-safe.
"""

import collections
import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from room_agent.audio import debug

log = logging.getLogger("room-agent")

MIC_SR = 16000
FRAME = 1280  # 80 ms: what openWakeWord and the rest of the pipeline consume
APM_IN = MIC_SR // 100  # WebRTC processes 10 ms frames
FRAME_S = FRAME / MIC_SR


def _rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) + 1e-6


class EchoDelay:
    """Estimates how long the agent's own audio takes to come back into the mic (sound card + Bluetooth +
    room), by correlating the loudness envelope of what was played with what the mic heard."""

    STEP = 0.01  # envelope resolution: 10 ms
    MAX_LAG = 100  # look up to 1 s back
    WINDOW = 400  # correlate over the last 4 s

    def __init__(self, initial=0.45):
        self.ref = collections.deque(maxlen=self.WINDOW + self.MAX_LAG)
        self.mic = collections.deque(maxlen=self.WINDOW)
        self.estimates = collections.deque(maxlen=5)
        self.delay = initial  # seconds; conservative until measured (a longer guess only waits longer)
        self.measured = False
        self._since = 0

    def add_ref(self, level):
        self.ref.append(np.log10(max(level, 0.0) + 1.0))

    def add_mic(self, level):
        self.mic.append(np.log10(max(level, 0.0) + 1.0))
        self._since += 1
        if self._since >= 100:  # re-estimate about once a second
            self._since = 0
            self._estimate()

    def _estimate(self):
        if len(self.mic) < self.WINDOW or len(self.ref) < self.WINDOW + self.MAX_LAG:
            return
        ref = np.array(self.ref)
        mic = np.array(self.mic)
        if np.mean(ref[-self.WINDOW:] > 2.0) < 0.2:  # the agent barely played anything: nothing to learn
            return
        # ref[-1] and mic[-1] are "now" on their own clocks; echo of ref[k] shows up in mic `lag` steps later
        best, best_lag = 0.0, None
        m = (mic - mic.mean()) / (mic.std() + 1e-9)
        for lag in range(0, self.MAX_LAG):
            r = ref[len(ref) - self.WINDOW - lag : len(ref) - lag]
            r = (r - r.mean()) / (r.std() + 1e-9)
            c = float(np.mean(m * r))
            if c > best:
                best, best_lag = c, lag
        if best_lag is not None and best > 0.5:
            self.estimates.append(best_lag * self.STEP)
            new = float(np.median(self.estimates))
            if not self.measured or abs(new - self.delay) > 0.06:  # ignore +-40 ms wobble between two peaks
                log.info("echo delay: %d ms%s", new * 1000, "" if self.measured else " (measured)")
                self.delay, self.measured = new, True

    def ref_level_at_echo(self):
        """Loudness of what was played `delay` ago (+-100 ms): what the mic should be hearing as echo now."""
        if not self.ref:
            return 0.0
        lag = int(self.delay / self.STEP)
        ref = list(self.ref)
        lo, hi = max(0, len(ref) - lag - 10), max(0, len(ref) - lag + 10)
        seg = ref[lo:hi]
        return float(10 ** max(seg) - 1) if seg else 0.0


class AudioEngine:
    def __init__(self, out_sr, aec=True, noise_suppression=True, barge_in=True, speech_rms=500.0,
                 barge_ms=240, barge_vad=0.6, duck_gain=1.0):
        self.out_sr = out_sr
        # While a possible interruption is being judged the agent's volume drops to duck_gain (1.0 = never). The echo canceller
        # gates out about half of your voice when the agent is 4x louder than you; at 15% volume it keeps nearly all of it.
        self.duck_gain, self._duck_target, self._duck_now = duck_gain, 1.0, 1.0
        self.speech_rms = speech_rms
        self.barge_in = barge_in
        self.barge_frames = max(2, round(barge_ms / 80))  # this many voice frames...
        self.barge_window = self.barge_frames + 1  # ...within this many recent frames = confirmation
        self.barge_vad = barge_vad
        self.barge_vad_eff, self.barge_rms_factor, self.barge_gap = barge_vad, 0.4, 3  # (see set_sensitive)
        self.barge_win_eff, self.barge_min_frames = self.barge_frames + 1, max(self.barge_frames, 4)
        self.barge_tries = 2
        # verifier(pcm16k) -> (is_user: bool, detail: str). Set by the app (e.g. a Whisper check).
        self.barge_verifier = None

        self.mic_q: "queue.Queue[tuple[np.ndarray, float]]" = queue.Queue()
        self.interrupted = threading.Event()  # set when you talk over the agent
        self.BARGE = object()  # marker put in mic_q where your interruption starts
        self.barge_preroll = 0  # frames after the marker recorded while the agent was still audible
        self.voice_heard_at = 0.0  # last time something that might be you got through while it talked
        self.user_voice_at = 0.0  # last time a voice was heard while the agent was silent
        self.capturing = False  # the mic is open (False in text mode)
        self.epoch = 0  # bumped when the app takes the mic audio; late barge-in verdicts from before are dropped

        self.delay = EchoDelay()
        self._buf = bytearray()  # PCM waiting to be played
        self._lock = threading.Lock()
        self._q_lock = threading.RLock()  # keeps mic_q in order when a barge-in inserts audio (re-entrant: verify -> confirm)
        self._raw_q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._render_q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._render_left = np.zeros(0, dtype=np.int16)
        # The echo canceller copes with a few hundred ms of delay but fails beyond ~500 ms (tested: 74 dB of echo
        # removed at 400 ms, 3 dB at 600 ms). So the reference is pre-delayed by the measured delay minus a margin,
        # leaving the canceller only a short delay to find, whatever the Bluetooth latency does.
        self._align = collections.deque()  # 10 ms reference frames waiting to be fed
        self._align_frames = 0
        self._last_play = 0.0
        self._armed = False  # one interruption per stretch of speech
        self._history = collections.deque(maxlen=40)  # last 3.2 s of (frame number, item) while playing
        self._rejections = collections.deque(maxlen=10)  # times of "likely echo" rejections
        self._streams = []

        self.apm = None
        if aec or noise_suppression:
            try:
                from livekit import rtc

                self._rtc = rtc
                self.apm = rtc.AudioProcessingModule(
                    echo_cancellation=aec, noise_suppression=noise_suppression, high_pass_filter=True
                )
            except ImportError:
                log.warning("livekit not installed: no echo cancellation, falling back to half duplex")
        self.full_duplex = self.apm is not None and aec

        from openwakeword.utils import download_models
        from openwakeword.vad import VAD

        download_models(model_names=["silero_vad"])  # fetches only the shared feature + VAD models
        self.vad = VAD()

    # ---------- playback ----------
    def set_sensitive(self, on, vad=0.45, rms_factor=0.2, gap=5):
        """With a speaker check behind it, look for your voice more eagerly: in double talk the echo canceller leaves only
        ~20-45% of your level and breaks your speech into short pieces, so the default thresholds miss most interruptions."""
        self.barge_vad_eff, self.barge_rms_factor, self.barge_gap = (min(vad, self.barge_vad), rms_factor, gap) if on else (
            self.barge_vad, 0.4, 3)
        # your speech arrives in pieces: count candidate frames over a longer window, and judge sooner (3 frames)
        self.barge_win_eff = self.barge_frames + 3 if on else self.barge_frames + 1
        self.barge_min_frames = max(self.barge_frames, 3 if on else 4)
        self.barge_tries = 3 if on else 2  # looks at the growing snippet before giving up (the speaker check may say "unsure")

    # ---------- playback ----------
    def start(self, capture=True):
        out_block = self.out_sr // 100  # 10 ms
        self.full_duplex = self.full_duplex and capture
        self.capturing = capture
        self._streams = [
            sd.OutputStream(samplerate=self.out_sr, channels=1, dtype="int16", blocksize=out_block,
                            callback=self._on_play, latency="low"),
        ]
        if capture:
            self._streams.append(
                sd.InputStream(samplerate=MIC_SR, channels=1, dtype="int16", blocksize=FRAME,
                               callback=self._on_mic, latency="low")
            )
            threading.Thread(target=self._process_loop, daemon=True).start()
        for s in self._streams:
            s.start()
        log.info("audio: %s, noise suppression %s",
                 "echo cancellation on (you can interrupt)" if self.full_duplex else "half duplex",
                 "on" if self.apm else "off")

    def play(self, pcm: bytes):
        with self._lock:
            self._buf += pcm
            if pcm:
                self._armed = True  # new speech: interrupting it is possible again

    def queued_seconds(self):
        with self._lock:
            return len(self._buf) / 2 / self.out_sr

    def flush(self):
        """Stop talking right now."""
        with self._lock:
            self._buf.clear()
            self._duck_target = self._duck_now = 1.0  # the next thing it says starts at full volume

    def duck(self, on):
        self._duck_target = self.duck_gain if on else 1.0

    def echo_tail(self):
        """How long after the last sample is handed to the sound card its echo can still reach the mic."""
        return max(0.25, self.delay.delay + 0.2)

    def is_playing(self):
        """True while the agent is audible in the room, including the echo still on its way back."""
        with self._lock:
            busy = len(self._buf) > 0
        return busy or time.time() - self._last_play < self.echo_tail()

    def wait_drained(self):
        while self.is_playing() and not self.interrupted.is_set():
            time.sleep(0.02)

    def _on_play(self, outdata, frames, t, status):
        n = frames * 2
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
        if chunk:
            self._last_play = time.time()
        if len(chunk) < n:
            chunk += b"\x00" * (n - len(chunk))
        out = np.frombuffer(chunk, dtype=np.int16)
        if self._duck_now != self._duck_target:  # fall fast (~30 ms), come back gently (~100 ms), per 10 ms callback
            step = 0.4 if self._duck_target < self._duck_now else 0.1
            self._duck_now = (max(self._duck_target, self._duck_now - step) if self._duck_target < self._duck_now
                              else min(self._duck_target, self._duck_now + step))
        if self._duck_now != 1.0:
            out = (out.astype(np.float32) * self._duck_now).astype(np.int16)  # (the echo reference gets the ducked audio too)
        outdata[:, 0] = out
        if self.full_duplex:
            self._render_q.put(out.copy())  # exactly what the speaker is playing

    # ---------- capture ----------
    def _on_mic(self, indata, frames, t, status):
        self._raw_q.put(indata[:, 0].copy())

    def _feed_render(self):
        """Hand everything the speaker played since last time to the echo canceller, in 10 ms frames,
        before the mic audio it could have echoed into gets processed."""
        parts = [self._render_left]
        while True:
            try:
                parts.append(self._render_q.get_nowait())
            except queue.Empty:
                break
        audio = np.concatenate(parts)
        step = self.out_sr // 100
        n = len(audio) // step * step
        if self.delay.measured:
            target = max(0, int((self.delay.delay - 0.15) / EchoDelay.STEP))
            if abs(target - self._align_frames) >= 5:  # only re-align on a real change (>= 50 ms)
                self._align_frames = target
        for i in range(0, n, step):
            frame = audio[i : i + step]
            self.delay.add_ref(_rms(frame))  # the delay estimate always uses the true, un-delayed reference
            self._align.append(frame)
        while len(self._align) > self._align_frames:
            frame = self._align.popleft()
            self.apm.process_reverse_stream(self._rtc.AudioFrame(frame.tobytes(), self.out_sr, 1, step))
        self._render_left = audio[n:]

    def _clean(self, block):
        if not self.apm:
            return block
        if self.full_duplex:
            self._feed_render()
        out = np.empty_like(block)
        for i in range(0, len(block), APM_IN):
            raw = block[i : i + APM_IN]
            if self.full_duplex:
                self.delay.add_mic(_rms(raw))
            f = self._rtc.AudioFrame(raw.tobytes(), MIC_SR, 1, APM_IN)
            self.apm.process_stream(f)
            out[i : i + APM_IN] = np.frombuffer(bytes(f.data), dtype=np.int16)
        return out

    def _process_loop(self):
        noise = 30.0  # the mic's own background level after cleaning
        leftover = collections.deque(maxlen=75)  # leftover echo as a fraction of playback (fallback check)
        recent = collections.deque(maxlen=12)
        history = self._history
        episode = None  # a stretch of voice-like sound during playback being judged
        run_start, last_voice = None, -100  # first frame of the current run of voice (episodes < 1 s apart)
        cooldown_until = 0  # frame number: after an echo rejection, give the canceller a moment
        last_hint = 0.0
        frame_no = 0
        idle_voice = 0  # consecutive voiced frames while the agent is silent
        while True:
            raw = self._raw_q.get()
            try:
                clean = self._clean(raw)
            except Exception as e:
                log.error("audio processing error: %s", e)
                clean = raw
            voice = float(self.vad.predict(clean, frame_size=640))
            item = (clean, voice)
            frame_no += 1
            playing = self.full_duplex and self.barge_in and self.is_playing()
            debug.block(raw, clean, voice, self.is_playing())
            if self.duck_gain < 1.0:  # back to full volume once an attempt is over (rejected, confirmed or given up)
                self.duck(bool(playing and episode is not None and not episode["done"]))
            with self._q_lock:  # mic_q and the barge-in history stay in step
                self.mic_q.put(item)
                if playing:
                    history.append((frame_no, item))

            if not self.is_playing() and voice >= 0.7 and _rms(clean) >= self.speech_rms:
                idle_voice += 1
                if idle_voice >= 3:  # ~240 ms of voice
                    self.user_voice_at = time.time()
            else:
                idle_voice = 0

            if not (self.full_duplex and self.barge_in):
                continue
            c, r = _rms(clean), _rms(raw)
            if not playing and voice < 0.3:
                noise = 0.95 * noise + 0.05 * c
            if not self._armed or not playing or self.interrupted.is_set():
                if episode and not episode["done"]:
                    # (not a rejection: voice_heard_at stays, so the app can still check what was said)
                    episode["done"] = True
                    log.info("barge-in undecided: the agent stopped talking first")
                recent.clear()
                with self._q_lock:
                    history.clear()
                episode, run_start = None, None
                continue

            ref = self.delay.ref_level_at_echo()
            candidate = voice >= self.barge_vad_eff and c >= self.speech_rms * self.barge_rms_factor and c / r > 0.15
            if not candidate and c / r < 0.2 and ref > 1000:
                leftover.append(max(c - noise, 0.0) / ref)  # clearly cancelled echo: learn its level
            if frame_no < cooldown_until:
                candidate = False
            recent.append(candidate)

            if candidate:
                if episode is None or episode["done"]:
                    if run_start is None or frame_no - last_voice > 12:
                        run_start = frame_no  # a new run of voice (not a continuation of the last one)
                    episode = {"start": frame_no, "run": run_start, "frames": 0, "quiet": 0, "done": False,
                               "pending": False, "tries": 0, "epoch": self.epoch, "heard_before": self.voice_heard_at}
                    log.info("barge-in candidate (voice %.2f, mic %.0f, cleaned %.0f)", voice, r, c)
                    self.duck(self.duck_gain < 1.0)
                    debug.event("barge_candidate", frame=frame_no, run_start=run_start, voice=voice, mic_rms=r, cleaned_rms=c)
                episode["frames"] += 1
                episode["quiet"] = 0
                last_voice = frame_no
                self.voice_heard_at = time.time()
            elif episode and not episode["done"]:
                episode["quiet"] += 1
                if episode["quiet"] >= self.barge_gap and not episode["pending"]:  # 240 ms without a voice: it's over
                    self._end_episode(episode, "too short")
                    episode = None
                    recent.clear()
                continue

            if (not episode or episode["done"] or episode["pending"]
                    or sum(list(recent)[-self.barge_win_eff:]) < self.barge_frames
                    or episode["frames"] < self.barge_min_frames * (1 + episode["tries"])):
                continue
            # Sustained voice. Now: is it you, or the agent's own echo? (Judge the whole run of voice.)
            with self._q_lock:
                audio = np.concatenate([f for n, (f, _) in history if n >= episode["run"] - 2])
            if self.barge_verifier:
                episode["pending"] = True
                episode["tries"] += 1
                threading.Thread(target=self._verify, args=(episode, audio), daemon=True).start()
            else:
                expected = noise + (np.percentile(leftover, 90) if len(leftover) >= 10 else 1.0) * ref
                if c > 2.5 * expected:
                    self._confirm(episode, "louder than any echo")
                else:
                    self._end_episode(episode, f"likely echo (cleaned {c:.0f} vs expected echo {expected:.0f})")
            if episode["done"]:
                recent.clear()
            if self._rejections and time.time() - self._rejections[-1] < 0.2:
                cooldown_until = frame_no + 5  # 0.4 s after an echo rejection
            if len([t for t in self._rejections if time.time() - t < 60]) >= 5 and time.time() - last_hint > 300:
                last_hint = time.time()
                log.warning("the agent's own voice keeps leaking into the mic: move the speaker further from "
                            "the mic or turn it down, so interrupting works reliably")

    def _verify(self, episode, audio):
        debug.ctx.why = "barge"
        t_start = time.time()
        try:
            is_user, detail = self.barge_verifier(audio)
        except Exception as e:
            is_user, detail = False, f"check failed: {e}"
        debug.event("barge_verify", try_no=episode["tries"], seconds=len(audio) / MIC_SR, is_user=is_user, detail=detail,
                    run_start=episode["run"], episode_frames=episode["frames"], took=time.time() - t_start,
                    audio=debug.wav("barge_segment", audio))
        with self._q_lock:  # decided atomically against settle/drain (which bump the epoch)
            episode["pending"] = False
            if episode["done"] or self.interrupted.is_set():
                return
            if episode["epoch"] != self.epoch or not self.is_playing():
                episode["done"] = True
                log.info("barge-in undecided: verdict came after the agent stopped (%s)", detail)
                return
            if is_user:
                self._confirm(episode, detail)
            elif detail.startswith("noise") and episode["tries"] < self.barge_tries:
                pass  # too little to tell yet: checked again once more of it has been heard
            else:
                self._end_episode(episode, detail)

    def _confirm(self, episode, detail):
        with self._q_lock:
            episode["done"] = True
            log.info("barge-in confirmed: user speech (%s)", detail)
            # Your audio from just before the run of voice started goes in first, behind a marker; only then is
            # the interruption flagged, so a reader never sees the flag without the marker.
            kept = [it for n, it in list(self._history) if n >= episode["run"] - 2]
            debug.event("barge_confirmed", detail=detail, kept_frames=len(kept), preroll_frames=2,
                        run_start=episode["run"], history_first=self._history[0][0] if self._history else None,
                        audio=debug.wav("barge_kept", np.concatenate([f for f, _ in kept]) if kept else None))
            self.barge_preroll = int(np.ceil(self.echo_tail() / FRAME_S))  # the agent's last audio still echoing
            self.mic_q.put(self.BARGE)
            for it in kept:
                self.mic_q.put(it)
            self._armed = False
            self.flush()
            self.interrupted.set()
        log.info("TTS cancelled")

    def _end_episode(self, episode, reason):
        if episode["done"]:
            return
        episode["done"] = True
        log.info("barge-in rejected: %s", reason)
        debug.event("barge_rejected", reason=reason, episode_frames=episode["frames"])
        if reason.startswith("likely echo"):
            self._rejections.append(time.time())
            self.voice_heard_at = episode["heard_before"]  # it was the agent, not you

    # ---------- reading the mic ----------
    def keep_recent_mic(self, seconds):
        """Keep only the last `seconds` of buffered mic audio (instead of throwing it all away)."""
        with self._q_lock:
            self.epoch += 1
            items = []
            while True:
                try:
                    item = self.mic_q.get_nowait()
                except queue.Empty:
                    break
                if item is not self.BARGE:
                    items.append(item)
            kept = items[-int(seconds / FRAME_S):]
            for item in kept:
                self.mic_q.put(item)
        return kept  # the (audio, voice) frames now waiting in the queue

    def drain_mic(self):
        """Throw away buffered mic audio. If you interrupted, keep everything from the interruption on."""
        with self._q_lock:
            self.epoch += 1
            kept = []
            while True:
                try:
                    item = self.mic_q.get_nowait()
                except queue.Empty:
                    break
                if item is self.BARGE:
                    kept = []
                    continue
                kept.append(item)
            if self.interrupted.is_set():
                for item in kept:
                    self.mic_q.put(item)

    def close(self):
        for s in self._streams:
            s.stop()
            s.close()
