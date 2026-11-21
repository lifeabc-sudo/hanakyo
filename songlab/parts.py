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
    # 伸ばしている音が「澄んだ音（シンセ・ストリングスなど）」か「ノイズっぽい音（歪んだギター）」か。
    # 歪んだギターをのばしても音の変化は少ないので、のばし方だけではシンセと区別できない。
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    band = (freqs >= 200) & (freqs <= 6000)
    Sb = S[band] + 1e-10
    flat = np.exp(np.mean(np.log(Sb), axis=0)) / np.mean(Sb, axis=0)
    sus = stable & active
    n_act = max(1, int(active.sum()))
    tonal_sustain = float(np.sum(sus & (flat < 0.12)) / n_act)     # 澄んだ音をのばしている割合
    noisy_sustain = float(np.sum(sus & (flat >= 0.2)) / n_act)     # 歪んだ音をのばしている割合
    pad_score = float(np.clip(0.5 * tonal_sustain / 0.25 + 0.5 * steadiness, 0, 1)) * float(
        np.clip(1 - (noisy_sustain - tonal_sustain) / 0.3, 0, 1))

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
    if arp_score >= 0.5 and float(np.median(flat[active])) < 0.15:
        kind = "細かく規則的に並ぶ音（アルペジオ・シーケンス系）が目立つ"
    elif pad_score >= 0.6:
        kind = "澄んだ音をのばすパート（シンセパッド・ストリングス・オルガンなど）が目立つ"
    elif pad_score >= 0.4:
        kind = "澄んだ音をのばすパートが少しある（シンセ・鍵盤の可能性）"
    elif noisy_sustain > tonal_sustain + 0.05:
        kind = "シンセ・鍵盤らしい音は目立たない。のばしている音は歪んだギター（パワーコードなど）の可能性が高い"
    else:
        kind = "シンセ・鍵盤らしい音は目立たない（ギターなどが中心の可能性）"
    return {"available": True, "pad_score": round(pad_score, 2), "arpeggio_score": round(arp_score, 2),
            "tonal_sustain": round(tonal_sustain, 2), "noisy_sustain": round(noisy_sustain, 2),
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
    vib = vibrato(f0, voiced & active_f, sr)
    return {
        "vibrato": vib,
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


# ------------------------------------------------------------------ 編成とイントロの入り方

PART_JA = {"vocals": "歌", "drums": "ドラム", "bass": "ベース", "other": "ギターなど上物"}


def intro_entry(stems, beat_times, intro_end, sr=SR):
    """イントロで、どのパートがどの順番で入ってくるか。上物がリフっぽいかも調べる。"""
    end = max(4.0, min(intro_end if intro_end else 15.0, 25.0))
    step = 0.25
    entries = {}
    for k, v in stems.items():
        n = int(len(v) / sr / step)
        lv = np.array([10 * np.log10(np.mean(v[int(i * step * sr):int((i + 1) * step * sr)] ** 2) + 1e-12)
                       for i in range(n)])
        ref = np.percentile(lv, 75)               # その曲でふつうに鳴っているときの音量
        on = np.where(lv[: int(end / step)] > ref - 12)[0]
        entries[k] = round(float(on[0] * step), 2) if len(on) else None
    order = sorted([(t, k) for k, t in entries.items() if t is not None])
    first = order[0][1] if order else None

    # 上物（other）がイントロでリフ（音程が動く短いフレーズのくり返し）か、コードを鳴らしているか
    seg = stems["other"][: int(end * sr)]
    riff = None
    if np.sqrt(np.mean(seg ** 2)) > 1e-4:
        on = librosa.onset.onset_detect(y=seg, sr=sr, hop_length=HOP, units="frames")
        chroma = librosa.feature.chroma_cqt(y=seg, sr=sr, hop_length=HOP)
        if len(on) > 4:
            top = np.argmax(chroma[:, np.clip(on + 2, 0, chroma.shape[1] - 1)], axis=0)
            change = float(np.mean(top[1:] != top[:-1]))
            rate = len(on) / end
            bt = np.array([b for b in beat_times if b < end])
            bar_sec = 4 * float(np.median(np.diff(bt))) if len(bt) > 2 else 2.0
            per_bar = rate * bar_sec
            # 歪んだギターは音がのびるので発音が拾いにくい。曲のテンポに対して
            # 「1小節に1.5回以上、音程が動きながら鳴る」ならリフとみなす
            riff = {"notes_per_sec": round(rate, 2), "notes_per_bar": round(per_bar, 2),
                    "pitch_change": round(change, 2),
                    "is_riff": bool(change >= 0.45 and per_bar >= 1.5)}

    lines = []
    if order:
        t0 = order[0][0]
        same = [PART_JA[k] for t, k in order if t - t0 < 0.6]
        later = [(t, PART_JA[k]) for t, k in order if t - t0 >= 0.6]
        lines.append("・".join(same) + " から始まる")
        for t, nm in later:
            lines.append(f"{t:.1f}秒ごろに{nm}が入る")
    if entries.get("vocals") is None:
        lines.append(f"最初の{end:.0f}秒に歌は入らない")
    if riff:
        lines.append("上物は音程の動くフレーズ（リフ）を弾いている可能性が高い" if riff["is_riff"]
                     else "上物はコードを鳴らしている（リフではない）可能性が高い")
    return {"entries_sec": entries, "first": first, "riff": riff, "text": "、".join(lines), "checked_until": end}


def lineup(synth, timbre, balance):
    """編成の推定: バンドだけか、シンセ・鍵盤が入っているか。"""
    has_synth = bool(synth.get("available") and (
        synth["pad_score"] >= 0.5 or (synth["arpeggio_score"] >= 0.5 and synth.get("tonal_sustain", 0) >= 0.1)))
    dist = timbre.get("distortion_score", 0) if timbre.get("available") else 0
    gtr = "歪んだエレキギター" if dist >= 0.55 else "軽く歪んだギター" if dist >= 0.4 else "クリーンなギター（または鍵盤）"
    if has_synth:
        text = f"バンド（歌・{gtr}・ベース・ドラム）に、シンセ・鍵盤などの澄んだ音が加わっている可能性"
    else:
        text = f"バンドだけの編成（歌・{gtr}・ベース・ドラム）の可能性が高い。シンセ・鍵盤は目立たない"
    return {"band_only": not has_synth, "has_synth": has_synth, "guitar": gtr, "text": text}


# ------------------------------------------------------------------ 演奏の細かい特徴

def vibrato(f0, ok, sr=SR):
    """のばした音のビブラート（声のゆれ）の幅と速さ。"""
    fps = sr / HOP
    midi = librosa.hz_to_midi(f0)
    runs, i, n = [], 0, len(ok)
    while i < n:
        if ok[i]:
            j = i
            while j < n and ok[j]:
                j += 1
            if (j - i) / fps >= 0.4:
                runs.append((i, j))
            i = j
        else:
            i += 1
    ext, rate = [], []
    for a, b in runs:
        m = midi[a:b]
        w = max(3, int(fps * 0.16) | 1)   # ビブラート1周期（約0.15秒）くらいの幅でならす
        d = m - np.convolve(m, np.ones(w) / w, mode="same")
        d = d[w // 2: len(d) - w // 2]
        if len(d) < 8 or np.any(np.isnan(d)):
            continue
        ext.append(float(np.percentile(d, 95) - np.percentile(d, 5)))
        rate.append(float(np.sum(np.diff(np.sign(d)) != 0) / 2 / (len(d) / fps)))
    if len(ext) < 5:
        return {"available": False}
    e, r = float(np.median(ext)), float(np.median(rate))
    if e >= 0.9 and r >= 5.5:
        label = "幅が広く速いビブラート（ねっとり・ドラマチックな歌い方）"
    elif e >= 0.6:
        label = "はっきりしたビブラート"
    else:
        label = "ビブラートは控えめ（まっすぐ歌う）"
    return {"available": True, "extent_semitones": round(e, 2), "rate_hz": round(r, 1),
            "notes_checked": len(ext), "label": label}


def playing_details(stems, stereo_paths, beat_times, chorus, sr=SR):
    """ギターの左右の広がり、ベースの動き、ドラムの踏み方（サビで調べる）。"""
    out = {}
    ibi = float(np.median(np.diff(beat_times))) if len(beat_times) > 2 else 0.5
    a, b = (chorus["start"], chorus["end"]) if chorus else (0, len(stems["other"]) / sr)
    n_beats = max(1.0, (b - a) / ibi)

    # ギター（上物）の左右の広がり: 左右の相関が低いほど、別々のギターを左右に振っている
    if stereo_paths and stereo_paths.get("other"):
        import soundfile as sf
        y, ssr = sf.read(stereo_paths["other"])
        if y.ndim == 2:
            seg = y[int(a * ssr):int(b * ssr)]
            corr = float(np.corrcoef(seg[:, 0], seg[:, 1])[0, 1])
            out["guitar_lr_corr"] = round(corr, 2)
            out["guitar_width"] = ("左右に大きく広がっている（2本のギターを左右に振り分けている可能性が高い）" if corr < 0.5
                                   else "やや広がっている" if corr < 0.8 else "真ん中に集まっている")

    # ベース: 1拍あたりの発音数と、音の動き
    seg = stems["bass"][int(a * sr):int(b * sr)]
    if np.sqrt(np.mean(seg ** 2)) > 1e-4:
        on = librosa.onset.onset_detect(y=seg, sr=sr, hop_length=HOP, units="frames")
        per_beat = len(on) / n_beats
        C = np.abs(librosa.cqt(seg, sr=sr, hop_length=HOP, fmin=librosa.note_to_hz("E1"), n_bins=36))
        top = np.argmax(C[:, np.clip(on + 2, 0, C.shape[1] - 1)], axis=0) if len(on) else np.array([])
        change = float(np.mean(np.diff(top) != 0)) if len(top) > 1 else 0.0
        kinds = len(set(top.tolist()))
        out["bass_notes_per_beat"] = round(per_beat, 2)
        out["bass_pitch_change"] = round(change, 2)
        out["bass_pitches"] = kinds
        rhythm = "8分音符で刻む" if 1.5 <= per_beat < 3 else "16分音符で細かく刻む" if per_beat >= 3 else "のばし気味"
        move = "メロディのように動き回る" if change >= 0.4 and kinds >= 8 else "ルート（コードの一番下の音）中心"
        out["bass"] = f"{rhythm}・{move}"
        out["bass_melodic"] = bool(change >= 0.4 and kinds >= 8)
        out["bass_eighths"] = bool(1.5 <= per_beat < 3)

    # ドラム: バスドラ（低音）とハイハット（高音）の1拍あたりの数
    seg = stems["drums"][int(a * sr):int(b * sr)]
    if np.sqrt(np.mean(seg ** 2)) > 1e-4:
        S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=HOP))
        fr = librosa.fft_frequencies(sr=sr, n_fft=2048)
        res = {}
        for name, mask in (("kick", fr < 150), ("hat", fr > 6000)):
            e = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[mask]), sr=sr, hop_length=HOP)
            pk = librosa.util.peak_pick(e, pre_max=3, post_max=3, pre_avg=5, post_avg=5,
                                        delta=float(np.std(e)) * 0.8, wait=3)
            res[name] = len(pk) / n_beats
        out["kick_per_beat"] = round(res["kick"], 2)
        out["hat_per_beat"] = round(res["hat"], 2)
        out["drums"] = ("バスドラを毎拍踏む、勢いのある刻み" if res["kick"] >= 0.85
                        else "バスドラは要所だけ（ふつうの8ビート）")
        out["kick_every_beat"] = bool(res["kick"] >= 0.85)
    return out
