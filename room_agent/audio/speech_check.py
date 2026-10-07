"""Is this speech the agent's own voice leaking into the mic, or filler noise? Decided from the words alone."""

import difflib
import logging
import re

from room_agent import runtime as rt

log = logging.getLogger("room-agent")

# Spelling variants speech recognition uses for the same sound ("Lemme see" is heard as "let me see")
_CANON = {"lemme": "let me", "gonna": "going to", "wanna": "want to", "gotta": "got to", "kinda": "kind of",
          "yup": "yeah", "yep": "yeah", "yea": "yeah", "ya": "yeah", "yes": "yeah", "ok": "okay", "k": "okay",
          "mmhm": "mhm", "mmhmm": "mhm", "mhmm": "mhm", "uhhuh": "mhm", "hmm": "hm", "hmmm": "hm", "mm": "hm",
          "im": "i'm", "its": "it's", "thats": "that's", "whats": "what's", "dont": "don't", "cant": "can't"}
# What Whisper "hears" in silence, breathing, noise or music, and sounds that carry no meaning on their own
FILLER_WORDS = {"you", "so", "uh", "um", "hm", "mhm", "oh", "ah", "huh", "the", "i", "a", "and", "okay", "yeah", "bye",
                "music", "applause", "silence", "thank", "thanks", "for", "watching", "subscribe"}
# Words too common to tell anyone apart (but "no", "stop", "wait" mean something when you cut in)
STOP_WORDS = set("""a an the i i'm me my you you're your we our it it's its is are was were be been am do does did to
of in on at for with about and or but so that that's this what what's who how when where why can could would will
just like really yeah okay mhm hm oh uh um well right then there here have has had not don't can't
let going got get""".split())


def norm_words(text):
    text = re.sub(r"[\[(*][^\])*]*[\])*]", " ", str(text).lower()).replace("-", "")  # "[music]", "(laughs)"
    out = []
    for w in re.findall(r"[a-z0-9']+", text):
        out += _CANON.get(w, w).split()
    return out


def _similar(a, b):
    """Same word, allowing recognition errors and clipped words ("chica" vs "chicago")."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return len(a) >= 4 and len(b) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def agent_said():
    return norm_words(" ".join(list(rt.turn_speech) + list(rt.recent_speech)[-6:]))


def own_speech_scores(words):
    """(word overlap, word-pair overlap) between these words and what the agent said recently, each 0..1."""
    said = agent_said()
    if not said or not words:
        return 0.0, 0.0
    said_set = set(said)

    def has(w):
        return w in said_set or any(_similar(w, x) for x in said_set)

    content = [w for w in words if w not in STOP_WORDS]
    word_ratio = sum(has(w) for w in content) / len(content) if content else 0.0
    said_pairs = set(zip(said, said[1:]))
    pairs = list(zip(words, words[1:]))
    pair_ratio = sum(p in said_pairs for p in pairs) / len(pairs) if pairs else 0.0
    return word_ratio, pair_ratio


def is_own_speech(words):
    """Do these words come from what the agent is saying / just said (its echo), rather than from you?"""
    word_ratio, pair_ratio = own_speech_scores(words)
    return word_ratio >= 0.6 or pair_ratio >= 0.5


def is_filler(text):
    words = norm_words(text)
    return not words or all(w in FILLER_WORDS for w in words)


# One-word answers ("yes", "okay", "mhm") are real replies. They share FILLER_WORDS only because a barge-in shouldn't fire
# on a backchannel; as a whole utterance they count when recognition was confident. (Silence hallucinations are
# "Thank you." / "you" / "Bye.", so those words never qualify.)
ANSWER_WORDS = {"yeah", "okay", "mhm"}
HALLUCINATION_WORDS = {"you", "thank", "thanks", "for", "watching", "subscribe", "music", "applause", "silence", "bye"}


def confident(conf=None):
    """Was speech recognition sure about the last utterance? (Whisper: token log-probability and its no-speech estimate;
    Deepgram: its confidence score.) The audio already passed the VAD, so this is the second piece of evidence."""
    conf = rt.stt_confidence if conf is None else conf
    if not conf:
        return False
    if "score" in conf:
        return conf["score"] >= 0.7
    return conf["logprob"] >= -0.8 and conf["no_speech"] <= 0.5


def is_short_answer(text, conf=None):
    words = norm_words(text)
    return (bool(words) and any(w in ANSWER_WORDS for w in words) and not any(w in HALLUCINATION_WORDS for w in words)
            and confident(conf))


USER, AGENT_ECHO, NOISE, UNCERTAIN = "USER", "AGENT_ECHO", "NOISE", "UNCERTAIN"


def classify_audio(text, speech_started=None):
    """Who produced this audio? -> (label, detail). Timing against the agent's own audio decides first, the words
    only second, and anything doubtful while the agent is silent is treated as you.
      NOISE       no real words (silence hallucinations, breathing, filler sounds)
      USER        you started after the agent's audio (and its echo's trip back) had ended, or the words differ
      AGENT_ECHO  it overlapped the agent's audio AND repeats what it said
      UNCERTAIN   it overlapped and partly resembles what it said; counts as you unless the agent is still talking"""
    if is_filler(text) and not is_short_answer(text):
        unsure = is_short_answer(text, conf={"score": 1})  # the right words, but recognition wasn't sure it heard them
        return NOISE, "short answer, but recognition was unsure" if unsure else "no real words"
    heard = norm_words(text)
    engine = rt.engine
    if engine is not None and is_filler(text):  # a confident "yes" / "okay": unless it's the agent's own word echoing back
        age = None if speech_started is None else speech_started - rt.tts_end
        if age is not None and age <= engine.delay.delay + 0.15 and set(heard) & set(agent_said()):
            return AGENT_ECHO, "short word the agent just said, heard while its audio was still arriving"
        return USER, "short answer (recognition confident)"
    if engine is None or len(heard) < 4:
        return USER, "too short to resemble the agent, or no live audio"
    echo_s = engine.delay.delay + 0.15  # how long after its last sound the agent's echo can still arrive
    age = None if speech_started is None else speech_started - rt.tts_end  # < 0: it started while the agent was talking
    if age is not None and age > echo_s:
        return USER, f"started {age * 1000:.0f}ms after the agent stopped (its echo needs {echo_s * 1000:.0f}ms)"
    word_ratio, pair_ratio = own_speech_scores(heard)
    similarity = max(word_ratio, pair_ratio)
    detail = (f"similarity={similarity:.2f}, tts_age={'unknown' if age is None else f'{age * 1000:.0f}ms'}, "
              f"echo={engine.delay.delay * 1000:.0f}ms")
    during = age is None or age < 0  # it started while the agent was talking (or we can't tell when)
    # Over live playback, shared words are evidence. Right after it stopped, you may simply be answering with its own
    # words, so only a copied word SEQUENCE counts.
    if (word_ratio >= 0.6 or pair_ratio >= 0.5) if during and not rt.ringing else pair_ratio >= 0.75:
        return AGENT_ECHO, detail
    if similarity >= 0.4:
        return UNCERTAIN, detail
    return USER, detail


def sounds_like_own_reply(text, speech_started=None):
    return classify_audio(text, speech_started)[0] == AGENT_ECHO
