#!/usr/bin/env python3
"""原曲と Suno で作った曲を、解析結果（reports/*.json）どうしで比べる。

使い方:
    python3 analyze.py input/suno版.m4a            # 先に Suno 版を解析しておく
    python3 compare.py reports/原曲.json reports/suno版.json

結果: reports/compare_<原曲>_vs_<suno版>.md
「どこが原曲とちがうか」と「Style をどう直すか」の提案を出す。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def tempo_close(a, b):
    """倍・半分のとりちがえも考えて、テンポが近いか。"""
    if not a or not b:
        return None, None
    best = min((abs(b * m - a) / a, m) for m in (0.5, 1, 2))
    return best[0] < 0.04, best[1]


def compare(orig, suno):
    rows, fixes = [], []

    def row(name, a, b, ok, note=""):
        mark = "✓" if ok else ("✗" if ok is False else "−")
        rows.append(f"| {name} | {a} | {b} | {mark} {note} |")

    # テンポ
    ta, tb = g(orig, "tempo", "bpm"), g(suno, "tempo", "bpm")
    ok, mult = tempo_close(ta, tb)
    note = "" if mult in (1, None) else "（倍・半分の読みちがいの可能性。耳で確認）"
    row("テンポ", f"{ta} BPM", f"{tb} BPM", ok, note)
    if ok is False:
        fixes.append(f"テンポがちがう → Style の BPM を {round(ta)} にして、速さを表す言葉（fast / mid-tempo など）も足す")

    # キーと雰囲気
    ka = g(orig, "key", "candidates", default=[{}])[0].get("name")
    kb = g(suno, "key", "candidates", default=[{}])[0].get("name")
    row("キー（推定）", ka, kb, None, "（Suno はキー指定を守らないことが多いので参考）")
    da, db = g(orig, "mood", "darkness"), g(suno, "mood", "darkness")
    if da is not None and db is not None:
        ok = abs(da - db) < 0.2
        row("暗さ（0=明るい〜1=暗い）", da, db, ok)
        if not ok:
            fixes.append("明るさがちがう → " + ("もっと暗く: dark, minor key を強める。Exclude に cheerful, bright" if da > db
                                         else "もっと明るく: Exclude から dark を外し、major key を足す"))

    # 編成
    la, lb = g(orig, "lineup", "band_only"), g(suno, "lineup", "band_only")
    row("バンドだけの編成か", la, lb, la == lb)
    if la and lb is False:
        fixes.append("Suno 版にシンセ・鍵盤が入っている → Exclude に synth, keyboard, strings, pad を足す。Style の先頭に raw live band sound")

    # 歪み・明るさ
    xa, xb = g(orig, "timbre_chorus", "distortion_score"), g(suno, "timbre_chorus", "distortion_score")
    if xa is not None and xb is not None:
        ok = abs(xa - xb) < 0.2
        row("ギターの歪み（サビ）", xa, xb, ok)
        if not ok:
            fixes.append("歪みがちがう → " + ("もっと歪ませる: heavily overdriven guitars" if xa > xb else "歪みを減らす: crunchy / clean guitars"))
    ca, cb = g(orig, "timbre_chorus", "centroid_hz"), g(suno, "timbre_chorus", "centroid_hz")
    if ca and cb:
        ok = abs(ca - cb) / ca < 0.2
        row("音の明るさ（サビ・Hz）", ca, cb, ok)
        if not ok:
            fixes.append("音色の明るさがちがう → " + ("bright sharp guitar tone を足す" if ca > cb else "warm darker tone を足す"))

    # 演奏の細部
    wa, wb = g(orig, "details", "guitar_lr_corr"), g(suno, "details", "guitar_lr_corr")
    if wa is not None and wb is not None:
        ok = (wa < 0.5) == (wb < 0.5)
        row("ギターの左右の広がり（相関・小さいほど広い）", wa, wb, ok)
        if not ok and wa < 0.5:
            fixes.append("ギターが左右に広がっていない → twin guitars panned hard left and right を前のほうに書く")
    ba, bb = g(orig, "details", "bass"), g(suno, "details", "bass")
    row("ベース", ba, bb, ba == bb)
    if ba != bb and g(orig, "details", "bass_melodic"):
        fixes.append("ベースが動かない → busy melodic running bass を前のほうに書く")
    ka2, kb2 = g(orig, "details", "kick_every_beat"), g(suno, "details", "kick_every_beat")
    row("バスドラを毎拍踏むか", ka2, kb2, ka2 == kb2)

    # ボーカル
    va, vb = g(orig, "vocal", "range"), g(suno, "vocal", "range")
    if va and vb:
        row("声の音域", f"{va['lowest']}〜{va['highest']}", f"{vb['lowest']}〜{vb['highest']}",
            abs(va["highest_hz"] - vb["highest_hz"]) / va["highest_hz"] < 0.12)
        if vb["highest_hz"] < va["highest_hz"] * 0.88:
            fixes.append("声が低い → high tenor, soaring high notes を足す")
    via, vib = g(orig, "vocal", "vibrato"), g(suno, "vocal", "vibrato")
    if via and vib and via.get("available") and vib.get("available"):
        ok = abs(via["extent_semitones"] - vib["extent_semitones"]) < 0.4
        row("ビブラートの幅（半音）", via["extent_semitones"], vib["extent_semitones"], ok)
        if not ok and via["extent_semitones"] > vib["extent_semitones"]:
            fixes.append("ビブラートが足りない → heavy wide vibrato を足す")
    sa, sb = g(orig, "vocal", "a_vs_chorus_db"), g(suno, "vocal", "a_vs_chorus_db")
    if sa is not None and sb is not None:
        ok = abs(sa - sb) < 3
        row("Aメロ→サビの声の強さの差（dB）", sa, sb, ok)
        if not ok and sa > sb:
            fixes.append("強弱が小さい → restrained quiet verses, explosive choruses を書き、歌詞の見出しにも restrained / explosive を入れる")

    # 構成
    na = [s["name"] for s in orig.get("sections", [])]
    nb = [s["name"] for s in suno.get("sections", [])]
    row("構成", " → ".join(na), " → ".join(nb), None)
    fa = next((s["start"] for s in orig.get("sections", []) if s["name"] == "サビ"), None)
    fb = next((s["start"] for s in suno.get("sections", []) if s["name"] == "サビ"), None)
    if fa and fb:
        ok = abs(fa - fb) < 15
        row("最初のサビの時刻（秒）", fa, fb, ok)
        if not ok:
            fixes.append("サビが来るのが" + ("早すぎる" if fb < fa else "遅すぎる") + " → 歌詞の行数（Aメロ・Bメロの長さ）を原曲に合わせる")
    ia, ib = g(orig, "intro_entry", "text"), g(suno, "intro_entry", "text")
    row("イントロの入り方", ia, ib, None)
    return rows, fixes


def main(a, b):
    orig, suno = load(a), load(b)
    rows, fixes = compare(orig, suno)
    ok = sum(1 for r in rows if "| ✓" in r)
    ng = sum(1 for r in rows if "| ✗" in r)
    L = [f"# 原曲とSuno版の比較: {orig['name']} vs {suno['name']}", "",
         f"一致 {ok} 項目 / ちがう {ng} 項目（数値はどちらも推定）", "",
         "| 項目 | 原曲 | Suno版 | 判定 |", "|---|---|---|---|"] + rows + ["", "## Style の直し方の提案", ""]
    L += [f"{i + 1}. {f}" for i, f in enumerate(fixes)] or ["大きなちがいは見つかりませんでした。あとは耳での判断です。"]
    out = "\n".join(L) + "\n"
    path = os.path.join(HERE, "reports", f"compare_{orig['name']}_vs_{suno['name']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
    print(f"保存: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
