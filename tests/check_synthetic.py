#!/usr/bin/env python3
"""合成音を解析して、正解とどれくらい合っているかを確かめる。

    python tests/check_synthetic.py            # Demucs が使えれば使う
    python tests/check_synthetic.py --no-demucs
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    no_demucs = "--no-demucs" in sys.argv
    subprocess.run([sys.executable, os.path.join(ROOT, "tests", "make_test_audio.py")], check=True,
                   stdout=subprocess.DEVNULL)
    wav = os.path.join(ROOT, "input", "test_synth.wav")
    mp4 = os.path.join(ROOT, "input", "test_synth.mp4")
    # 画面収録の代わりに、黒い映像＋音声の mp4 を作る
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10",
                    "-i", wav, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", mp4], check=True)
    with open(os.path.join(ROOT, "tests", "test_synth_truth.json"), encoding="utf-8") as f:
        truth = json.load(f)

    from analyze import run
    res = run(mp4, os.path.join(ROOT, "reports"), os.path.join(ROOT, "work"),
              use_demucs=not no_demucs, log=lambda *a: None)

    results = []

    def check(name, ok, detail):
        results.append((name, ok, detail))

    bpm = res["tempo"]["bpm"]
    check("BPM", abs(bpm - truth["bpm"]) / truth["bpm"] < 0.02, f"推定 {bpm} / 正解 {truth['bpm']}")
    check("拍子", res["meter"]["meter"] == 4, f"推定 {res['meter']['meter']}/4 / 正解 4/4")
    keys = [c["key"] for c in res["key"]["candidates"]]
    check("キー（上位3つに正解）", truth["key"] in keys, f"推定 {keys} / 正解 {truth['key']}")
    check("キー（1位が正解）", keys[0] == truth["key"], f"1位 {keys[0]}")

    # コード: 正解の各小節の中央の時刻で、推定の拍ごとのコードを比べる
    beats = res["beat_chords"]
    hit = 0
    bar_len = 4 * 60 / truth["bpm"]
    for b in truth["bars"]:
        mid = b["t"] + bar_len / 2
        near = [x for x in beats if b["t"] + 0.1 <= x["t"] < b["t"] + bar_len - 0.1]
        if not near:
            continue
        names = [x["chord"].split("/")[0] for x in near]
        top = max(set(names), key=names.count)
        hit += top == b["chord"]
        _ = mid
    acc = hit / len(truth["bars"])
    check("コード（小節ごとの正解率 70%以上）", acc >= 0.7, f"{acc * 100:.0f}%")

    # 小節の頭の位置: 推定した小節の頭が正解の小節の頭にそろっているか
    starts = [b["t"] for b in truth["bars"]]
    est = [b["t"] for b in res["bars"]]
    aligned = sum(1 for e in est if min(abs(e - s) for s in starts) < 0.12) / max(1, len(est))
    check("小節の頭の位置（80%以上一致）", aligned >= 0.8, f"{aligned * 100:.0f}%")

    # サビ: 正解のサビの時間のうち、推定でもサビになっている割合
    tc = [s for s in truth["sections"] if s["name"] == "サビ"]
    ec = [s for s in res["sections"] if s["name"] == "サビ"]
    cover = sum(overlap(a["start"], a["end"], b["start"], b["end"]) for a in tc for b in ec)
    total = sum(a["end"] - a["start"] for a in tc)
    check("サビの位置（正解のサビの70%以上をカバー）", cover / total >= 0.7, f"{cover / total * 100:.0f}%")
    est_chorus_total = sum(b["end"] - b["start"] for b in ec)
    precision = cover / est_chorus_total if est_chorus_total else 0
    check("サビの位置（推定サビの70%以上が本当にサビ）", precision >= 0.7, f"{precision * 100:.0f}%")
    check("イントロ", res["sections"][0]["name"] == "イントロ",
          f"最初の区間: {res['sections'][0]['name']}（{res['sections'][0]['start']}〜{res['sections'][0]['end']}秒、正解 0〜{truth['sections'][0]['end']:.1f}秒）")
    names = [s["name"] for s in res["sections"]]
    check("Aメロ・Bメロが見つかる", "Aメロ" in names and "Bメロ" in names, " → ".join(names))

    # 音色: 曲全体（other）と、イントロ（パッドのみ = クリーン）
    from songlab import SR, parts, stems
    import librosa
    # サビの区間だけで歪みとカッティングを確かめる
    y, _ = librosa.load(os.path.join(ROOT, "work", "test_synth", "test_synth.wav"), sr=SR, mono=True)
    st = stems.approximate_stems(y) if res["separation"]["approximate"] else None
    if st is None:
        p = os.path.join(ROOT, "work", "test_synth", "demucs", "htdemucs", "test_synth")
        st = stems.load_stems_demucs({s: os.path.join(p, f"{s}.wav") for s in stems.STEMS})
    beats_t = [b["t"] for b in res["beat_chords"]]
    c = tc[0]
    a = next(s for s in truth["sections"] if s["name"] == "Aメロ")
    tc_ = parts.timbre(st["other"], beats_t, c["start"], c["end"])
    ta_ = parts.timbre(st["other"], beats_t, a["start"], a["end"])
    check("歪み: サビ（歪みギター）> Aメロ（クリーン）", tc_["distortion_score"] > ta_["distortion_score"],
          f"サビ {tc_['distortion_score']}（{tc_['distortion'].split('（')[0]}） / Aメロ {ta_['distortion_score']}（{ta_['distortion'].split('（')[0]}）")
    check("カッティング: サビで検出", tc_["cutting_score"] >= 0.6 and tc_["cutting_score"] > ta_["cutting_score"],
          f"サビ {tc_['cutting_score']} / Aメロ {ta_['cutting_score']}")

    v = res["vocal"]
    if v.get("available") and v.get("range"):
        r = v["range"]
        check("ボーカル音域（最低 G3〜B3、最高 C5〜E5 付近）",
              53 <= librosa.note_to_midi(r["lowest"]) <= 59 and 72 <= librosa.note_to_midi(r["highest"]) <= 76,
              f"推定 {r['lowest']}〜{r['highest']} / 正解 G3〜D5")
        check("Aメロよりサビの声が強い", (v["a_vs_chorus_db"] or 0) > 3, f"差 {v['a_vs_chorus_db']} dB（正解 約+5dB）")
    else:
        check("ボーカル", False, v.get("reason", ""))
    check("雰囲気: 暗め（短調）と判定", res["mood"]["darkness"] >= 0.5, res["mood"]["tone"])

    print(f"\n=== 合成音テスト（{'概算' if res['separation']['approximate'] else 'Demucs'}） ===")
    for n, ok, d in results:
        print(f"{'OK ' if ok else 'NG '} {n}: {d}")
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed} / {len(results)} 合格")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
