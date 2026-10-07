"""Evaluation of one speaker-embedding model on the cached test cases (tests/speaker_bench.py builds them).
Pure numpy so it also runs in a separate environment (e.g. SpeechBrain ECAPA): see tests/speaker_ecapa.py."""
import itertools
import pathlib
import pickle
import time

import numpy as np

SR = 16000
LENGTHS = [0.5, 1.0, 2.0]
CASES = pathlib.Path(__file__).resolve().parents[1] / "debug" / "speaker_cases.pkl"


def load_cases():
    with open(CASES, "rb") as f:
        return pickle.load(f)


def snippets(src, L, how):
    n = int(L * SR)
    out = []
    for key, (audio, start) in src.items():
        if how == "onset":
            out.append(audio[start: start + n])
        else:  # windows of the echo-only stream, after the canceller has converged
            out += [audio[a: a + n] for a in range(int(start), len(audio) - n, int(1.1 * SR))]
    return [s for s in out if len(s) >= n * 0.9]


def centroid(embed, clips, win=3 * SR, hop=int(1.5 * SR)):
    embs = [embed(c[a: a + win]) for c in clips for a in range(0, max(len(c) - win, 0) + 1, hop)]
    m = np.mean(embs, axis=0)
    return m / np.linalg.norm(m)


def auc(pos, neg):
    return float(np.mean([p > n for p in pos for n in neg]) + 0.5 * np.mean([p == n for p in pos for n in neg]))


def eer(pos, neg):
    best = 1.0
    for t in np.unique(np.concatenate([pos, neg])):
        best = min(best, max(np.mean(np.array(pos) < t), np.mean(np.array(neg) >= t)))
    return best


def scores(embed, cases):
    """-> {L: {"you quiet": [...], "you in room": [...], "other voices, quiet": [...], ...}} (cosine to your enrolled voice).
    Enrolled on one half of your speech and tested on the other half, both ways."""
    items = sorted(cases["user"])
    folds = [items[0::2], items[1::2]]
    out = {L: {k: [] for k in ("you quiet", "you in room", "other voices, quiet", "other voices + echo", "agent echo only",
                               "agent voice, no echo")} for L in LENGTHS}
    for fold in range(2):
        cen = centroid(embed, [cases["user"][i] for i in folds[fold]])
        for L in LENGTHS:
            for i in folds[1 - fold]:
                for s in snippets({i: cases["genuine_quiet"][i]}, L, "onset"):
                    out[L]["you quiet"].append(float(embed(s) @ cen))
                for s in snippets({i: cases["genuine_room"][i]}, L, "onset"):
                    out[L]["you in room"].append(float(embed(s) @ cen))
            for key, v in cases["other_quiet"].items():
                for s in snippets({key: v}, L, "onset"):
                    out[L]["agent voice, no echo" if key[0] == "jarvis(ryan)" else "other voices, quiet"].append(float(embed(s) @ cen))
            for key, v in cases["other_room"].items():
                for s in snippets({key: v}, L, "onset"):
                    out[L]["other voices + echo"].append(float(embed(s) @ cen))
            for name in ("cleaned", "raw echo"):
                for s in snippets({name: cases["agent_only"][name]}, L, "stream"):
                    out[L]["agent echo only"].append(float(embed(s) @ cen))
    return out


def report(name, embed, cases):
    sc = scores(embed, cases)
    rows = []
    lat = {}
    for L in LENGTHS:
        x = cases["user"][sorted(cases["user"])[0]][: int(L * SR)]
        embed(x)
        ts = []
        for _ in range(30):
            a = time.perf_counter()
            embed(x)
            ts.append(time.perf_counter() - a)
        lat[L] = float(np.median(ts)) * 1000
    print(f"\n=== {name}")
    for L in LENGTHS:
        s = sc[L]
        you = s["you quiet"] + s["you in room"]
        non = list(itertools.chain.from_iterable(v for k, v in s.items() if not k.startswith("you")))
        thr = max(non) + 0.005
        print(f" snippet {L:.1f}s: {lat[L]:5.1f} ms | you quiet mean {np.mean(s['you quiet']):.2f} min {min(s['you quiet']):.2f} | "
              f"you in room mean {np.mean(s['you in room']):.2f} min {min(s['you in room']):.2f}")
        print("            not you: " + ", ".join(f"{k} mean {np.mean(v):.2f} max {max(v):.2f}" for k, v in s.items()
                                                  if not k.startswith("you") and v))
        print(f"            AUC {auc(you, non):.3f}  EER {eer(you, non) * 100:.1f}%  zero-false-accept threshold {thr:.2f} keeps "
              f"{np.mean(np.array(you) >= thr) * 100:.0f}% of you ({np.mean(np.array(s['you in room']) >= thr) * 100:.0f}% in the room)")
        rows.append((name, L, auc(you, non), eer(you, non), thr, float(np.mean(np.array(you) >= thr)), lat[L]))
    return rows, sc


def summary(rows):
    print("\nSUMMARY")
    for r in rows:
        print(f" {r[0]:26} {r[1]:.1f}s  AUC {r[2]:.3f}  EER {r[3] * 100:5.1f}%  zero-false-accept threshold {r[4]:.2f} keeps {r[5] * 100:3.0f}%  {r[6]:.1f} ms")
