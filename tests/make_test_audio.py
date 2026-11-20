#!/usr/bin/env python3
"""答えがわかっている合成音（テスト曲）を作る。

128 BPM・4/4拍子・キー A minor。
構成: イントロ4小節 → Aメロ8 → Bメロ4 → サビ8 → Aメロ8 → Bメロ4 → サビ8 → アウトロ4（計48小節）
サビだけ、歪んだギターの16分カッティングと高い声。
"""

import json
import os
import sys

import numpy as np
import soundfile as sf

SR = 44100
BPM = 128.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT
rng = np.random.default_rng(0)

NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord_notes(name):
    minor = name.endswith("m")
    root = NOTE[name[:-1] if minor else name]
    return root, [0, 3, 7] if minor else [0, 4, 7]


SECTIONS = [
    ("イントロ", ["Am", "F", "C", "G"]),
    ("Aメロ", ["Am", "F", "C", "G"] * 2),
    ("Bメロ", ["Dm", "Em", "F", "E"]),
    ("サビ", ["F", "G", "Em", "Am"] * 2),
    ("Aメロ", ["Am", "F", "C", "G"] * 2),
    ("Bメロ", ["Dm", "Em", "F", "E"]),
    ("サビ", ["F", "G", "Em", "Am"] * 2),
    ("アウトロ", ["Am", "F", "C", "G"]),
]


# 母音のフォルマント（声の響きの山）: (周波数, 幅)
VOWELS = [
    [(800, 80), (1200, 90), (2500, 120)],   # あ
    [(500, 60), (1800, 100), (2500, 120)],  # え
    [(300, 50), (2300, 100), (3000, 150)],  # い
    [(500, 60), (850, 80), (2500, 120)],    # お
    [(350, 50), (1300, 90), (2400, 120)],   # う
]


def voice(ph, f0, vowel, n, sr=SR):
    """人の声に近い音: 倍音をフォルマントで形づくり、子音の息の音を足す。"""
    out = np.zeros(n)
    for k in range(1, 40):
        fk = f0 * k
        if fk > sr / 2.5:
            break
        g = sum(1 / (1 + ((fk - F) / B) ** 2) for F, B in VOWELS[vowel]) / k ** 0.6
        out += np.sin(k * ph) * g
    out /= np.max(np.abs(out)) + 1e-9
    breath = rng.standard_normal(n) * np.exp(-np.arange(n) / sr / 0.03) * 0.3
    breath = breath - np.convolve(breath, np.ones(4) / 4, mode="same")
    shimmer = 1 + 0.08 * np.sin(2 * np.pi * 3.1 * np.arange(n) / sr + vowel)
    return out * shimmer + breath


SYLLABLES = ["laa", "nee", "mii", "doo", "ruu"]
_BANK = None


def voice_bank():
    """espeak-ng の声を音節ごとに用意する（なければ None）。人の声に近いので Demucs が歌として扱える。"""
    global _BANK
    if _BANK is not None:
        return _BANK or None
    import shutil
    import subprocess
    import tempfile
    import librosa
    if not shutil.which("espeak-ng"):
        _BANK = {}
        return None
    bank = {}
    with tempfile.TemporaryDirectory() as d:
        for i, syl in enumerate(SYLLABLES):
            w = os.path.join(d, f"{i}.wav")
            subprocess.run(["espeak-ng", "-v", "en", "-s", "90", "-p", "50", "-w", w, syl], check=True)
            y, _ = librosa.load(w, sr=SR, mono=True)
            y, _ = librosa.effects.trim(y, top_db=30)
            f0, vf, _ = librosa.pyin(y, fmin=60, fmax=400, sr=SR)
            base = float(np.nanmedian(librosa.hz_to_midi(f0[vf]))) if np.any(vf) else 50.0
            bank[i] = (y / (np.max(np.abs(y)) + 1e-9), base)
    _BANK = bank
    return bank


def sung_note(midi, n, idx):
    """音節を目的の高さ・長さにそろえる。"""
    import librosa
    bank = voice_bank()
    if not bank:
        return None
    y, base = bank[idx % len(bank)]
    y = librosa.effects.time_stretch(y, rate=len(y) / max(n, 1))
    y = librosa.effects.pitch_shift(y, sr=SR, n_steps=midi - base)
    y = y[:n]
    if len(y) < n:
        y = np.pad(y, (0, n - len(y)))
    return y / (np.max(np.abs(y)) + 1e-9)


def env(n, attack=0.005, decay=None, sr=SR):
    t = np.arange(n) / sr
    e = np.minimum(1, t / attack) if attack > 0 else np.ones(n)
    if decay:
        e *= np.exp(-t / decay)
    return e


def saw(f, n, sr=SR, harmonics=12):
    t = np.arange(n) / sr
    out = np.zeros(n)
    for k in range(1, harmonics + 1):
        if f * k > sr / 2.5:
            break
        out += np.sin(2 * np.pi * f * k * t) / k
    return out


def add(buf, start, sig):
    a = int(start * SR)
    b = min(len(buf), a + len(sig))
    if a < len(buf):
        buf[a:b] += sig[: b - a]


def kick(n=int(0.3 * SR)):
    t = np.arange(n) / SR
    f = 50 + 90 * np.exp(-t / 0.03)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12)


def snare(n=int(0.2 * SR)):
    noise = rng.standard_normal(n)
    noise = np.convolve(noise, np.ones(3) / 3, mode="same") - np.convolve(noise, np.ones(30) / 30, mode="same")
    return noise * np.exp(-np.arange(n) / SR / 0.05) * 0.8


def hat(n=int(0.04 * SR)):
    noise = rng.standard_normal(n)
    noise = noise - np.convolve(noise, np.ones(8) / 8, mode="same")
    return noise * np.exp(-np.arange(n) / SR / 0.01) * 0.25


def main(out_dir):
    total_bars = sum(len(c) for _, c in SECTIONS)
    dur = total_bars * BAR + 1.5
    N = int(dur * SR)
    drums, bass, pad, gtr, voc = (np.zeros(N) for _ in range(5))
    truth = {"bpm": BPM, "meter": 4, "key": "Am", "sections": [], "bars": []}
    K, S, H = kick(), snare(), hat()
    t = 0.0
    melody_low = [57, 60, 64, 62, 60, 59, 57, 55]      # A3〜E4 くらい
    melody_high = [69, 72, 71, 69, 67, 69, 72, 74]     # A4〜D5 くらい
    for sec, chords in SECTIONS:
        truth["sections"].append({"name": sec, "start": round(t, 3), "end": round(t + len(chords) * BAR, 3)})
        for bi, ch in enumerate(chords):
            truth["bars"].append({"t": round(t, 3), "chord": ch, "section": sec})
            root, iv = chord_notes(ch)
            # パッド（伸ばす音）
            n = int(BAR * SR)
            p = sum(saw(hz(48 + root + k + (12 if root + k < 5 else 0)), n, harmonics=6) for k in iv)
            level = 0.10 if sec in ("イントロ", "アウトロ") else 0.06
            pad_env = np.minimum(1, np.arange(n) / (0.15 * SR)) * np.minimum(1, (n - np.arange(n)) / (0.05 * SR))
            add(pad, t, p * pad_env * level)
            if sec not in ("イントロ", "アウトロ"):
                # ドラム
                for b in range(4):
                    bt = t + b * BEAT
                    if b in (0, 2) or sec == "サビ":
                        add(drums, bt, K * 0.9)
                    if b in (1, 3):
                        add(drums, bt, S * (1.0 if sec == "サビ" else 0.7))
                    subs = 4 if sec == "サビ" else 2
                    for s in range(subs):
                        add(drums, bt + s * BEAT / subs, H)
                # ベース（8分音符でルート）
                for e in range(8):
                    nn = int(BEAT / 2 * SR * 0.9)
                    f = hz(28 + (root - 4) % 12)  # E1〜D#2 のルート音
                    add(bass, t + e * BEAT / 2, saw(f, nn, harmonics=4) * env(nn, 0.004, 0.25) * 0.35)
                # ボーカル（1拍1音、ビブラート付き）
                mel = melody_high if sec == "サビ" else melody_low
                for b in range(4):
                    m = mel[(bi * 4 + b) % len(mel)]
                    nn = int(BEAT * SR * 0.92)
                    tt = np.arange(nn) / SR
                    f = hz(m) * (1 + 0.006 * np.sin(2 * np.pi * 5.5 * tt))
                    ph = 2 * np.pi * np.cumsum(f) / SR
                    tone = sung_note(m, nn, bi * 4 + b)
                    if tone is None:
                        tone = voice(ph, hz(m), (bi * 4 + b) % len(VOWELS), nn)
                    amp = 0.45 if sec == "サビ" else 0.25
                    add(voc, t + b * BEAT, tone * env(nn, 0.03) * np.minimum(1, (nn - np.arange(nn)) / (0.03 * SR)) * amp)
                # ギター
                if sec == "サビ":
                    for s16 in range(16):
                        nn = int(BEAT / 4 * SR * 0.45)  # 短く切る
                        g = sum(saw(hz(52 + root + k), nn) for k in iv + [12])
                        g = np.tanh(6 * g) * env(nn, 0.002, 0.04) * 0.10
                        add(gtr, t + s16 * BEAT / 4, g)
                elif sec in ("Aメロ", "Bメロ"):
                    for b in range(4):  # クリーンなアルペジオ（1拍1音）
                        nn = int(BEAT * SR)
                        f = hz(60 + root + iv[b % 3] + (12 if b == 3 else 0))  # コードの構成音
                        add(gtr, t + b * BEAT, np.sin(2 * np.pi * f * np.arange(nn) / SR) * env(nn, 0.003, 0.3) * 0.12)
            t += BAR
    mix = drums + bass + pad + gtr + voc
    mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.9
    stereo = np.stack([mix, mix], axis=1)
    os.makedirs(out_dir, exist_ok=True)
    wav = os.path.join(out_dir, "test_synth.wav")
    sf.write(wav, stereo, SR)
    truth["duration"] = dur
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_synth_truth.json"), "w", encoding="utf-8") as f:
        json.dump(truth, f, ensure_ascii=False, indent=1)
    print(wav)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input"))
