"""テンポ・拍子・キー・コード・構成・エネルギーの推定。"""

import difflib

import librosa
import numpy as np

from . import HOP, SR

PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler のキー・プロファイル
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# コードの型: (表記, 構成音の半音, 重み, ペナルティ)
CHORD_TYPES = [
    ("", (0, 4, 7), (1.0, 1.0, 1.0), 0.0),
    ("m", (0, 3, 7), (1.0, 1.0, 1.0), 0.0),
    ("7", (0, 4, 7, 10), (1.0, 1.0, 1.0, 0.8), 0.04),
    ("m7", (0, 3, 7, 10), (1.0, 1.0, 1.0, 0.8), 0.04),
    ("maj7", (0, 4, 7, 11), (1.0, 1.0, 1.0, 0.8), 0.05),
]


def _confidence_label(x):
    if x >= 0.7:
        return "高い"
    if x >= 0.4:
        return "中くらい"
    return "低い"


# ------------------------------------------------------------------ テンポと拍

def tempo_and_beats(y, sr=SR):
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, aggregate=np.median)
    tg = librosa.feature.tempogram(onset_envelope=onset, sr=sr, hop_length=HOP)
    ac = tg.mean(axis=1)
    bpms = librosa.tempo_frequencies(tg.shape[0], sr=sr, hop_length=HOP)
    valid = (bpms >= 50) & (bpms <= 220)
    # 人の曲として自然な 80〜170 BPM を少しだけ優先する
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpms, 1e-6) / 125.0) / 0.9) ** 2)
    score = np.where(valid, ac * prior, 0)
    peaks = [i for i in range(1, len(score) - 1)
             if score[i] > 0 and score[i] >= score[i - 1] and score[i] >= score[i + 1]]
    peaks.sort(key=lambda i: score[i], reverse=True)
    if not peaks:
        raise RuntimeError("テンポを推定できませんでした")
    best = peaks[0]
    coarse_bpm = float(bpms[best])

    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=sr, hop_length=HOP, start_bpm=coarse_bpm, tightness=120)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)
    if len(beat_times) >= 8:
        bpm = 60.0 / np.median(np.diff(beat_times))
    else:
        bpm = float(np.atleast_1d(tempo)[0])
    beat_times = _extend_beats(beat_times, 60.0 / bpm, len(y) / sr)

    # 確からしさ: 1位のピークが、倍・半分以外のライバルよりどれだけ強いか
    rivals = [score[i] for i in peaks[1:]
              if abs(np.log2(bpms[i] / bpms[best])) > 0.15
              and abs(abs(np.log2(bpms[i] / bpms[best])) - 1) > 0.1]
    rival = rivals[0] if rivals else 0.0
    conf = float(np.clip((score[best] - rival) / (score[best] + 1e-9) * 1.6, 0, 1))
    # 拍の間隔のばらつきも考える
    if len(beat_times) >= 8:
        ibi = np.diff(beat_times)
        cv = float(np.std(ibi) / (np.mean(ibi) + 1e-9))
        conf *= float(np.clip(1.2 - cv * 6, 0.3, 1.0))

    alt = []
    for mult in (0.5, 2.0):
        b = bpm * mult
        if 50 <= b <= 240:
            alt.append(round(b, 1))
    return {
        "bpm": round(float(bpm), 1),
        "confidence": round(conf, 2),
        "confidence_label": _confidence_label(conf),
        "alternatives": alt,
        "beat_times": beat_times,
        "onset_env": onset,
    }


def _extend_beats(beats, period, duration):
    """ドラムのないイントロ・アウトロにも、同じ間隔で拍を足す。"""
    if len(beats) < 2:
        return np.arange(0, duration, period)
    head = np.arange(beats[0] - period, -period * 0.25, -period)[::-1]
    head = head[head >= 0]
    tail = np.arange(beats[-1] + period, duration - period * 0.25, period)
    return np.r_[head, beats, tail]


def _sync(X, frames, agg=np.mean):
    """各拍の「その拍から次の拍まで」の平均を取る（列 j = 拍 j の区間）。"""
    return librosa.util.sync(X, frames, aggregate=agg, pad=True)[:, 1:]


def meter_and_downbeats(y, beat_times, sr=SR):
    """拍子（4拍子か3拍子か）と、小節の頭の位置を推定する。"""
    if len(beat_times) < 16:
        return {"meter": 4, "confidence": 0.0, "confidence_label": "低い", "phase": 0}
    # 低音のアタックは小節の頭に来やすい
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[freqs < 200]), sr=sr, hop_length=HOP)
    chroma = librosa.feature.chroma_stft(S=S**2, sr=sr)
    bf = librosa.time_to_frames(beat_times, sr=sr, hop_length=HOP)
    bf = np.clip(bf, 0, len(low) - 1)
    acc = low[bf]
    acc = (acc - acc.mean()) / (acc.std() + 1e-9)
    # 和音が変わる場所も小節の頭になりやすい
    bc = _sync(chroma, bf)
    bc = bc / (np.linalg.norm(bc, axis=0, keepdims=True) + 1e-9)
    change = np.r_[0, 1 - np.sum(bc[:, 1:] * bc[:, :-1], axis=0)]
    change = change[: len(acc)]
    if len(change) < len(acc):
        change = np.r_[change, np.zeros(len(acc) - len(change))]
    change = (change - change.mean()) / (change.std() + 1e-9)
    sig = acc + change

    def phase_score(m):
        scores = [sig[p::m].mean() - sig.mean() for p in range(m)]
        return max(scores), int(np.argmax(scores))

    s4, p4 = phase_score(4)
    s3, p3 = phase_score(3)
    if s3 > s4 * 1.25 and s3 > 0.3:
        meter, phase, conf = 3, p3, (s3 - s4) / (abs(s3) + 1e-9)
    else:
        meter, phase, conf = 4, p4, (s4 - max(s3, 0)) / (abs(s4) + 1e-9)
    conf = float(np.clip(conf, 0, 1))
    return {"meter": meter, "confidence": round(conf, 2),
            "confidence_label": _confidence_label(conf), "phase": phase}


# ------------------------------------------------------------------ キー

def estimate_key(chroma_mean):
    c = chroma_mean / (chroma_mean.sum() + 1e-9)
    res = []
    for i in range(12):
        for mode, prof in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(c, np.roll(prof, i))[0, 1]
            res.append((float(r), i, mode))
    res.sort(reverse=True)
    top = res[:3]
    vals = np.array([r for r, _, _ in res])
    # 確からしさ: 1位と2位の差（相対調はほぼ同点になりやすい）
    gap = top[0][0] - top[1][0]
    conf = float(np.clip(gap / 0.15, 0, 1)) * float(np.clip((top[0][0] - 0.3) / 0.5, 0, 1))
    out = []
    for r, i, mode in top:
        name = PITCHES[i] + (" major（長調）" if mode == "major" else " minor（短調）")
        out.append({"key": PITCHES[i] + ("" if mode == "major" else "m"), "name": name,
                    "tonic": i, "mode": mode, "score": round(r, 3)})
    return {"candidates": out, "confidence": round(conf, 2),
            "confidence_label": _confidence_label(conf), "_all": vals}


def refine_relative_key(key, bars):
    """平行調（C major と A minor など）の取り違えを、コードの使われ方で見直す。
    小節の頭・区切りの最初と最後に来るコードは、その曲の中心（主音）になりやすい。"""
    cands = key["candidates"]
    top = cands[0]
    rel_tonic = (top["tonic"] + 9) % 12 if top["mode"] == "major" else (top["tonic"] + 3) % 12
    rel_mode = "minor" if top["mode"] == "major" else "major"

    def weight(tonic, mode):
        w = 0.0
        for i, b in enumerate(bars):
            c = b["chord"].split("/")[0].split(" ")[0]
            if c == "N":
                continue
            minor = c.endswith("m") or c.endswith("m7")
            root = c.rstrip("m7aj")
            if root not in PITCHES:
                continue
            if PITCHES.index(root) == tonic and minor == (mode == "minor"):
                w += 2.0 if i % 4 == 0 else 1.0     # 4小節のまとまりの頭は重く数える
        return w

    w_top, w_rel = weight(top["tonic"], top["mode"]), weight(rel_tonic, rel_mode)
    key["relative_check"] = {"top": round(w_top, 1), "relative": round(w_rel, 1)}
    if w_rel > w_top * 1.3 and w_rel >= 4:
        rel = next((c for c in cands if c["tonic"] == rel_tonic and c["mode"] == rel_mode), None)
        if rel is None:
            rel = {"key": PITCHES[rel_tonic] + ("m" if rel_mode == "minor" else ""),
                   "name": PITCHES[rel_tonic] + (" minor（短調）" if rel_mode == "minor" else " major（長調）"),
                   "tonic": rel_tonic, "mode": rel_mode, "score": top["score"]}
        key["candidates"] = [rel] + [c for c in cands if c is not rel][:2]
        key["note"] = "音の分布だけだと平行調と区別しにくいので、コードの使われ方で順位を入れ替えました"
        key["confidence"] = round(min(key["confidence"], 0.6), 2)
        key["confidence_label"] = _confidence_label(key["confidence"])
    return key


# ------------------------------------------------------------------ コード

def _templates():
    labels, roots, mats, pens, tones = [], [], [], [], []
    for root in range(12):
        for suffix, iv, w, pen in CHORD_TYPES:
            t = np.zeros(12)
            for k, ww in zip(iv, w):
                t[(root + k) % 12] = ww
            t /= np.linalg.norm(t)
            labels.append(PITCHES[root] + suffix)
            roots.append(root)
            mats.append(t)
            pens.append(pen)
            tones.append({(root + k) % 12 for k in iv})
    return labels, np.array(roots), np.array(mats), np.array(pens), tones


def chords_per_beat(y_harm, beat_times, sr=SR, bass_audio=None):
    """拍ごとのコードを推定する（HMM で滑らかにする）。"""
    labels, roots, T, pens, tones = _templates()
    bf = librosa.time_to_frames(beat_times, sr=sr, hop_length=HOP)
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=HOP, bins_per_octave=36,
                                        fmin=librosa.note_to_hz("C2"), n_octaves=6)
    n = chroma.shape[1]
    bf = np.clip(bf, 0, n - 1)
    bc = _sync(chroma, bf, np.median)
    energy = bc.sum(axis=0)
    bcn = bc / (np.linalg.norm(bc, axis=0, keepdims=True) + 1e-9)
    sim = T @ bcn - pens[:, None]                            # (コード数, 拍)
    # 無音・非和音
    silent = energy < 0.15 * np.median(energy)
    n_lab = len(labels)
    emis = np.vstack([sim, np.full((1, sim.shape[1]), 0.55)])
    emis[-1, silent] = 2.0
    prob = np.exp(emis * 25)
    prob /= prob.sum(axis=0, keepdims=True)
    trans = librosa.sequence.transition_loop(n_lab + 1, 0.8)
    path = librosa.sequence.viterbi(prob, trans)

    # ベース音（分数コードの判定用）
    src = bass_audio if bass_audio is not None else y_harm
    C = np.abs(librosa.cqt(src, sr=sr, hop_length=HOP, fmin=librosa.note_to_hz("E1"),
                           n_bins=36, bins_per_octave=12))
    C = _sync(C, np.clip(bf, 0, C.shape[1] - 1), np.median)
    bass_pc = np.zeros(C.shape[1], dtype=int)
    bass_strength = np.zeros(C.shape[1])
    for j in range(C.shape[1]):
        pc_energy = np.zeros(12)
        for b in range(36):
            pc_energy[(4 + b) % 12] += C[b, j]
        bass_pc[j] = int(np.argmax(pc_energy))
        bass_strength[j] = pc_energy.max() / (pc_energy.sum() + 1e-9)

    out = []
    sorted_sim = np.sort(sim, axis=0)
    for j in range(min(len(beat_times), sim.shape[1])):
        s = path[j]
        if s == n_lab:
            out.append({"t": float(beat_times[j]), "chord": "N", "conf": 0.0, "root": None})
            continue
        margin = float(sim[s, j] - sorted_sim[-2, j]) if sim[s, j] >= sorted_sim[-1, j] - 1e-9 \
            else float(sim[s, j] - sorted_sim[-1, j])
        fit = float(sim[s, j])
        conf = float(np.clip((fit - 0.6) / 0.3, 0, 1) * 0.6 + np.clip(margin / 0.08, 0, 1) * 0.4)
        name = labels[s]
        bp = bass_pc[j] if j < len(bass_pc) else None
        if bp is not None and bp != roots[s] and bass_strength[j] > 0.28 and bp in tones[s] | {
                (roots[s] + 10) % 12, (roots[s] + 11) % 12, (roots[s] + 2) % 12}:
            name = f"{name}/{PITCHES[bp]}"
        out.append({"t": float(beat_times[j]), "chord": name, "conf": round(conf, 2),
                    "root": int(roots[s])})
    return out


def chords_by_bar(beat_chords, meter, phase):
    """拍ごとのコードを小節単位にまとめる。"""
    bars = []
    i = phase
    # phase より前の拍は「弱起（小節の途中から始まる部分）」として最初の小節に入れる
    if phase > 0:
        bars.append(_summarize_bar(beat_chords[:phase], beat_chords[0]["t"]))
    while i < len(beat_chords):
        seg = beat_chords[i:i + meter]
        bars.append(_summarize_bar(seg, seg[0]["t"]))
        i += meter
    return bars


def _summarize_bar(seg, t0):
    names = [b["chord"] for b in seg]
    confs = [b["conf"] for b in seg]
    half = max(1, len(seg) // 2)
    first, second = names[:half], names[half:]

    def major(xs):
        return max(set(xs), key=xs.count) if xs else None

    a, b = major(first), major(second)
    if b and a != b and second.count(b) == len(second) and first.count(a) == len(first):
        label = f"{a} {b}"
    else:
        label = major(names)
    agree = names.count(major(names)) / len(names)
    conf = float(np.mean(confs)) * (0.5 + 0.5 * agree) if label != f"{a} {b}" else float(np.mean(confs))
    if label == "N":
        conf = 1.0
    sure = conf >= 0.45
    return {"t": round(float(t0), 2), "chord": label, "conf": round(conf, 2),
            "display": label if sure or label == "N" else label + "?"}


# ------------------------------------------------------------------ エネルギー

def energy_curve(y, sr=SR):
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)
    db = librosa.amplitude_to_db(rms + 1e-9, ref=1.0)
    # 1秒ごとに平均
    sec = np.arange(0, int(np.ceil(t[-1])) + 1)
    per_sec = np.array([db[(t >= s) & (t < s + 1)].mean() if np.any((t >= s) & (t < s + 1)) else db.min()
                        for s in sec])
    lo, hi = np.percentile(per_sec, 5), np.percentile(per_sec, 99)
    score = np.clip((per_sec - lo) / (hi - lo + 1e-9) * 100, 0, 100)
    return {"times": sec.astype(float), "db": per_sec, "score": score, "frame_db": db, "frame_t": t}


# ------------------------------------------------------------------ 構成

def bar_features(y, bars, duration, sr=SR):
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=14)[1:]
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    edges = [b["t"] for b in bars] + [duration]
    fr = librosa.time_to_frames(edges, sr=sr, hop_length=HOP)
    fr = np.clip(fr, 0, chroma.shape[1])
    feats, energies = [], []
    for a, b in zip(fr[:-1], fr[1:]):
        b = max(b, a + 1)
        c = chroma[:, a:b].mean(axis=1)
        m = mfcc[:, a:b].mean(axis=1)
        feats.append(np.r_[c / (np.linalg.norm(c) + 1e-9), m / 60.0])
        energies.append(float(librosa.amplitude_to_db(np.array([rms[a:b].mean() + 1e-9]))[0]))
    return np.array(feats), np.array(energies)


def segment_song(feats, energies, bars, duration):
    """小節の特徴の似かたから区切りを見つけ、くり返しのグループに分ける。"""
    n = len(feats)
    if n < 8:
        return [{"start_bar": 0, "end_bar": n, "group": "A"}]
    F = feats - feats.mean(axis=0)
    F = F / (np.linalg.norm(F, axis=1, keepdims=True) + 1e-9)
    S = F @ F.T
    def checker(L):
        k = np.ones((2 * L, 2 * L))
        k[:L, L:] = -1
        k[L:, :L] = -1
        w = np.hanning(2 * L + 2)[1:-1]
        k *= np.outer(w, w)
        Sp = np.pad(S, L, mode="edge")
        v = np.array([np.sum(Sp[i:i + 2 * L, i:i + 2 * L] * k) for i in range(n)])
        return np.minimum(v / (inner_max(np.abs(v)) + 1e-9), 2.0)

    def inner_max(v):
        # 大きな変化が1つあっても他の区切りが埋もれないよう、90パーセンタイルを基準にする
        mid = v[2:n - 2] if n > 8 else v
        ref = float(np.percentile(mid, 90)) if len(mid) else float(np.max(v))
        return ref if ref > 0 else float(np.max(v)) + 1e-9

    nov = checker(4) + 0.6 * checker(2)
    # 4小節のコードのくり返しが崩れた場所（Aメロ→Bメロ などで起きやすい）
    C = feats[:, :12]
    C = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)

    def rep(i):
        ks = [k for k in range(4) if 0 <= i + k - 4 and i + k < n]
        return float(np.mean([C[i + k] @ C[i + k - 4] for k in ks])) if ks else 0.0

    loop_break = np.array([max(0.0, rep(i - 4) - rep(i)) if i >= 8 else 0.0 for i in range(n)])
    nov = np.minimum(nov / (inner_max(np.abs(nov)) + 1e-9), 2.0) \
        + np.minimum(loop_break / (inner_max(loop_break) + 1e-9), 2.0)
    # エネルギーの急な変化も区切りの手がかりにする
    e = np.r_[energies[0], energies]
    jump = np.abs(np.diff(e))
    nov = nov + 0.5 * np.minimum(jump / (inner_max(jump) + 1e-9), 2.0)
    nov[0] = 0
    thr = np.mean(nov) + 0.25 * np.std(nov)
    bounds = [0]
    peak = [i for i in range(1, n) if nov[i] >= nov[i - 1] and (i == n - 1 or nov[i] >= nov[i + 1])]
    for i in sorted(peak, key=lambda i: nov[i], reverse=True):
        if nov[i] < thr:
            break
        if all(abs(i - b) >= 4 for b in bounds) and n - i >= 2:
            bounds.append(int(i))
    bounds = sorted(bounds)

    # 長すぎる区間は、8小節ごとの区切りの中で一番変化の大きい場所で割る
    refined = []
    for a, b in zip(bounds, bounds[1:] + [n]):
        refined.append(a)
        while b - a > 16:
            cands = list(range(a + 8, b - 3))
            cut = max(cands, key=lambda i: nov[i] + (0.3 if (i - a) % 4 == 0 else 0))
            refined.append(cut)
            a = cut
    bounds = sorted(set(refined))

    segs = [{"start_bar": a, "end_bar": b} for a, b in zip(bounds, bounds[1:] + [n])]
    # 区間どうしの似かた: 特徴の並び（ずらして比較）とコード列
    chords = [b["chord"] for b in bars]

    def seg_sim(s1, s2):
        a = F[s1["start_bar"]:s1["end_bar"]]
        b = F[s2["start_bar"]:s2["end_bar"]]
        m = min(len(a), len(b))
        if m == 0:
            return 0.0
        feat_sim = float(np.mean(np.sum(a[:m] * b[:m], axis=1)))
        ca = chords[s1["start_bar"]:s1["end_bar"]]
        cb = chords[s2["start_bar"]:s2["end_bar"]]
        chord_sim = difflib.SequenceMatcher(None, ca, cb).ratio()
        len_ratio = m / max(len(a), len(b))
        return (0.5 * feat_sim + 0.5 * chord_sim) * (0.7 + 0.3 * len_ratio)

    groups = []
    letters = "ABCDEFGHIJKLMNOP"
    for s in segs:
        best, best_sim = None, 0.0
        for gi, g in enumerate(groups):
            sim = max(seg_sim(s, segs[m]) for m in g)
            if sim > best_sim:
                best, best_sim = gi, sim
        if best is not None and best_sim >= 0.62:
            groups[best].append(segs.index(s))
            s["group"] = letters[best % len(letters)]
            s["group_sim"] = round(best_sim, 2)
        else:
            groups.append([segs.index(s)])
            s["group"] = letters[(len(groups) - 1) % len(letters)]
            s["group_sim"] = None
    return segs


def name_sections(segs, bars, energies, duration, vocal_ratio=None):
    """グループと音量から、イントロ / Aメロ / Bメロ / サビ などの名前を推定する。"""
    edges = [b["t"] for b in bars] + [duration]
    for s in segs:
        s["start"] = round(edges[s["start_bar"]], 2)
        s["end"] = round(edges[s["end_bar"]], 2)
        s["bars"] = s["end_bar"] - s["start_bar"]
        s["energy_db"] = float(np.mean(energies[s["start_bar"]:s["end_bar"]]))
        s["vocal"] = None if vocal_ratio is None else vocal_ratio(s["start"], s["end"])

    loud = max((s["vocal"] for s in segs if s["vocal"] is not None), default=None)

    def has_vocal(s):
        # 曲の中で一番大きく歌っている区間から 15dB 以内なら「歌あり」
        if s["vocal"] is None or loud is None:
            return True
        return loud > -60 and s["vocal"] >= loud - 15

    groups = {}
    for i, s in enumerate(segs):
        groups.setdefault(s["group"], []).append(i)
    g_energy = {g: np.mean([segs[i]["energy_db"] for i in idx]) for g, idx in groups.items()}

    rep = [g for g, idx in groups.items() if len(idx) >= 2 and any(has_vocal(segs[i]) for i in idx)]
    if rep:
        chorus = max(rep, key=lambda g: g_energy[g])
    else:
        chorus = segs[int(np.argmax([s["energy_db"] for s in segs]))]["group"]

    names = [None] * len(segs)
    for i, s in enumerate(segs):
        if s["group"] == chorus:
            names[i] = "サビ"
    first, last = 0, len(segs) - 1
    body = [s["energy_db"] for i, s in enumerate(segs) if i not in (first, last)] or [s["energy_db"] for s in segs]
    body_db = float(np.median(body))

    def edge_like(i):
        g = segs[i]["group"]
        only_edges = all(j in (first, last) for j in groups[g])
        quiet = segs[i]["energy_db"] < body_db - 4
        return len(groups[g]) == 1 or only_edges or quiet or not has_vocal(segs[i])

    if names[first] is None and edge_like(first):
        names[first] = "イントロ"
    if last > first and names[last] is None and edge_like(last):
        names[last] = "アウトロ"
    for i, s in enumerate(segs):
        if names[i] is None and s["vocal"] is not None and not has_vocal(s):
            names[i] = "間奏"

    # サビの直前に来ることが多いグループを Bメロ にする
    before = {}
    for i in range(1, len(segs)):
        if names[i] == "サビ" and names[i - 1] is None:
            before[segs[i - 1]["group"]] = before.get(segs[i - 1]["group"], 0) + 1
    b_group = max(before, key=before.get) if before else None
    remaining = [segs[i]["group"] for i in range(len(segs)) if names[i] is None]
    a_group = None
    for g in remaining:
        if g != b_group:
            a_group = g
            break
    if a_group is None and b_group is not None:
        # サビの前にしか歌のパートがない → それは Aメロ
        a_group, b_group = b_group, None
    first_chorus = next((i for i, nm in enumerate(names) if nm == "サビ"), len(segs))
    for i, s in enumerate(segs):
        if names[i]:
            continue
        if s["group"] == a_group:
            names[i] = "Aメロ"
        elif s["group"] == b_group:
            names[i] = "Bメロ"
        elif len(groups[s["group"]]) == 1 and i > first_chorus:
            names[i] = "Cメロ（大サビ前など）"
        else:
            names[i] = "その他"
    for s, nm in zip(segs, names):
        s["name"] = nm
    return segs
