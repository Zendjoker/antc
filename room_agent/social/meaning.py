"""What an utterance MEANS for how to answer it, read by similarity instead of by matching words.

A small local sentence-embedding model (all-MiniLM-L6-v2, ONNX int8, ~23 MB, CPU, a few ms a sentence) places what they
said next to a few short DESCRIPTIONS of each situation below, relative to ordinary requests. Nothing is looked up, so
phrasing nobody wrote down still lands where its meaning is ("I'm running on fumes" sits next to "I have no energy left").
The descriptions are definitions of the situations, fixed and few: they are not where a missed sentence gets added.

Supporting evidence for SocialState like every other source: the words themselves (signals.py), what just happened, and
the voice (prosody.py) still count, and a single source stays below the strongest readings. English only (other
languages: this source is absent). Loaded in the background at startup; until it's ready, or if it can't be (no model
and no internet, SOCIAL_MEANING=0), this source is simply absent and nothing waits for it.
"""

import logging
import os
import re
import threading

import numpy as np

log = logging.getLogger("room-agent")

REPO = os.getenv("SOCIAL_MEANING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
MODEL_FILES = ("onnx/model_quint8_avx2.onnx", "onnx/model.onnx")
ENABLED = os.getenv("SOCIAL_MEANING", "1") == "1"

# situation -> (short descriptions of it, SocialState evidence it gives at full strength)
SITUATIONS = {
    "low": (["I am tired.", "I have no energy left.", "I'm exhausted and need rest.", "I feel down and sad."],
            {"low": 0.6, "energy_down": 0.6}),
    "urgent": (["This is an emergency, I need help right now.", "There is a fire, smoke or a gas smell.",  # (kinds of hazard,
                "Water is leaking or flooding everywhere.", "Someone is hurt, choking or unconscious.",  # not phrasings)
                "Something important is failing right now and getting worse."],
               {"urgent": 0.6, "brief": 0.4, "serious": 0.3}),
    "serious": (["I got very bad news.", "Someone I love is ill or has died.", "I'm going through something painful.",
                 "I'm scared and worried."],
                {"serious": 0.6}),
    "excited": (["I'm so happy, it worked!", "I just got great news!", "I succeeded!"],
                {"excited": 0.55, "positive": 0.55, "energy_up": 0.45}),
    "frustrated": (["It isn't working again and I'm annoyed.", "This keeps failing.", "I'm fed up with this."],
                   {"frustration": 0.55}),
}
NEUTRAL = ["What time is it?", "Open the app.", "Set a timer.", "What's the weather?", "Play some music.",
           "Tell me about this.", "Remind me later.", "Send a message.", "Add it to my list.", "How does this work?"]
MARGIN = 0.10   # how much closer to a situation than to an ordinary request before it counts at all
FULL = 0.25     # margin at which it counts at full strength
SECOND = 0.04   # a runner-up this close means the reading is ambiguous: halved
FLOOR = 0.75    # strength just past MARGIN: a clear reading alone is enough to shape HOW Jarvis answers (still a
                # single source: SocialState's confidence stays below what two agreeing sources give)

# time: urgency is about NOW. Tense and time words are supporting evidence for any wording (closed word classes,
# not phrases): a plan ("this weekend", "tomorrow", "I need to ... later") isn't an emergency; "right now", "just",
# "!", "what do I do" press.
FUTURE = re.compile(r"\b(tomorrow|tonight|later|this (weekend|week|month|evening|afternoon)|next (week|month|year|time)|"
                    r"someday|eventually|soon|at some point|need to|have to|going to|gonna|plan(ning)? to|want to)\b", re.I)
# urgency is a problem + pressure to act NOW: something going wrong said with "quick", "hurry", "right now" is urgent,
# whatever is going wrong (a server, a pipe, a pan)
PRESSURE = re.compile(r"^\W*(quick(ly)?|hurry( up)?|help|asap|urgent(ly)?)\b|\b(right now|immediately|asap|right away)\b", re.I)
# polarity: embeddings place "I failed the exam" next to "I passed the exam" (same topic). Good news needs no failure or
# negation in it; when it has some, there's no positive reading at all (never upbeat about bad news).
FAILED = re.compile(r"\b(not|no|never|nothing|didn'?t|don'?t|doesn'?t|couldn'?t|can'?t|won'?t|wasn'?t|isn'?t|without|"
                    r"fail(ed|ing|s)?|los[et]|losing|miss(ed)?|reject\w*|denied|fired|laid off|dumped|cancel+ed|ruin\w*|"
                    r"broke|died|dead|worse|worst)\b", re.I)
NOW = re.compile(r"\b(now|right now|just|currently|still|immediately|already)\b|!|\bhelp\b|\bwhat (do|should) i do\b|"
                 r"\bhow do i stop\b|\b(is|are|'s|'re) \w+ing\b", re.I)

_m = {"session": None, "tok": None, "anchors": None, "neutral": None, "failed": False}
_lock = threading.Lock()


def _file(name):
    """The cached copy (no network), or download it the first time."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from huggingface_hub import hf_hub_download

    try:
        return hf_hub_download(REPO, name, local_files_only=True)
    except Exception:
        return hf_hub_download(REPO, name)


def _load():
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(_file("tokenizer.json"))
    tok.enable_truncation(128)
    tok.enable_padding()
    session = None
    for name in MODEL_FILES:  # (the int8 model needs AVX2; the full one runs anywhere)
        try:
            session = ort.InferenceSession(_file(name), providers=["CPUExecutionProvider"])
            _m.update(session=session, tok=tok)
            embed(["warm up"])
            break
        except Exception as e:
            log.debug("meaning model %s unusable: %s", name, e)
            session = None
    if session is None:
        raise RuntimeError("no usable model")
    _m["anchors"] = {k: embed(d) for k, (d, _) in SITUATIONS.items()}
    _m["neutral"] = embed(NEUTRAL)


def load(wait=True):
    """Load the model (downloads it once, ~23 MB). wait=False: in the background."""
    def run():
        with _lock:
            if _m["anchors"] is not None or _m["failed"] or not ENABLED:
                return
            try:
                _load()
                log.info("meaning reader ready")
            except Exception as e:
                _m["failed"] = True
                log.warning("meaning reader unavailable (%s): reading moods from words and voice only", e)
    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True, name="meaning-load").start()


def ready():
    return _m["anchors"] is not None


def embed(texts):
    """-> unit vectors, one row per text."""
    enc = _m["tok"].encode_batch([str(t) for t in texts])
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    feeds = {"input_ids": ids, "attention_mask": mask}
    if any(i.name == "token_type_ids" for i in _m["session"].get_inputs()):
        feeds["token_type_ids"] = np.zeros_like(ids)
    hidden = _m["session"].run(None, feeds)[0]
    m = mask[..., None].astype(np.float32)
    v = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def reading(text):
    """-> (situation or None, strength 0..1, {situation: margin}) for one utterance."""
    if not ready() or not str(text or "").strip():
        return None, 0.0, {}
    v = embed([text])[0]
    base = float(np.max(_m["neutral"] @ v))
    margins = {k: float(np.max(a @ v)) - base for k, a in _m["anchors"].items()}
    ranked = sorted(margins.items(), key=lambda kv: -kv[1])
    best, margin = ranked[0]
    if margin < MARGIN:
        return None, 0.0, margins
    strength = min(1.0, FLOOR + (margin - MARGIN) / (FULL - MARGIN) * (1 - FLOOR))
    if len(ranked) > 1 and margin - ranked[1][1] < SECOND:
        strength *= 0.6
    if best == "excited" and FAILED.search(str(text)):
        return None, 0.0, margins
    if best == "frustrated" and PRESSURE.search(str(text)):
        best = "urgent"
    if best == "urgent":
        t = str(text)
        strength *= 0.4 if FUTURE.search(t) and not NOW.search(t) else 1.0 if NOW.search(t) else 0.8
    return best, round(strength, 2), margins


def evidence(text, now, lang="en"):
    """SocialState evidence from what the utterance means (empty when the reader isn't ready or it's not English)."""
    from room_agent.social.signals import Evidence

    if lang != "en":
        return []
    if not ready():
        load(wait=False)  # (normally already loaded at startup; this turn goes without it)
        return []
    try:
        situation, strength, _ = reading(text)
    except Exception as e:  # (never in the way of the conversation)
        log.debug("meaning: %s", e)
        return []
    if not situation:
        return []
    _, effects = SITUATIONS[situation]
    clears = {"low": ("excited", "energy_up"), "excited": ("low", "frustration", "energy_down"),
              "urgent": ("joking",), "serious": ("joking",)}.get(situation, ())
    return [Evidence(dim, round(w * strength, 2), "meaning", now, f"means: {situation}", clears) for dim, w in effects.items()]
