#!/usr/bin/env python3
"""曲解析ツール

使い方:
    python analyze.py input/曲.mp4
    python analyze.py input/曲.mp4 --no-demucs   # 音源分離を使わず速く概算する

結果:
    reports/<名前>.md          日本語レポート
    reports/<名前>.json        数値データ
    reports/<名前>_energy.png  エネルギーとパート別音量のグラフ
    work/<名前>/               取り出した音声・分離した音声（Gitには入れない）
"""

import argparse
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser(description="画面収録の動画や音声から曲の特徴を解析する")
    ap.add_argument("file", help="input/ に置いたファイル（mp4 / mov / m4a / mp3 / wav）")
    ap.add_argument("--no-demucs", action="store_true", help="Demucs を使わず概算で分ける（速い）")
    ap.add_argument("--reports", default=os.path.join(HERE, "reports"))
    ap.add_argument("--work", default=os.path.join(HERE, "work"))
    args = ap.parse_args()

    try:
        run(args.file, args.reports, args.work, use_demucs=not args.no_demucs)
    except Exception as e:  # noqa: BLE001
        from songlab.audio import UserError
        if isinstance(e, UserError):
            print(f"\nエラー: {e}", file=sys.stderr)
        else:
            traceback.print_exc()
            print(f"\n想定外のエラーで止まりました: {e}", file=sys.stderr)
        sys.exit(1)


def run(src, reports_dir, work_root, use_demucs=True, log=print):
    import librosa
    import numpy as np

    from songlab import SR, analysis, audio, parts, report, stems

    t_start = time.time()
    name = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(work_root, name)
    os.makedirs(work, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    log(f"[1/7] ファイルを確認: {src}")
    info = audio.check_input(src)
    wav = audio.extract_wav(src, os.path.join(work, f"{name}.wav"))
    y, _ = librosa.load(wav, sr=SR, mono=True)
    duration = len(y) / SR

    log("[2/7] パートに分ける")
    st, sep = stems.separate(wav, y, work, use_demucs=use_demucs, log=log)
    harm_src = st["other"] + st["bass"] + 0.5 * st["vocals"]

    log("[3/7] テンポ・拍子・キー")
    tempo = analysis.tempo_and_beats(y)
    beats = tempo.pop("beat_times")
    tempo.pop("onset_env")
    meter = analysis.meter_and_downbeats(y, beats)
    y_harm = librosa.effects.harmonic(harm_src, margin=2.0)
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=SR)
    key = analysis.estimate_key(chroma.mean(axis=1))

    log("[4/7] コード進行")
    # 和音はベース以外の上物から、ベース音はベースのパートから別に読む
    y_chord = librosa.effects.harmonic(st["other"] + 0.3 * st["vocals"], margin=2.0)
    beat_chords = analysis.chords_per_beat(y_chord, beats, bass_audio=st["bass"])
    bars = analysis.chords_by_bar(beat_chords, meter["meter"], meter["phase"])

    log("[5/7] 構成とエネルギー")
    energy = analysis.energy_curve(y)
    feats, bar_energy = analysis.bar_features(y, bars, duration)
    segs = analysis.segment_song(feats, bar_energy, bars, duration)
    voc_db_total = float(np.mean(st["vocals"] ** 2))

    def vocal_ratio(a, b):
        """区間の歌の音量（dB）。"""
        ia, ib = int(a * SR), int(b * SR)
        if ib <= ia or voc_db_total < 1e-9:
            return -120.0
        return float(10 * np.log10(np.mean(st["vocals"][ia:ib] ** 2) + 1e-12))

    key = analysis.refine_relative_key(key, bars)
    sections = analysis.name_sections(segs, bars, bar_energy, duration,
                                      vocal_ratio=None if sep["approximate"] else vocal_ratio)
    balance = stems.section_balance(st, sections)
    overall = stems.section_balance(st, [{"start": 0, "end": duration}])[0]

    log("[6/7] 音色・シンセ・ボーカル")
    tim = parts.timbre(st["other"], beats)
    chorus_secs = [s for s in sections if s["name"] == "サビ"]
    tim_chorus = parts.timbre(st["other"], beats, chorus_secs[0]["start"], chorus_secs[0]["end"]) \
        if chorus_secs else {"available": False}
    intro = parts.intro_detail(st["other"], beats)
    syn = parts.synth_presence(st["other"], beats)
    voc = parts.vocal_analysis(st["vocals"], sections)

    log("[7/7] レポートを書く")
    res = {
        "name": name,
        "source": os.path.relpath(src, HERE) if os.path.isabs(src) else src,
        "file_info": info,
        "duration": round(duration, 2),
        "separation": sep,
        "tempo": tempo,
        "meter": meter,
        "key": key,
        "beat_chords": beat_chords,
        "bars": bars,
        "sections": sections,
        "balance": balance,
        "overall_balance": overall,
        "timbre": tim,
        "timbre_chorus": tim_chorus,
        "intro_detail": intro,
        "synth": syn,
        "vocal": voc,
        "energy_per_sec": {"time": energy["times"], "score": energy["score"], "db": energy["db"]},
    }
    res["mood"] = report.mood(key, bars, sections, energy)
    res["suno"] = report.suno_hints(res)
    png = os.path.join(reports_dir, f"{name}_energy.png")
    res["plot"] = png
    report.plot(res, energy, stems.stem_curves(st), png)
    md = os.path.join(reports_dir, f"{name}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(report.to_markdown(res))
    js = os.path.join(reports_dir, f"{name}.json")
    report.write_json(res, js)
    res["elapsed_sec"] = round(time.time() - t_start, 1)
    log(f"\n完了（{res['elapsed_sec']}秒）")
    log(f"  レポート: {md}\n  数値: {js}\n  グラフ: {png}")
    return res


if __name__ == "__main__":
    main()
