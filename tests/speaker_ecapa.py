"""SpeechBrain ECAPA-TDNN on the same test cases (run with the throwaway PyTorch environment, not the project's):
%TEMP%\\ecapa_venv\\Scripts\\python tests\\speaker_ecapa.py"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
os.chdir(ROOT)

import numpy as np  # noqa: E402
import torch  # noqa: E402
from speechbrain.inference.speaker import EncoderClassifier  # noqa: E402
from speechbrain.utils.fetching import LocalStrategy  # noqa: E402

import speaker_eval as ev  # noqa: E402

torch.set_num_threads(1)
clf = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(ROOT / "models" / "speaker" / "ecapa"),
                                     run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)


def embed(pcm):
    x = torch.from_numpy(np.asarray(pcm, dtype=np.float32) / 32768.0)[None]
    with torch.no_grad():
        e = clf.encode_batch(x)[0, 0].numpy()
    return e / (np.linalg.norm(e) + 1e-9)


rows, _ = ev.report("SpeechBrain ECAPA-TDNN", embed, ev.load_cases())
ev.summary(rows)
