# hanakyo

## 曲解析ツール（analyze.py）

スマホで画面収録した動画（曲を流したもの）から音声を取り出して、曲の特徴を数値で解析し、日本語のレポートを出します。Suno でオマージュ曲を作るときの資料用です。

### 使い方（スマホから）
1. 画面収録した動画を `input/` フォルダに置く（mp4 / mov / m4a / mp3 / wav）
2. Claude に「**input/◯◯.mp4 を解析して**」と頼む
3. 数分〜10分ほどで、要点と Suno 用の Style がチャットに返ってきます

### わかること
| 項目 | 内容 |
|---|---|
| 基本情報 | 長さ、BPM（テンポ）、拍子、キー（候補3つ） |
| 構成 | イントロ / Aメロ / Bメロ / サビ / 間奏 / アウトロ の時刻 |
| コード進行 | セクションごと・小節ごと。自信がないところには `?` |
| 楽器のバランス | 歌・ドラム・ベース・上物の音量の割合（セクションごと） |
| 音色 | 歪みかクリーンか、明るさ、カッティングの有無（イントロ20秒は細かく） |
| シンセ | 伸ばす音（パッド・ブラス）か、アルペジオか |
| ボーカル | 音域、ハイトーンか、Aメロとサビの声の強さの差、掛け声の候補 |
| 雰囲気 | 明るい／暗い、盛り上がる場所、イントロからサビへの入り方 |
| Sunoへのヒント | Style（英語・3〜4軸）と Exclude Styles の候補 |

すべて機械による**推定**です。レポートには確からしさも書いてあります。

### 出力
- `reports/<名前>.md`：レポート（冒頭に3行まとめ）
- `reports/<名前>.json`：数値データ
- `reports/<名前>_energy.png`：盛り上がりとパート別音量のグラフ
- `work/<名前>/`：取り出した音声・分離した音声（Gitには入れません）

### 自分で動かす場合
```bash
pip install -r requirements.txt            # 基本（必須）
pip install -r requirements-demucs.txt     # 音源分離（任意・大きい）
python3 analyze.py input/曲.mp4
python3 analyze.py input/曲.mp4 --no-demucs  # 音源分離なしで速く概算
```
ffmpeg が必要です。

### テスト
```bash
python3 tests/check_synthetic.py
```
答えがわかっている合成音（128 BPM・A minor・コードと構成が既知）を作って解析し、15項目で正解と比べます。

## suno-mcp
Suno（sunoapi.org）を Claude Code から使う MCP サーバーです。→ [suno-mcp/README.md](suno-mcp/README.md)
