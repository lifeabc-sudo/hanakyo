#!/usr/bin/env python3
"""作詞チェックツール

使い方:
    python3 lyric_check.py songbook/lyrics/曲名.txt

調べること:
  1. 行ごとの音数（モーラ。「きゃ」は1、「ん」「っ」「ー」は1と数える）
  2. 同じメロディに乗る部分（1番Aメロと2番Aメロ、サビどうし など）の音数がそろっているか
  3. 行末の母音（韻のそろい方）
  4. NGワード（songbook/ng_words.txt）
  5. 前の曲で使った言葉の使い回し（songbook/lyrics/ の他の曲と比べる）

漢字の読みは自動変換なので、まれに読み違えます（例: 月 → がつ）。音数は ±1 の誤差を見込んでいます。
"""

import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SONGBOOK = os.path.join(HERE, "songbook")
SMALL = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
VOWEL = {}
for row, v in (("あかさたなはまやらわがざだばぱ", "a"), ("いきしちにひみりぎじぢびぴ", "i"),
               ("うくすつぬふむゆるぐずづぶぷ", "u"), ("えけせてねへめれげぜでべぺ", "e"),
               ("おこそとのほもよろをごぞどぼぽ", "o")):
    for ch in row:
        VOWEL[ch] = v


def to_hira(text):
    import pykakasi
    k = pykakasi.kakasi()
    return "".join(x["hira"] for x in k.convert(text))


def kata_to_hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def morae(text):
    """音数（モーラ）を数える。英語は1単語を音節数でおおまかに数える。"""
    eng = re.findall(r"[A-Za-z]+", text)
    jp = re.sub(r"[A-Za-z]+", "", text)
    h = kata_to_hira(to_hira(jp))
    h = re.sub(r"[^ぁ-ゖー]", "", h)
    n = sum(1 for c in h if c not in SMALL)
    for w in eng:
        n += max(1, len(re.findall(r"[aeiouy]+", w.lower())))
    return n, h


def last_vowel(hira):
    for c in reversed(hira):
        if c in ("ん", "っ", "ー") or c in SMALL:
            continue
        return VOWEL.get(c, "?")
    return "?"


def parse(text):
    """[見出し] ごとに歌詞の行をまとめる。"""
    secs = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[([^\]|:]+)", line)
        if m:
            cur = {"tag": m.group(1).strip(), "header": line, "lines": []}
            secs.append(cur)
            continue
        if cur is None:
            cur = {"tag": "(見出しなし)", "header": "", "lines": []}
            secs.append(cur)
        cur["lines"].append(line)
    return secs


def kind(tag):
    t = tag.lower()
    for k in ("pre-chorus", "final chorus", "chorus", "verse", "bridge", "breakdown"):
        if k in t:
            return "chorus" if k == "final chorus" else k
    return t


def load_ng():
    p = os.path.join(SONGBOOK, "ng_words.txt")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def past_words(exclude_path):
    """前の曲で使った言葉（2文字以上の漢字・カタカナのかたまり）。"""
    d = os.path.join(SONGBOOK, "lyrics")
    used = {}
    if not os.path.isdir(d):
        return used
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.abspath(p) == os.path.abspath(exclude_path) or not f.endswith(".txt"):
            continue
        body = "\n".join(l for l in open(p, encoding="utf-8") if not l.strip().startswith("["))
        for w in set(re.findall(r"[一-鿿]{1,4}|[ァ-ヶー]{2,}", body)):
            used.setdefault(w, []).append(os.path.splitext(f)[0])
    return used


def main(path):
    text = open(path, encoding="utf-8").read()
    secs = parse(text)
    L = [f"# 作詞チェック: {os.path.basename(path)}", ""]

    # 1. 音数
    L.append("## 1. 行ごとの音数（推定）")
    L.append("")
    for s in secs:
        if not s["lines"]:
            continue
        L.append(f"**{s['tag']}**")
        L.append("```")
        s["counts"], s["hira"] = [], []
        for line in s["lines"]:
            n, h = morae(line)
            s["counts"].append(n)
            s["hira"].append(h)
            L.append(f"{n:>3}音  {last_vowel(h)}  {line}")
        L.append("```")

    # 2. 同じメロディに乗る部分の比較
    L.append("## 2. 同じメロディに乗る部分の音数のそろい方")
    L.append("")
    L.append("1番と2番、サビどうしは同じメロディに乗るので、行ごとの音数の差が**2音以内**だと歌いやすいです。")
    L.append("")
    problems = 0
    groups = {}
    for s in secs:
        if s["lines"]:
            groups.setdefault(kind(s["tag"]), []).append(s)
    for k, ss in groups.items():
        if len(ss) < 2:
            continue
        base = ss[0]
        for other in ss[1:]:
            rows = []
            for i in range(min(len(base["counts"]), len(other["counts"]))):
                d = other["counts"][i] - base["counts"][i]
                if abs(d) > 2:
                    problems += 1
                    rows.append(f"- {i + 1}行目: {base['tag']} {base['counts'][i]}音 ↔ {other['tag']} {other['counts'][i]}音（差 {d:+d}）")
            if len(base["counts"]) != len(other["counts"]):
                longer_final = "final" in other["tag"].lower() and len(other["counts"]) > len(base["counts"])
                if not longer_final:
                    problems += 1
                rows.append(f"- 行数がちがう: {base['tag']} {len(base['counts'])}行 ↔ {other['tag']} {len(other['counts'])}行"
                            + ("（最後のサビを長くするのは、わざとならOK）" if longer_final else ""))
            L.append(f"**{base['tag']} ↔ {other['tag']}**: " + ("そろっている ✓" if not rows else "ずれあり"))
            L += rows
    if problems == 0:
        L.append("問題なし。")
    L.append("")

    # 3. 行末の母音
    L.append("## 3. 行末の母音（韻）")
    L.append("")
    for s in secs:
        if s.get("hira"):
            vs = [last_vowel(h) for h in s["hira"]]
            same = max(Counter(vs).values()) if vs else 0
            L.append(f"- {s['tag']}: {' '.join(vs)}" + ("（行末の母音がそろっている）" if same >= 3 else ""))
    L.append("")

    # 4. NGワード
    ng = load_ng()
    body = "\n".join(l for s in secs for l in s["lines"])
    hits = [w for w in ng if w in body]
    L.append("## 4. NGワード（songbook/ng_words.txt）")
    L.append("")
    L.append("なし ✓" if not hits else "見つかった: " + "、".join(hits))
    L.append("")

    # 5. 使い回し
    used = past_words(path)
    mine = Counter(re.findall(r"[一-鿿]{1,4}|[ァ-ヶー]{2,}", body))
    rep = sorted([(w, used[w]) for w in mine if w in used and len(w) >= 2], key=lambda x: -len(x[1]))
    L.append("## 5. 前の曲からの言葉の使い回し")
    L.append("")
    if rep:
        L.append("前に作った曲でも使った言葉です。別の言い方にできないか考えてください。")
        for w, songs in rep[:30]:
            L.append(f"- {w}（{'、'.join(songs)}）")
    else:
        L.append("なし ✓")
    L.append("")
    L.append(f"---\n音数のずれ {problems} か所 / NGワード {len(hits)} 個 / 使い回し {len(rep)} 語")
    out = "\n".join(L) + "\n"
    print(out)
    return problems, hits, rep


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
