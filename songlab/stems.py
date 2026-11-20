"""パート分離。Demucs が使えれば Demucs、だめなら HPSS と周波数帯で概算する。"""

import os
import shutil
import subprocess
import sys

import librosa
import numpy as np
import scipy.signal as sps
import soundfile as sf

from . import HOP, SR

STEMS = ["vocals", "drums", "bass", "other"]


def demucs_available():
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def run_demucs(wav_path, out_dir, log=print):
    """Demucs（htdemucs）で4パートに分ける。成功したら各パートの wav パスを返す。"""
    if not demucs_available():
        return None, "Demucs が入っていません（pip install -r requirements-demucs.txt で入れられます）"
    name = os.path.splitext(os.path.basename(wav_path))[0]
    target = os.path.join(out_dir, "htdemucs", name)
    paths = {s: os.path.join(target, f"{s}.wav") for s in STEMS}
    if all(os.path.exists(p) for p in paths.values()):
        log("  （前回分離した結果を使います）")
        return paths, None
    cmd = [sys.executable, "-m", "demucs", "-n", "htdemucs", "-o", out_dir, "--jobs", "2", wav_path]
    log("  Demucs で分離中（CPUだと曲の長さの1〜2倍くらい時間がかかります）…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not all(os.path.exists(p) for p in paths.values()):
        err = (r.stderr or r.stdout or "").strip().splitlines()
        tail = " / ".join(err[-3:])[:300]
        if "fbaipublicfiles" in tail or "403" in tail or "URLError" in tail:
            tail += "（モデルのダウンロード先 dl.fbaipublicfiles.com がネットワーク設定で許可されていない可能性があります）"
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        return None, f"Demucs が失敗しました: {tail}"
    return paths, None


def load_stems_demucs(paths, sr=SR):
    out = {}
    for s, p in paths.items():
        y, _ = librosa.load(p, sr=sr, mono=True)
        out[s] = y
    return out


def _filter(y, sr, low=None, high=None, order=6):
    nyq = sr / 2
    if low and high:
        sos = sps.butter(order, [low / nyq, high / nyq], btype="band", output="sos")
    elif low:
        sos = sps.butter(order, low / nyq, btype="high", output="sos")
    else:
        sos = sps.butter(order, high / nyq, btype="low", output="sos")
    return sps.sosfiltfilt(sos, y)


def approximate_stems(y, sr=SR):
    """Demucs の代わりの概算。
    drums  = HPSS の打楽器成分
    bass   = 音程成分の 150Hz 以下
    vocals = 音程成分のうち、くり返しの少ない（前に出ている）成分の 200〜4000Hz
    other  = 残り
    """
    D = librosa.stft(y, n_fft=2048, hop_length=HOP)
    H, P = librosa.decompose.hpss(D, margin=(1.0, 2.0))
    harm = librosa.istft(H, hop_length=HOP, length=len(y))
    drums = librosa.istft(P, hop_length=HOP, length=len(y))
    bass = _filter(harm, sr, high=150)
    rest = harm - bass
    # くり返し成分（伴奏）と前景（歌）に分ける: librosa のボーカル分離の例と同じ方法
    S_full, phase = librosa.magphase(librosa.stft(rest, n_fft=2048, hop_length=HOP))
    width = int(librosa.time_to_frames(2, sr=sr, hop_length=HOP))
    S_filter = librosa.decompose.nn_filter(S_full, aggregate=np.median, metric="cosine",
                                           width=min(width, max(1, S_full.shape[1] // 4)))
    S_filter = np.minimum(S_full, S_filter)
    mask_v = librosa.util.softmask(S_full - S_filter, 10 * S_filter, power=2)
    voc = librosa.istft(mask_v * S_full * phase, hop_length=HOP, length=len(y))
    voc = _filter(voc, sr, low=200, high=4000)
    other = rest - voc
    return {"vocals": voc, "drums": drums, "bass": bass, "other": other}


def separate(wav_path, y, work_dir, use_demucs=True, log=print):
    if use_demucs:
        paths, err = run_demucs(wav_path, os.path.join(work_dir, "demucs"), log=log)
        if paths:
            return load_stems_demucs(paths), {"method": "demucs", "approximate": False, "note": None}
        note = err
    else:
        note = "--no-demucs が指定されました"
    log(f"  {note} → HPSS と周波数帯で概算します")
    stems = approximate_stems(y)
    for s, v in stems.items():
        sf.write(os.path.join(work_dir, f"approx_{s}.wav"), v, SR)
    return stems, {"method": "approx", "approximate": True, "note": note}


_WEIGHTED = {}


def _weighted_power(stems, sr=SR):
    """人の耳の聞こえ方（B特性: 低音を少し控えめに数える）で重みをつけた、フレームごとのパワー。"""
    key = id(stems)
    if key not in _WEIGHTED:
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        w = librosa.db_to_power(librosa.frequency_weighting(freqs, kind="B"))[:, None]
        _WEIGHTED[key] = {k: np.sum(np.abs(librosa.stft(v, n_fft=2048, hop_length=HOP)) ** 2 * w, axis=0)
                          for k, v in stems.items()}
    return _WEIGHTED[key]


def section_balance(stems, sections, sr=SR):
    """セクションごとの各パートの音量の比率（%）。耳の聞こえ方に合わせて重みをつけている。"""
    P = _weighted_power(stems, sr)
    out = []
    for s in sections:
        a, b = int(s["start"] * sr / HOP), int(s["end"] * sr / HOP)
        e = {k: float(np.mean(v[a:b])) if b > a else 0.0 for k, v in P.items()}
        tot = sum(e.values()) + 1e-12
        out.append({k: round(100 * e[k] / tot, 1) for k in STEMS})
    return out


def stem_curves(stems, sr=SR):
    """1秒ごとの各パートの音量（dB）。グラフ用。"""
    out = {}
    for k, v in stems.items():
        n = int(np.ceil(len(v) / sr))
        out[k] = np.array([10 * np.log10(np.mean(v[i * sr:(i + 1) * sr] ** 2) + 1e-10) for i in range(n)])
    return out
