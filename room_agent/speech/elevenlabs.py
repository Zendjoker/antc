"""ElevenLabs rendering: SpeechPerformance -> the performance script and request for THIS model.

Model capabilities differ and are kept here, per model (ElevenLabs docs, models overview / changelog 2026-09-28 / v4 help
center / request stitching guide, and the official elevenlabs-python SDK v2.71, checked 2026-10-10):
    eleven_v4_turbo, eleven_v4, eleven_v3, eleven_v3_conversational
        audio tags in square brackets, placed before the words they shape ([curious], [excited], [sighs], [laughs],
        descriptive ones like [warm, conversational tone]); NO SSML at all (no <break>): pacing comes from punctuation and
        text structure, and exact silences from our own scheduler between sentences (speaker.py). v4 / v4 Turbo take only
        stability and similarity_boost (no speed, no style); v3 doesn't do request stitching (previous_text). The
        documented real-time path for v4 Turbo is the Text to Dialogue WebSocket (speech/dialogue_ws.py), used when the
        HTTP stream refuses the model (or ELEVENLABS_TRANSPORT=dialogue).
    eleven_flash_v2_5, eleven_turbo_v2_5 (deprecated), eleven_multilingual_v2, eleven_flash_v2
        no audio tags (a bracket would be read out); SSML <break time="x.xs" /> works (up to 3 s; too many make the voice
        unstable: one per request at most here); previous_text for continuity. Flash / Turbo v2.5 don't normalize
        numbers unless asked ("on" is Enterprise-only), so money and percentages are written out before they're sent.
Unknown models get the conservative treatment (plain text, no settings beyond speed, our own normalization).

The script is presentation metadata: it's built right before the request, sent, and dropped. Memory, history, the UI and
the logs keep the semantic text (SPEECH_DEBUG=1 shows both, for development only). validate() checks every script: the
same words in the same order, only allowed tags (a fixed list: anything else from anywhere is refused), no shouting, no
ellipsis spam, no laughter in a serious moment, nothing internal. If anything's off, the plain semantic text is sent.
"""

import logging
import re

from room_agent.speech.director import SpeechPerformance

log = logging.getLogger("room-agent")

V4_SETTINGS = ("stability", "similarity_boost")
ALL_SETTINGS = ("stability", "similarity_boost", "style", "use_speaker_boost", "speed")
CAPS = {
    "eleven_v4_turbo": {"tags": True, "ssml": False, "realtime": "text-to-dialogue websocket", "continuity": "tags",
                        "settings": V4_SETTINGS, "normalize": "light", "dialogue": True},
    "eleven_v4": {"tags": True, "ssml": False, "realtime": None, "continuity": "tags", "settings": V4_SETTINGS,
                  "normalize": "light", "dialogue": True},
    "eleven_v3": {"tags": True, "ssml": False, "realtime": None, "continuity": "tags", "settings": ALL_SETTINGS,
                  "normalize": "light", "dialogue": True},
    "eleven_v3_conversational": {"tags": True, "ssml": False, "realtime": "text-to-dialogue websocket", "continuity": "tags",
                                 "settings": ALL_SETTINGS, "normalize": "light", "dialogue": True},
    "eleven_flash_v2_5": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text",
                          "settings": ALL_SETTINGS, "normalize": "numbers"},
    "eleven_turbo_v2_5": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text",
                          "settings": ALL_SETTINGS, "normalize": "numbers"},
    "eleven_flash_v2": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text",
                        "settings": ALL_SETTINGS, "normalize": "numbers"},
    "eleven_multilingual_v2": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text",
                               "no_language_code": True, "settings": ALL_SETTINGS, "normalize": "light"},
}
CONSERVATIVE = {"tags": False, "ssml": False, "realtime": None, "continuity": "previous_text", "settings": ("speed",),
                "normalize": "numbers"}
TAG = re.compile(r"\[([^\[\]]{1,60})\]")
TAG_OK = re.compile(r"^[a-z][a-z ,'-]{1,58}$")
BREAK = re.compile(r"<break time=\"(\d(?:\.\d)?)s\" />")
REACTIONS = {"chuckles", "laughs", "laughs softly", "sighs", "gasps", "whispers", "clears throat"}
# every word a tag may contain: what the director and the chosen styles use, plus the documented v3/v4 tags. Anything
# else (a tag the model made up, a bracket from outside content) never reaches the voice as a direction.
ALLOWED_WORDS = {
    "calm", "clear", "firm", "quiet", "warm", "warmly", "sincere", "direct", "matter-of-fact", "serious", "tone",
    "thoughtful", "excited", "genuinely", "surprised", "curious", "lightly", "amused", "playful", "teasing", "upbeat",
    "engaged", "interested", "lively", "confident", "softly", "gently", "friendly", "whispers", "conversational",
    "chuckles", "laughs", "softly", "sighs", "exhales", "gasps", "clears", "throat", "mischievously", "sarcastic",
}
INTERNAL = re.compile(r"\b(strategy|direction|semantic|performance|socialstate|tool_|runtime|system note)\b", re.I)


def caps(model):
    return CAPS.get(model, CONSERVATIVE)


def _words(text):
    """The words of a text, without tags, breaks, case or punctuation (for 'nothing semantic changed' checks)."""
    return re.findall(r"[a-z0-9']+", BREAK.sub(" ", TAG.sub(" ", str(text))).lower().replace("’", "'"))


_OPENING = re.compile(r"^(\W*)(yeah|man|hmm+|well|oh|so|wait|no way|honestly|okay|alright)(,)\s+", re.I)


def render(p: SpeechPerformance, model, repeat_direction=True):
    """-> the performance script for `model` (plain semantic text when the model can't take directions)."""
    text = p.semantic_text.strip()
    c = caps(model)
    trails = (p.pauses == "thoughtful" or p.purpose == "surprise") and not p.protect
    if not c["tags"]:
        # SSML models: one short break after a thoughtful opening ("Hmm, <break> ..."), never with numbers around
        if c["ssml"] and trails and p.pauses == "thoughtful":
            text = _OPENING.sub(lambda m: f'{m.group(1)}{m.group(2)}, <break time="0.3s" /> ', text, count=1)
        return text
    # pacing through punctuation, sparingly: an opening interjection may trail off; nothing added when urgent
    if trails:
        text = _OPENING.sub(r"\1\2... ", text, count=1)
    if p.emphasis and p.purpose == "surprise" and not p.protect:
        text = re.sub(rf"\b({re.escape(p.emphasis)})\b", lambda m: m.group(1).upper(), text, count=1, flags=re.I)
    tags = []
    if p.reaction:
        tags.append(f"[{p.reaction}]")
    if p.direction and (p.changed or repeat_direction):
        tags.append(f"[{p.direction_text}]")
    return (" ".join(tags) + " " + text).strip()


def validate(p: SpeechPerformance, script, model):
    """-> list of problems ([] = fine to send)."""
    problems = []
    c = caps(model)
    tags = TAG.findall(script)
    if tags and not c["tags"]:
        problems.append("tags for a model that would read them out")
    breaks = BREAK.findall(script)
    if "<" in BREAK.sub("", script) or (breaks and not c["ssml"]) or len(breaks) > 1 or any(float(b) > 1.0 for b in breaks):
        problems.append("markup this model can't take")
    for t in tags:
        if any(w not in ALLOWED_WORDS for w in re.findall(r"[a-z-]+", t.lower())):
            problems.append(f"tag [{t}] isn't on the allowed list")
    if _words(script) != _words(p.semantic_text):
        problems.append("the words changed")
    if len(tags) > 2:
        problems.append("too many tags")
    for t in tags:
        if not TAG_OK.match(t) or len(t.split(",")) > 3:
            problems.append(f"odd tag [{t}]")
        if t.strip() in REACTIONS and (p.seriousness != "low" or p.purpose in ("apology", "failure", "warning")):
            problems.append(f"[{t}] doesn't fit a serious moment")
    caps_words = [w for w in re.findall(r"\b[A-Z]{2,}\b", TAG.sub("", script)) if w not in re.findall(r"\b[A-Z]{2,}\b",
                                                                                                         p.semantic_text)]
    if len(caps_words) > 1:
        problems.append("too much shouting (capitals)")
    if TAG.sub("", script).count("...") > p.semantic_text.count("...") + 1:
        problems.append("too many ellipses")
    if INTERNAL.search(" ".join(tags)):
        problems.append("internal words in a tag")
    return problems


def script_for(p: SpeechPerformance, model, repeat_direction=True):
    """Render + validate. -> (script, problems). If anything's wrong: the plain semantic text, neutral delivery."""
    script = render(p, model, repeat_direction)
    problems = validate(p, script, model)
    if problems:
        log.info("speech: performance script rejected (%s): plain delivery", "; ".join(problems))
        return p.semantic_text, problems
    return script, []


def voice_settings(base_rate, p, profile=None, model=None):
    """voice_settings for the request: speed from their rate and this performance; the rest only from a tested profile
    (speech_profiles in settings.json), never invented: unset fields keep the voice's own saved settings. Only what
    this model takes (v4: stability and similarity only, so their speaking rate can't be applied there)."""
    out = dict(profile or {})
    base = float(out.get("speed") or 1.0)  # (the profile's speed is the baseline their rate and this reply adjust)
    speed = round(max(0.7, min(1.2, base * base_rate * (p.pace if p else 1.0))), 3)
    if speed != 1.0 or "speed" in out:
        out["speed"] = speed
    if model is not None:
        allowed = caps(model)["settings"]
        out = {k: v for k, v in out.items() if k in allowed}
    return out or None


def request_body(text, p, model, base_rate=1.0, previous_text="", language="", normalization="auto", seed=None,
                 profile=None, dictionaries=None):
    """The JSON body for /v1/text-to-speech/{voice}/stream, with only what this model takes."""
    body = {"text": text, "model_id": model}
    vs = voice_settings(base_rate, p, profile, model)
    if vs:
        body["voice_settings"] = vs
    c = caps(model)
    if previous_text and c["continuity"] == "previous_text":
        body["previous_text"] = previous_text[-500:]  # (prosody carries over from the sentence before)
    if language and language != "en" and not c.get("no_language_code"):
        body["language_code"] = language
    if normalization in ("auto", "on", "off"):
        body["apply_text_normalization"] = normalization
    if seed is not None:
        body["seed"] = int(seed)
    if dictionaries:
        body["pronunciation_dictionary_locators"] = dictionaries[:3]
    return body
