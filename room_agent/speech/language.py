"""Which language a sentence is in (cheap, no model): kept as metadata so speech uses the same language as the semantic
text (ElevenLabs language_code where the model takes it; local voices are English-only and say so in the log).
Never translates."""

import re

STOP = {
    "en": "the and you is are was it that this what with for have not just",
    "fr": "le la les et est je tu vous pas une des que qui pour avec mais c'est de du ça tout suis dans sur ce cette il elle "
          "on nous oui non très",
    "es": "el la los las y es que de no por para una con pero está lo ahora mismo eso esto hay muy aquí qué cómo yo",
    "de": "der die das und ist ich du nicht ein eine mit für auch",
    "it": "il lo la gli e è che di non per una con ma sono",
    "pt": "o a os as e é que de não para uma com mas você",
    "nl": "de het een en is ik je niet met voor maar",
}
STOP = {k: set(v.split()) for k, v in STOP.items()}


def detect(text, default="en"):
    t = str(text or "")
    if re.search(r"[؀-ۿ]", t):
        return "ar"
    if re.search(r"[぀-ヿ]", t):
        return "ja"
    if re.search(r"[一-鿿]", t):
        return "zh"
    if re.search(r"[Ѐ-ӿ]", t):
        return "ru"
    words = re.findall(r"[a-zà-ÿ']+", t.lower())
    if len(words) < 3:
        return default
    scores = {lang: sum(w in s for w in words) for lang, s in STOP.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 and scores[best] > scores.get(default, 0) else default
