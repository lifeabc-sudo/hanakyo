# hanakyo

## このリポジトリについて
- `analyze.py` と `songlab/`：曲解析ツール。画面収録した動画や音声から曲の特徴を解析し、Suno でオマージュ曲を作るための日本語レポートを出す
- `suno-mcp/`：Suno（sunoapi.org）を Claude Code から使う MCP サーバー（別ツール）

ユーザーはスマホからリモートで指示を出す。プログラミングはしないので、説明は短く、専門用語には一言で説明を付ける。

## 曲解析ツールの使い方（Claude がやること）

ユーザーが「input/◯◯.mp4 を解析して」と言ったら：

1. **準備**（新しいセッションでは毎回確認する）
   - `python3 -c "import librosa"` が失敗したら `pip install --break-system-packages -q -r requirements.txt`
   - `python3 -c "import demucs"` が失敗したら `pip install --break-system-packages -q -r requirements-demucs.txt`（数GBあり数分かかる。バックグラウンドで実行する）
   - `ffmpeg` がなければ `apt-get install -y ffmpeg`
2. **実行**：`python3 analyze.py input/◯◯.mp4`
   - Demucs（音源分離）は CPU だと曲の長さの2〜3倍の時間がかかる（4分の曲で10分前後）。**バックグラウンドで実行して**、終わるのを待つ
   - Demucs が使えない・失敗したときは自動で概算に切り替わる。急ぐときは `--no-demucs` で1分程度
   - Demucs の初回はモデルを `dl.fbaipublicfiles.com` からダウンロードする。ネットワークで止められたら、そのホストの許可が必要
3. **報告**：`reports/◯◯.md` を読み、チャットで要点を返す
   - 冒頭の「3行まとめ」
   - BPM・キー・構成（サビの時刻）・各セクションのコード進行
   - 「Sunoへのヒント」の Style と Exclude Styles は、スマホでコピーしやすいように**それぞれ別のコードブロック**で出す
   - 推定値には「推定」と確からしさを添える。自信がない部分（`?` の付いたコード、概算のパート比率）はそう伝える
4. 結果（`reports/` の md / json / png）は Git にコミットしてよい。`input/`（ユーザーの録音）と `work/`（分離音声など）は大きいのでコミットしない（.gitignore 済み）

## エラーのとき
- `analyze.py` は、ユーザー向けの日本語エラー（音声が入っていない、短すぎる、長すぎる、大きすぎる、形式が違う）をそのまま出す。そのまま伝えて、対処法（録り直し・トリミングなど）を一言添える
- 上限：20分・4GBまで。15秒未満は解析しない

## テスト
- `python3 tests/check_synthetic.py`：答えがわかっている合成音（128 BPM・A minor・構成とコード既知）を作り、mp4 にして解析し、正解と比べる（15項目）
- `python3 tests/check_synthetic.py --no-demucs`：概算モードのテスト
- 合成の声は `espeak-ng` があれば使う（Demucs が歌として認識できるように）。なければ機械的な声になり、Demucs モードのボーカル系のテストは落ちることがある

## 方針
- YouTube などからダウンロードする機能は作らない。ユーザーが用意したファイルだけを扱う
- レポートや Suno 用の Style に、歌手名・作品名は入れない
- 原曲の歌詞は書き写さない
