"""ElevenLabs rendering: SpeechPerformance -> the performance script and request for THIS model.

Model capabilities differ and are kept here, per model (from the ElevenLabs docs, Oct 2026):
    eleven_v4_turbo, eleven_v4, eleven_v3, eleven_v3_conversational
        natural-language audio tags ([calm, direct], [genuinely surprised], [chuckles]...), NO SSML breaks: pacing comes
        from tags, punctuation and text structure. Real-time v4 Turbo is served by the Text to Dialogue WebSocket.
    eleven_flash_v2_5, eleven_turbo_v2_5, eleven_multilingual_v2, eleven_flash_v2
        no audio tags (a bracket would be read out). Plain text; delivery only through speed.
Unknown models get the conservative treatment (plain text).

The script is presentation metadata: it's built right before the request, sent, and dropped. Memory, history, the UI and
the logs keep the semantic text (SPEECH_DEBUG=1 shows both, for development only). validate() checks every script: the
same words in the same order, a few clean tags, no shouting, no ellipsis spam, no laughter in a serious moment, nothing
internal. If anything's off, the plain semantic text is sent instead.
"""

import logging
import re

from room_agent.speech.director import SpeechPerformance

log = logging.getLogger("room-agent")

CAPS = {
    "eleven_v4_turbo": {"tags": True, "ssml": False, "realtime": "text-to-dialogue websocket", "continuity": "tags"},
    "eleven_v4": {"tags": True, "ssml": False, "realtime": None, "continuity": "tags"},
    "eleven_v3": {"tags": True, "ssml": False, "realtime": None, "continuity": "tags"},
    "eleven_v3_conversational": {"tags": True, "ssml": False, "realtime": "text-to-dialogue websocket", "continuity": "tags"},
    "eleven_flash_v2_5": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text"},
    "eleven_turbo_v2_5": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text"},
    "eleven_flash_v2": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text"},
    "eleven_multilingual_v2": {"tags": False, "ssml": True, "realtime": "tts websocket", "continuity": "previous_text",
                               "no_language_code": True},
}
CONSERVATIVE = {"tags": False, "ssml": False, "realtime": None, "continuity": "previous_text"}
TAG = re.compile(r"\[([^\[\]]{1,60})\]")
TAG_OK = re.compile(r"^[a-z][a-z ,'-]{1,58}$")
REACTIONS = {"chuckles", "laughs", "laughs softly", "sighs", "gasps", "whispers", "clears throat"}
INTERNAL = re.compile(r"\b(strategy|direction|semantic|performance|socialstate|tool_|runtime|system note)\b", re.I)


def caps(model):
    return CAPS.get(model, CONSERVATIVE)


def _words(text):
    """The words of a text, without tags, case or punctuation (for 'nothing semantic changed' checks)."""
    return re.findall(r"[a-z0-9']+", TAG.sub(" ", str(text)).lower().replace("’", "'"))


def render(p: SpeechPerformance, model, repeat_direction=True):
    """-> the performance script for `model` (plain semantic text when the model can't take directions)."""
    text = p.semantic_text.strip()
    c = caps(model)
    if not c["tags"]:
        return text
    # pacing through punctuation, sparingly: an opening interjection may trail off; nothing added when urgent
    if p.pauses == "thoughtful" or p.purpose == "surprise":
        text = re.sub(r"^(\W*)(yeah|man|hmm|well|oh|so|wait|no way|honestly|okay|alright)(,)\s+", r"\1\2... ", text, count=1,
                      flags=re.I)
    if p.emphasis and p.purpose == "surprise":
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


def voice_settings(base_rate, p, profile=None):
    """voice_settings for the request: speed from their rate and this performance; the rest only from a tested profile
    (speech_profiles in settings.json), never invented: unset fields keep the voice's own saved settings."""
    out = dict(profile or {})
    base = float(out.get("speed") or 1.0)  # (the profile's speed is the baseline their rate and this reply adjust)
    speed = round(max(0.7, min(1.2, base * base_rate * (p.pace if p else 1.0))), 3)
    if speed != 1.0 or "speed" in out:
        out["speed"] = speed
    return out or None


def request_body(text, p, model, base_rate=1.0, previous_text="", language="", normalization="auto", seed=None,
                 profile=None, dictionaries=None):
    """The JSON body for /v1/text-to-speech/{voice}/stream, with only what this model takes."""
    body = {"text": text, "model_id": model}
    vs = voice_settings(base_rate, p, profile)
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
