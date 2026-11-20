"""上物の音色（歪み・明るさ・カッティング）、シンセ、ボーカルの解析。"""

import librosa
import numpy as np

from . import HOP, SR


def _frames(t0, t1, sr=SR):
    return int(t0 * sr / HOP), int(t1 * sr / HOP)


# ------------------------------------------------------------------ 音色

def timbre(y, beat_times, t0=0.0, t1=None, sr=SR):
    """上物（other）の歪み・明るさ・カッティングを調べる。"""
    t1 = t1 if t1 is not None else len(y) / sr
    seg = y[int(t0 * sr):int(t1 * sr)]
    if len(seg) < sr or np.sqrt(np.mean(seg ** 2)) < 1e-4:
        return {"available": False, "reason": "この区間には上物の音がほとんどありません"}
    S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    rms = librosa.feature.rms(S=S)[0]
    active = rms > np.percentile(rms, 30)
    if active.sum() < 10:
        active = np.ones_like(rms, dtype=bool)
    band = (freqs >= 200) & (freqs <= 6000)
    Sb = S[band][:, active] + 1e-10
    flat = float(np.mean(np.exp(np.mean(np.log(Sb), axis=0)) / np.mean(Sb, axis=0)))
    hi = S[(freqs >= 2000) & (freqs <= 8000)][:, active] ** 2
    mid = S[(freqs >= 200) & (freqs < 2000)][:, active] ** 2
    hi_ratio = float(np.sum(hi) / (np.sum(mid) + 1e-10))
    # 波形のピークと平均の比（歪むと潰れて小さくなる）
    fl = 2048
    frames = librosa.util.frame(np.pad(seg, (0, fl)), frame_length=fl, hop_length=HOP)
    peak = np.max(np.abs(frames), axis=0)[: len(rms)]
    crest = float(np.median((peak / (rms + 1e-9))[active[: len(peak)]]))
    # 歪みスコア（0〜1）: 平坦度・高域の多さ・波形の潰れ
    d = (np.clip((flat - 0.02) / 0.10, 0, 1) * 0.45
         + np.clip((hi_ratio - 0.02) / 0.15, 0, 1) * 0.35
         + np.clip((3.2 - crest) / 1.4, 0, 1) * 0.20)
    if d >= 0.55:
        dist = "歪み寄り（ディストーション／オーバードライブ系の可能性が高い）"
    elif d <= 0.30:
        dist = "クリーン寄り"
    else:
        dist = "中間（軽い歪み、またはシンセ・ブラスなどと混ざっている）"

    cent = librosa.feature.spectral_centroid(S=S, sr=sr)[0][active]
    centroid = float(np.median(cent))
    if centroid >= 2500:
        bright = "かなり明るい（シャリシャリ・ジャキジャキ）"
    elif centroid >= 1500:
        bright = "明るめ"
    elif centroid >= 900:
        bright = "ふつう"
    else:
        bright = "こもった・暗め"

    cut = cutting(seg, [b - t0 for b in beat_times if t0 <= b < t1], sr)
    return {
        "available": True,
        "distortion_score": round(float(d), 2),
        "distortion": dist,
        "spectral_flatness": round(flat, 4),
        "high_band_ratio": round(hi_ratio, 3),
        "crest_factor": round(crest, 2),
        "centroid_hz": round(centroid),
        "brightness": bright,
        **cut,
    }


def cutting(seg, beats, sr=SR):
    """カッティング（短く切れた音を細かく刻む弾き方）の判定。"""
    if len(beats) < 4:
        return {"cutting": "判定できません（拍が少なすぎます）", "cutting_score": None}
    onset = librosa.onset.onset_strength(y=seg, sr=sr, hop_length=HOP)
    rms = librosa.feature.rms(y=seg, hop_length=HOP)[0]
    beats = np.array(beats)
    # 16分音符の位置を作る
    grid = []
    for a, b in zip(beats[:-1], beats[1:]):
        grid += [a + (b - a) * k / 4 for k in range(4)]
    grid = np.array(grid)
    gf = np.clip(librosa.time_to_frames(grid, sr=sr, hop_length=HOP), 0, len(onset) - 1)
    # 各位置の前後1フレームの最大値
    vals = np.array([onset[max(0, f - 1):f + 2].max() for f in gf])
    base = np.median(onset) + 1e-9
    strong = vals > base * 2.0
    pos = np.arange(len(grid)) % 4
    s16 = float(np.mean(strong[(pos == 1) | (pos == 3)]))   # 「ウラのウラ」
    s8 = float(np.mean(strong[pos == 2]))
    s4 = float(np.mean(strong[pos == 0]))
    # 音の短さ: 16分の区間の中で、音量が山の半分より下がる割合
    dips = []
    for f0, f1 in zip(gf[:-1], gf[1:]):
        if f1 - f0 < 2:
            continue
        r = rms[f0:f1]
        dips.append(float(r.min() / (r.max() + 1e-9)))
    dip = float(np.median(dips)) if dips else 1.0
    staccato = float(np.clip((0.75 - dip) / 0.5, 0, 1))
    score = float(np.clip(0.55 * s16 / 0.5 + 0.45 * staccato, 0, 1))
    if score >= 0.6 and s16 >= 0.3:
        label = "カッティングあり（16分音符で短く刻んでいる可能性が高い）"
    elif score >= 0.4:
        label = "細かい刻みが少しある（8分音符中心、またはミュート気味の刻み）"
    else:
        label = "カッティングはほぼなし（伸ばす音・コードを鳴らしっぱなしが中心）"
    return {
        "cutting": label,
        "cutting_score": round(score, 2),
        "hits_on_16th_offbeats": round(s16, 2),
        "hits_on_8th_offbeats": round(s8, 2),
        "hits_on_beats": round(s4, 2),
        "short_note_score": round(staccato, 2),
    }


def intro_detail(y, beat_times, sr=SR, length=20.0, window=5.0):
    """イントロ最初の20秒を5秒ごとに調べる。"""
    end = min(length, len(y) / sr)
    out = []
    t = 0.0
    while t < end - 1:
        r = timbre(y, beat_times, t, min(t + window, end), sr)
        r["start"], r["end"] = round(t, 1), round(min(t + window, end), 1)
        out.append(r)
        t += window
    return {"whole": timbre(y, beat_times, 0, end, sr), "windows": out}


# ------------------------------------------------------------------ シンセ

def synth_presence(y, beat_times, sr=SR):
    """伸ばす音（パッド・ブラス系）か、規則的な細かい音（アルペジオ）か。"""
    if np.sqrt(np.mean(y ** 2)) < 1e-4:
        return {"available": False}
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    rms = librosa.feature.rms(S=S)[0]
    active = rms > np.percentile(rms, 40)
    flux = np.r_[0, np.mean(np.maximum(0, np.diff(np.log1p(S * 50), axis=1)), axis=0)]
    stable = flux < np.percentile(flux[active], 35) if active.any() else flux < np.median(flux)
    sustain = float(np.mean(stable[active])) if active.any() else 0.0
    # 音量の揺れの小ささ（1秒窓の変動係数）
    win = int(sr / HOP)
    cvs = [np.std(rms[i:i + win]) / (np.mean(rms[i:i + win]) + 1e-9)
           for i in range(0, len(rms) - win, win) if active[i:i + win].mean() > 0.5]
    steadiness = float(np.clip(1 - np.median(cvs) / 0.6, 0, 1)) if cvs else 0.0
    pad_score = float(np.clip(0.5 * sustain / 0.6 + 0.5 * steadiness, 0, 1))

    # アルペジオ: 8分・16分の規則的な発音 + 1音ごとに音程が変わる
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    on_frames = librosa.onset.onset_detect(onset_envelope=onset, sr=sr, hop_length=HOP, backtrack=False)
    arp_score, regular, change = 0.0, 0.0, 0.0
    if len(on_frames) > 16 and len(beat_times) > 4:
        ibi = float(np.median(np.diff(beat_times)))
        ioi = np.diff(librosa.frames_to_time(on_frames, sr=sr, hop_length=HOP))
        near = np.minimum(np.abs(ioi - ibi / 2) / (ibi / 2), np.abs(ioi - ibi / 4) / (ibi / 4))
        regular = float(np.mean(near < 0.2))
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
        top = np.argmax(chroma[:, np.clip(on_frames + 2, 0, chroma.shape[1] - 1)], axis=0)
        change = float(np.mean(top[1:] != top[:-1]))
        arp_score = float(np.clip(regular / 0.6, 0, 1) * np.clip(change / 0.6, 0, 1))
    if arp_score >= 0.5:
        kind = "細かく規則的に並ぶ音（アルペジオ・シーケンス系）が目立つ"
    elif pad_score >= 0.6:
        kind = "伸ばす音（パッド・ストリングス・ブラス系）が目立つ"
    elif pad_score >= 0.4:
        kind = "伸ばす音が少しある"
    else:
        kind = "シンセ・キーボードらしい音は目立たない（ギターなどが中心の可能性）"
    return {"available": True, "pad_score": round(pad_score, 2), "arpeggio_score": round(arp_score, 2),
            "sustain_ratio": round(sustain, 2), "steadiness": round(steadiness, 2),
            "regular_onsets": round(regular, 2), "pitch_change_per_note": round(change, 2), "kind": kind}


# ------------------------------------------------------------------ ボーカル

def vocal_analysis(voc, sections, sr=SR):
    rms = librosa.feature.rms(y=voc, hop_length=HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)
    db = librosa.amplitude_to_db(rms + 1e-9, ref=np.max)
    active = db > -30
    if active.mean() < 0.03:
        return {"available": False, "reason": "歌声がほとんど見つかりませんでした（インスト曲の可能性）"}

    f0, voiced, vprob = librosa.pyin(voc, fmin=70, fmax=1100, sr=sr, frame_length=2048,
                                     hop_length=HOP, fill_na=np.nan)
    n = min(len(f0), len(active))
    f0, voiced, active_f = f0[:n], voiced[:n], active[:n]
    ok = voiced & active_f & ~np.isnan(f0)
    rng = None
    if ok.sum() > 50:
        midi = librosa.hz_to_midi(f0[ok])
        lo, hi, mean = np.percentile(midi, 3), np.percentile(midi, 97), np.median(midi)
        rng = {
            "lowest": librosa.midi_to_note(int(round(lo))),
            "highest": librosa.midi_to_note(int(round(hi))),
            "median": librosa.midi_to_note(int(round(mean))),
            "lowest_hz": round(float(librosa.midi_to_hz(lo))),
            "highest_hz": round(float(librosa.midi_to_hz(hi))),
            "median_hz": round(float(librosa.midi_to_hz(mean))),
            "span_semitones": int(round(hi - lo)),
            "voiced_ratio": round(float(ok.mean()), 2),
        }
        # 男性ならA4（440Hz）以上、女性ならD5（587Hz）以上が出ていればハイトーン
        if hi >= 69 and mean >= 60:
            rng["high_tone"] = "ハイトーン寄り（男性ボーカルならかなり高い。女性ならふつう〜やや高め）"
        elif hi >= 74:
            rng["high_tone"] = "最高音は高い（女性ボーカルならハイトーン寄り）"
        else:
            rng["high_tone"] = "ハイトーンではない（中低音中心）"

    # セクションごとの声の強さ
    per = []
    for s in sections:
        a, b = int(s["start"] * sr / HOP), int(s["end"] * sr / HOP)
        seg_db = db[a:b]
        act = seg_db > -30
        per.append({
            "name": s["name"],
            "start": s["start"],
            "vocal_db": round(float(np.mean(seg_db[act])), 1) if act.sum() > 10 else None,
            "singing_ratio": round(float(act.mean()), 2) if len(act) else 0.0,
        })

    def avg(name):
        xs = [p["vocal_db"] for p in per if p["name"] == name and p["vocal_db"] is not None]
        return float(np.mean(xs)) if xs else None

    a_db, c_db = avg("Aメロ"), avg("サビ")
    sung = [p["vocal_db"] for p in per if p["vocal_db"] is not None and p["singing_ratio"] > 0.2]
    spread = float(max(sung) - min(sung)) if len(sung) >= 2 else None
    if spread is None:
        steady = "判定できません"
    elif spread < 3:
        steady = "全体をほぼ同じ強さで歌っている（ずっと張って歌うタイプ）"
    elif spread < 6:
        steady = "やや強弱をつけている"
    else:
        steady = "はっきり強弱をつけている（Aメロは抑えめ→サビで張る、など）"

    shouts = find_shouts(voc, db, vprob[:n] if vprob is not None else None, sr)
    return {
        "available": True,
        "range": rng,
        "per_section": per,
        "a_vs_chorus_db": round(c_db - a_db, 1) if a_db is not None and c_db is not None else None,
        "dynamic_spread_db": round(spread, 1) if spread is not None else None,
        "steadiness": steady,
        "shouts": shouts,
    }


def find_shouts(voc, db, vprob, sr=SR):
    """短く叫ぶ声（掛け声）の候補を探す。"""
    act = db > -18
    events, i = [], 0
    n = len(act)
    while i < n:
        if act[i]:
            j = i
            while j < n and act[j]:
                j += 1
            events.append((i, j))
            i = j
        else:
            i += 1
    fps = sr / HOP
    out = []
    for a, b in events:
        dur = (b - a) / fps
        if not 0.12 <= dur <= 0.6:
            continue
        pre = db[max(0, a - int(0.4 * fps)):a]
        post = db[b:b + int(0.4 * fps)]
        loud = db[a:b].max() > -6                      # 曲の中でもかなり大きい声
        isolated = len(pre) and len(post) and pre.max() < -30 and post.max() < -30
        rough = vprob is None or np.nanmean(vprob[a:b]) < 0.4   # 音程がはっきりしない（叫び声）
        if loud and isolated and rough:
            out.append(round(a / fps, 1))
    return {"count": len(out), "times": out[:20],
            "note": "短く強い、前後が静かな声を数えた推定です。ドラムの音が混ざると誤検出することがあります。"}
