"""雰囲気の判定、Suno へのヒント、レポート（md / json / png）の出力。"""

import json
import os

import numpy as np

SEC_EN = {"イントロ": "Intro", "Aメロ": "Verse", "Bメロ": "Pre", "サビ": "Chorus", "間奏": "Inter",
          "アウトロ": "Outro", "Cメロ（大サビ前など）": "Bridge", "その他": "Other"}


def fmt_time(t):
    t = max(0, float(t))
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ------------------------------------------------------------------ 雰囲気

def mood(key, bars, sections, energy):
    qual = {"major": 0, "minor": 0}
    for b in bars:
        c = b["chord"].split("/")[0].split(" ")[0]
        if c == "N":
            continue
        if c.endswith("m") or ("m7" in c and "maj7" not in c):
            qual["minor"] += 1
        else:
            qual["major"] += 1
    tot = qual["major"] + qual["minor"]
    minor_ratio = qual["minor"] / tot if tot else 0.5
    mode = key["candidates"][0]["mode"]
    # キーの確からしさが低いとき（平行調と区別しきれないとき）は、キーの長短より和音の割合を重く見る
    w_mode = 0.6 if key["confidence"] >= 0.4 else 0.2
    dark = (1 if mode == "minor" else 0) * w_mode + minor_ratio * (1 - w_mode)
    if dark >= 0.6:
        tone = "暗め・シリアス（短調で、マイナーの和音が多い）"
        color = "minor key, dark and serious"
    elif dark >= 0.3:
        if key["confidence"] < 0.4:
            # 長調か短調か言い切れないときは、キーを断定しない言い方にする
            tone = "明暗が混ざっている（メジャーとマイナーの和音が混ざった、切なくて熱い響き）"
            color = "bittersweet mix of major and minor chords"
        elif mode == "major":
            tone = "明暗が混ざっている（明るいキーに、マイナーの和音を多く混ぜた切ない響き）"
            color = "major key with bittersweet minor chords"
        else:
            tone = "明暗が混ざっている（短調だけど、メジャーの和音で前向きに聞こえる響き）"
            color = "minor key with uplifting major chords"
    else:
        tone = "明るめ（長調で、メジャーの和音が多い）"
        color = "bright major key"

    sc = energy["score"]
    peaks = []
    for s in sections:
        seg = sc[int(s["start"]):int(np.ceil(s["end"]))]
        s["energy_score"] = round(float(seg.mean()), 0) if len(seg) else 0
    top = sorted(sections, key=lambda s: s["energy_score"], reverse=True)[:3]
    for s in top:
        peaks.append(f"{fmt_time(s['start'])}〜（{s['name']}）")

    first_chorus = next((s for s in sections if s["name"] == "サビ"), None)
    entry = "サビを見つけられませんでした"
    if first_chorus:
        t = first_chorus["start"]
        before = sc[max(0, int(t) - 4):int(t)]
        after = sc[int(t):int(t) + 4]
        jump = float(after.mean() - before.mean()) if len(before) and len(after) else 0.0
        how = ("いきなりドーンと上がる" if jump >= 25 else
               "はっきり一段上がる" if jump >= 10 else
               "なだらかに入る（前から盛り上がっている）")
        entry = (f"最初のサビは {fmt_time(t)} から（曲の始まりから約{int(t)}秒）。"
                 f"直前の4秒と比べてエネルギーが {jump:+.0f} 点 → {how}")
        if len(sections) > 1 and first_chorus is sections[1]:
            entry += "。イントロのすぐ後にサビが来る構成"
    return {"tone": tone, "color": color, "minor_chord_ratio": round(minor_ratio, 2), "darkness": round(dark, 2),
            "peaks": peaks, "intro_to_chorus": entry}


# ------------------------------------------------------------------ Suno

def suno_hints(res):
    """Style は「音の事実」だけで作る。ジャンル名（アニソン・J-POP など）は入れない。
    ジャンル名を入れると、Suno がそのジャンルのありがちな音（シンセ入りのアニソンなど）に寄せてしまうため。"""
    axes, why = [], []
    bpm = res["tempo"]["bpm"]
    m = res["mood"]
    t = res["timbre_chorus"] if res.get("timbre_chorus", {}).get("available") else res["timbre"]
    syn = res["synth"]
    voc = res["vocal"]
    lu = res["lineup"]
    intro = res.get("intro_entry") or {}
    names = [s["name"] for s in res["sections"]]

    # 軸1: 編成と音（いちばん大事なので先頭）
    inst = []
    if lu["band_only"]:
        inst.append("raw live band sound")
    if t.get("available"):
        if t["distortion_score"] >= 0.55:
            inst.append("overdriven electric guitars")
        elif t["distortion_score"] >= 0.40:
            inst.append("crunchy electric guitars")
        else:
            inst.append("clean electric guitars")
        if (t.get("cutting_score") or 0) >= 0.6:
            inst.append("tight 16th-note guitar cutting")
    inst += ["bass", "live drums"]
    if lu["has_synth"]:
        inst.append("synth arpeggios" if syn.get("arpeggio_score", 0) >= 0.5 else "synth pads")
    axes.append(", ".join(inst))
    why.append(f"編成: {lu['text']}")

    # 軸2: テンポと響き
    axes.append(f"{int(round(bpm))} BPM, {m['color']}")
    why.append(f"テンポ {bpm} BPM（推定）、響き: {m['tone']}")

    # 軸3: 曲の組み立て（事実として見つかったものだけ）
    arr = []
    if intro.get("first") == "other" and (intro.get("riff") or {}).get("is_riff"):
        arr.append("opens with a guitar riff")
    elif intro.get("first") == "drums":
        arr.append("opens with drums")
    elif intro.get("first") == "vocals":
        arr.append("opens with vocals")
    if any("ソロ" in n for n in names):
        arr.append("guitar solo")
    if any("落ちサビ" in n for n in names):
        arr.append("quiet breakdown before final chorus")
    if arr:
        axes.append(", ".join(arr))
        why.append("組み立て: " + (intro.get("text") or ""))

    # 軸4: ボーカル
    if voc.get("available") and voc.get("range"):
        r = voc["range"]
        v = "high-pitched powerful vocal" if "ハイトーン寄り" in r.get("high_tone", "") else "strong mid-range vocal"
        if voc.get("dynamic_spread_db") is not None and voc["dynamic_spread_db"] < 3:
            v += ", belting throughout"
        if voc["shouts"]["count"] >= 3:
            v += ", shouted backing calls"
        axes.append(v)
        why.append(f"ボーカル: 音域 {r['lowest']}〜{r['highest']}、{voc['steadiness']}")

    exclude = []
    if lu["band_only"]:
        exclude += ["synth", "synthesizer", "keyboard", "piano", "strings", "brass", "orchestral", "electronic", "EDM"]
    if m["darkness"] >= 0.6:
        exclude += ["cheerful", "idol pop"]
    elif m["darkness"] < 0.3:
        exclude += ["dark", "gloomy"]
    else:
        exclude += ["idol pop"]
    if bpm >= 120:
        exclude += ["ballad", "slow"]
    if t.get("available") and t["distortion_score"] >= 0.55:
        exclude += ["acoustic", "lo-fi"]
    exclude += ["autotune"]
    return {"style": ", ".join(axes), "axes": axes, "reasons": why,
            "exclude": ", ".join(dict.fromkeys(exclude)),
            "note": ("ジャンル名（アニソン・J-POP など）はわざと入れていません。入れると Suno がそのジャンルのありがちな音に寄せるためです。"
                     "男性か女性かは自動で判定していないので、Style の先頭に male vocal / female vocal を足してください。")}


# ------------------------------------------------------------------ グラフ

def plot(res, energy, curves, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    jp = None
    for f in font_manager.findSystemFonts():
        if any(k in f.lower() for k in ("ipag", "ipaexg", "notosanscjk", "notosansjp", "japanese")):
            jp = f
            break
    if jp:
        font_manager.fontManager.addfont(jp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=jp).get_name()
    label = (lambda s: s) if jp else (lambda s: SEC_EN.get(s, s))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax1.plot(energy["times"], energy["score"], color="#d1495b", lw=1.6)
    ax1.set_ylabel("Energy (0-100)")
    ax1.set_ylim(0, 105)
    colors = {"vocals": "#d1495b", "drums": "#2e4057", "bass": "#00798c", "other": "#edae49"}
    for k, v in curves.items():
        ax2.plot(np.arange(len(v)), v, label=k, color=colors.get(k), lw=1.2)
    ax2.set_ylabel("Part level (dB)")
    ax2.set_xlabel("Time (sec)")
    ax2.legend(loc="lower right", ncol=4, fontsize=9)
    for i, s in enumerate(res["sections"]):
        for ax in (ax1, ax2):
            if s["name"] == "サビ":
                ax.axvspan(s["start"], s["end"], color="#d1495b", alpha=0.08)
            ax.axvline(s["start"], color="gray", lw=0.6, ls="--")
        ax1.text(s["start"] + 0.5, 100, label(s["name"]), fontsize=8, va="top")
    title = res["name"] + (" (parts: approx)" if res["separation"]["approximate"] else "")
    ax1.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ------------------------------------------------------------------ Markdown

def conf_text(c):
    return f"確からしさ {c['confidence_label']}（{c['confidence']:.2f}）"


def to_markdown(res):
    L = []
    name = res["name"]
    t, k, me = res["tempo"], res["key"], res["meter"]
    m, h = res["mood"], res["suno"]
    approx = res["separation"]["approximate"]
    L.append(f"# 曲解析レポート：{name}")
    L.append("")
    L.append(f"元ファイル: `{res['source']}`　長さ: {fmt_time(res['duration'])}")
    L.append("")
    L.append("> 数値はすべて機械による**推定**です。耳で聴いた印象とちがうときは、耳のほうを信じてください。")
    L.append("")

    # 3行まとめ
    chorus = next((s for s in res["sections"] if s["name"] == "サビ"), None)
    k0 = k["candidates"][0]
    L.append("## 3行まとめ")
    L.append("")
    L.append(f"1. **{t['bpm']:.0f} BPM・{me['meter']}拍子・キーは {k0['name']}**（すべて推定）。{m['tone']}。")
    if chorus:
        L.append(f"2. 最初のサビは **{fmt_time(chorus['start'])}** から。{m['intro_to_chorus'].split('→')[-1].strip()}。")
    else:
        L.append("2. サビははっきり見つかりませんでした。")
    tim = res["timbre"]
    voc = res["vocal"]
    line3 = []
    if tim.get("available"):
        line3.append(f"上物は{tim['distortion'].split('（')[0]}・{tim['brightness'].split('（')[0]}")
    if voc.get("available") and voc.get("range"):
        line3.append(f"ボーカルは {voc['range']['lowest']}〜{voc['range']['highest']}（{voc['range']['high_tone'].split('（')[0]}）")
    L.append(f"3. {'、'.join(line3) if line3 else '音色・ボーカルの情報は取れませんでした'}。")
    L.append("")

    # Suno
    L.append("## Sunoへのヒント")
    L.append("")
    L.append("**Style（そのまま貼れます）**")
    L.append("```")
    L.append(h["style"])
    L.append("```")
    L.append("**Exclude Styles**")
    L.append("```")
    L.append(h["exclude"])
    L.append("```")
    L.append(f"- {h['note']}")
    for r in h["reasons"]:
        L.append(f"- {r}")
    L.append("")

    # 基本情報
    L.append("## 1. 基本情報")
    L.append("")
    L.append("| 項目 | 結果 | メモ |")
    L.append("|---|---|---|")
    L.append(f"| 長さ | {fmt_time(res['duration'])} | |")
    alts = " / ".join(f"{a:.0f}" for a in t["alternatives"])
    L.append(f"| BPM（1分間の拍の数） | {t['bpm']:.1f}（推定） | {conf_text(t)}。倍・半分の候補: {alts or 'なし'} |")
    L.append(f"| 拍子 | {me['meter']}/4（推定） | {conf_text(me)} |")
    keys = "、".join(f"{i + 1}位 {c['name']}" for i, c in enumerate(k["candidates"]))
    L.append(f"| キー（曲の中心になる音） | {k0['name']}（推定） | {keys}。{conf_text(k)} |")
    L.append("")
    L.append("※ 長調と短調は「C major と A minor」のように同じ音を使う組み合わせ（平行調）があり、取り違えやすいです。")
    L.append("")

    # 構成
    L.append("## 2. 構成（推定）")
    L.append("")
    L.append("サビは「エネルギーが一番高く、くり返される部分」として推定しています。同じ記号（A, B…）は、似ている部分です。")
    L.append("")
    L.append("| # | 時刻 | 長さ | 小節 | 推定 | 似ている部分 | エネルギー |")
    L.append("|---|---|---|---|---|---|---|")
    for i, s in enumerate(res["sections"]):
        L.append(f"| {i + 1} | {fmt_time(s['start'])}〜{fmt_time(s['end'])} | {s['end'] - s['start']:.0f}秒 | "
                 f"{s['bars']} | {s['name']} | {s['group']} | {s.get('energy_score', 0):.0f} |")
    L.append("")

    # コード
    L.append("## 3. コード進行（推定）")
    L.append("")
    L.append("1マス = 1小節。`C D` は小節の前半と後半でコードが変わるという意味です。"
             "`?` は自信がないところ、`N` は和音がない（ドラムだけ・無音など）ところです。"
             "`C/E` のような分数コードは「Cの和音で、いちばん低い音がE」という意味です。")
    L.append("")
    for i, s in enumerate(res["sections"]):
        bars = [b for b in res["bars"] if s["start"] - 0.05 <= b["t"] < s["end"] - 0.05]
        L.append(f"**{i + 1}. {s['name']}（{fmt_time(s['start'])}〜）**")
        L.append("")
        rows = [bars[j:j + 4] for j in range(0, len(bars), 4)]
        L.append("```")
        for r in rows:
            L.append("| " + " | ".join(f"{b['display']:<7}" for b in r) + " |")
        L.append("```")
    unsure = sum(1 for b in res["bars"] if b["display"].endswith("?"))
    L.append(f"自信がない小節: {unsure} / {len(res['bars'])}")
    L.append("")

    # 楽器バランス
    L.append("## 4. 楽器のバランス" + ("（概算）" if approx else ""))
    L.append("")
    L.append(f"- **編成（推定）**: {res['lineup']['text']}")
    ie = res.get("intro_entry") or {}
    if ie.get("text"):
        L.append(f"- **イントロの入り方（最初の{ie['checked_until']:.0f}秒）**: {ie['text']}")
    L.append("")
    if approx:
        L.append(f"> Demucs（AIでパートを分ける道具）が使えなかったので、**概算**です。理由: {res['separation']['note']}。"
                 "打楽器と音程楽器の分離（HPSS）と周波数で分けているため、特に vocals と other は大まかです。")
    else:
        L.append("Demucs（AIで vocals / drums / bass / other に分ける道具）で分けた結果です。other はギター・シンセ・ピアノなど上物全部です。")
    L.append("")
    L.append("| セクション | 歌 vocals | ドラム drums | ベース bass | 上物 other |")
    L.append("|---|---|---|---|---|")
    for s, b in zip(res["sections"], res["balance"]):
        L.append(f"| {s['name']}（{fmt_time(s['start'])}） | {b['vocals']}% | {b['drums']}% | {b['bass']}% | {b['other']}% |")
    ob = res["overall_balance"]
    L.append(f"| **曲全体** | {ob['vocals']}% | {ob['drums']}% | {ob['bass']}% | {ob['other']}% |")
    L.append("")
    L.append("※ 人の耳の聞こえ方に合わせて重みをつけた（B特性）音量の比率です。低音は実際のエネルギーより少し控えめに数えています。")
    L.append("")

    # 音色
    L.append("## 5. ギターなど上物の音色（other パート）")
    L.append("")
    if tim.get("available"):
        tc = res.get("timbre_chorus") or {}
        has_c = tc.get("available")

        def c(v):
            return f" | {v}" if has_c else ""
        L.append("| 項目 | 曲全体" + (" | 最初のサビ" if has_c else "") + " | 判定に使った数値（曲全体） |")
        L.append("|---|---|" + ("---|" if has_c else "") + "---|")
        L.append(f"| 歪み | {tim['distortion']}" + c(f"{tc.get('distortion', '')}（{tc.get('distortion_score')}）")
                 + f" | 歪みスコア {tim['distortion_score']}（0=クリーン〜1=歪み）。"
                 f"スペクトル平坦度 {tim['spectral_flatness']}（ノイズっぽさ）、高域比 {tim['high_band_ratio']}、波高率 {tim['crest_factor']}（小さいほど音が潰れている） |")
        L.append(f"| 明るさ | {tim['brightness']}" + c(f"{tc.get('brightness', '')}（{tc.get('centroid_hz')}Hz）")
                 + f" | スペクトル重心（音の重心の高さ）{tim['centroid_hz']} Hz |")
        L.append(f"| カッティング | {tim['cutting']}" + c(f"{tc.get('cutting', '')}（{tc.get('cutting_score')}）")
                 + f" | スコア {tim['cutting_score']}。16分のウラの発音率 {tim['hits_on_16th_offbeats']}、音の短さ {tim['short_note_score']} |")
        L.append("")
        L.append("※ Sunoへのヒントは、曲の印象を決める**サビの音色**をもとにしています。")
        L.append("")
        intro = res["intro_detail"]
        L.append("**イントロ最初の20秒（5秒ごと）**")
        L.append("")
        L.append("| 時間 | 歪み | 明るさ | カッティング |")
        L.append("|---|---|---|---|")
        for w in intro["windows"]:
            if not w.get("available"):
                L.append(f"| {w['start']:.0f}〜{w['end']:.0f}秒 | 音がほぼない | | |")
                continue
            L.append(f"| {w['start']:.0f}〜{w['end']:.0f}秒 | {w['distortion'].split('（')[0]}（{w['distortion_score']}） | "
                     f"{w['brightness'].split('（')[0]}（{w['centroid_hz']}Hz） | {w['cutting'].split('（')[0]}（{w['cutting_score']}） |")
    else:
        L.append(tim.get("reason", "解析できませんでした"))
    L.append("")

    # シンセ
    syn = res["synth"]
    L.append("## 6. シンセ・キーボードの存在感")
    L.append("")
    if syn.get("available"):
        L.append(f"- 判定: **{syn['kind']}**")
        L.append(f"- 伸ばす音のスコア {syn['pad_score']}（0〜1）、アルペジオのスコア {syn['arpeggio_score']}（0〜1）")
        L.append("- ※ other パートにはギターも入っているので、ギターとシンセを完全には区別できません。")
    else:
        L.append("解析できませんでした。")
    L.append("")

    # ボーカル
    L.append("## 7. ボーカル" + ("（概算）" if approx else ""))
    L.append("")
    if voc.get("available"):
        r = voc.get("range")
        if r:
            L.append(f"- 音域: 最低 **{r['lowest']}**（{r['lowest_hz']}Hz）〜 最高 **{r['highest']}**（{r['highest_hz']}Hz）、中心 {r['median']}。幅 {r['span_semitones']} 半音")
            L.append(f"- ハイトーン？: {r['high_tone']}")
            L.append("- ※ C4 が真ん中のド。男性はA4、女性はD5くらいから「高い」と感じやすいです。")
        if voc["a_vs_chorus_db"] is not None:
            L.append(f"- Aメロ→サビの声の強さの差: **{voc['a_vs_chorus_db']:+.1f} dB**（3dBで「少し強い」、6dBで「はっきり強い」くらい）")
        L.append(f"- 歌い方: {voc['steadiness']}（セクション間の差 {voc['dynamic_spread_db']} dB）")
        sh = voc["shouts"]
        times = "、".join(fmt_time(x) for x in sh["times"][:10])
        L.append(f"- 掛け声の候補: {sh['count']}か所" + (f"（{times}）" if sh["count"] else "") + f"　※{sh['note']}")
        L.append("")
        L.append("| セクション | 声の強さ(dB) | 歌っている割合 |")
        L.append("|---|---|---|")
        for p in voc["per_section"]:
            v = "—" if p["vocal_db"] is None else f"{p['vocal_db']}"
            L.append(f"| {p['name']}（{fmt_time(p['start'])}） | {v} | {int(p['singing_ratio'] * 100)}% |")
    else:
        L.append(voc.get("reason", "解析できませんでした"))
    L.append("")

    # 雰囲気
    L.append("## 8. 曲の雰囲気")
    L.append("")
    L.append(f"- 明るさ: **{m['tone']}**（マイナーの和音の割合 {int(m['minor_chord_ratio'] * 100)}%）")
    L.append(f"- 盛り上がる場所（上位）: {' / '.join(m['peaks'])}")
    L.append(f"- イントロ→サビ: {m['intro_to_chorus']}")
    L.append(f"- エネルギーのグラフ: `{os.path.basename(res['plot'])}`")
    L.append("")
    L.append("---")
    L.append(f"解析の方法: パート分離 = {'Demucs (htdemucs)' if not approx else 'HPSS＋周波数帯による概算'}、"
             "テンポ・拍 = librosa、キー = Krumhansl のキープロファイル、コード = テンプレート照合＋HMM（前後のつながりで滑らかにする方法）。")
    return "\n".join(L) + "\n"


def to_jsonable(o):
    if isinstance(o, dict):
        return {k: to_jsonable(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return [to_jsonable(v) for v in o.tolist()]
    if isinstance(o, (np.floating,)):
        return round(float(o), 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float):
        return round(o, 4)
    return o


def write_json(res, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_jsonable(res), f, ensure_ascii=False, indent=1)
