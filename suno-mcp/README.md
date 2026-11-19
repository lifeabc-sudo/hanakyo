# suno-mcp — Claude Code から Suno で作曲する MCP サーバー

「ラーメンの曲作って」と頼むだけで、**作詞 → Suno で生成 → mp3 保存** までを Claude Code の中で完結させるための MCP サーバーです。
API は [sunoapi.org](https://sunoapi.org) を使います。

- Python 3 の**標準ライブラリだけ**で動きます（`pip install` 不要）
- 通信は stdio 上の JSON-RPC 2.0（MCP）。`initialize` / `tools/list` / `tools/call` / `ping` に対応
- APIキーは環境変数 `SUNO_API_KEY` から読みます（コードには書きません）

## ファイル構成

```
~/suno-mcp/
├── suno_mcp_server.py      # MCP サーバー本体
├── README.md
└── tests/
    ├── fake_suno_api.py    # テスト用の偽 sunoapi.org
    └── test_server.py      # 全ツールのテスト
~/.claude/commands/song.md  # /song スラッシュコマンド
```

## 環境変数

| 変数 | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `SUNO_API_KEY` | ✅ | — | sunoapi.org の APIキー |
| `SUNO_OUTPUT_DIR` | | `~/Music/suno` | mp3 の保存先 |
| `SUNO_API_BASE_URL` | | `https://api.sunoapi.org` | API のベースURL（テスト時に偽サーバーへ向ける等） |

## 登録

```bash
claude mcp add suno --scope user \
  -e SUNO_API_KEY=<あなたのAPIキー> \
  -e SUNO_OUTPUT_DIR="$HOME/Music/suno" \
  -- python3 ~/suno-mcp/suno_mcp_server.py

claude mcp list   # suno: ... - ✓ Connected になればOK
```

APIキーを変えたいときは `claude mcp remove suno --scope user` してから登録し直してください。

## 使い方

### いちばん簡単：`/song`

```
/song ラーメン
```

1. 曲名とジャンルを決める
2. 日本語で歌詞を書く（`[Verse]` `[Chorus]` `[Bridge]` などのタグ付き。既存曲の歌詞は使わない）
3. style を英語で書く（ジャンル、ムード、楽器、ボーカル、BPM）
4. 内容を見せて確認を取る
5. OK が出たら `generate_music` で生成（完成まで数分）
6. 保存先と URL を報告

### 普通に頼む

「ラーメンの曲作って」「さっきの曲の状態見て」「残りクレジットは？」のように話しかければ、Claude が下のツールを使います。

## ツール一覧

### `generate_music` — 曲を生成する
既定では完成まで **8秒ごと** に状態を確認し（最大 **300秒**）、mp3 を `SUNO_OUTPUT_DIR` に `曲名_番号.mp3` で保存します（Suno は1回で2曲作るので `_1` と `_2` ができます）。同名ファイルがあるときは `曲名_1(2).mp3` のように番号を付けて上書きしません。

| 引数 | 説明 |
|---|---|
| `prompt` | カスタムモード：歌詞／おまかせモード：曲のイメージ（3000字まで） |
| `style` | 曲調（カスタムモードで必須） |
| `title` | 曲名・80字まで（カスタムモードで必須） |
| `custom_mode` | 既定 `true`。`false` でおまかせモード |
| `instrumental` | 既定 `false`。`true` で歌なし（このときは歌詞不要） |
| `model` | 既定 `V6` |
| `negative_tags` | 避けたい要素 |
| `vocal_gender` | `m`（男性）/ `f`（女性） |
| `style_weight` / `weirdness_constraint` / `audio_weight` | 0〜1 |
| `wait` | 既定 `true`。`false` だと task_id だけ返してすぐ終わる |
| `download` | 既定 `true`。完成した mp3 を保存するか |
| `max_wait_seconds` / `poll_interval_seconds` | 待ち時間の上限・確認間隔 |
| `output_dir` | 保存先を一時的に変える |

`callBackUrl` は API の必須項目なのでダミーURLを送っています（結果はポーリングで取得します）。

### `wait_for_music` — 完成を待って保存する
`task_id` を指定します。`generate_music` が300秒で終わらなかったときの続きに使います。

### `get_task_status` — 状態だけ確認する
待たずに、今の状態と曲の URL を返します。

| 状態 | 意味 |
|---|---|
| `PENDING` | 生成待ち |
| `TEXT_SUCCESS` | 歌詞の生成完了（音声は作成中） |
| `FIRST_SUCCESS` | 1曲目が完成 |
| `SUCCESS` | 全曲完成 |
| `CREATE_TASK_FAILED` / `GENERATE_AUDIO_FAILED` / `CALLBACK_EXCEPTION` | 失敗 |
| `SENSITIVE_WORD_ERROR` | 使えない言葉が含まれていた |

### `download_track` — URL から mp3 を保存する
`url`（必須）と `filename`（任意）を指定します。

### `get_credits` — 残りクレジットを確認する

## エラー表示

API のエラーは日本語で返します。

| コード | 表示 |
|---|---|
| 401 | APIキーが無効です |
| 413 | 歌詞・スタイル・タイトルなどが長すぎます |
| 429 | クレジットが足りません |
| 430 | リクエストが多すぎます |
| 455 | メンテナンス中です |

## テスト

ローカルに偽の API サーバーを立てて、全ツールを MCP（stdio）経由でテストします。**本物の API は呼びません**（クレジットも減りません）。

```bash
python3 ~/suno-mcp/tests/test_server.py
```

確認している内容：initialize / ping / tools/list / 不正なリクエスト、カスタム・おまかせ・インストの生成と送信パラメーター、mp3 保存とファイル名、同名ファイルの回避、入力チェック、エラーコード（401/413/429/430/455）、APIキー未設定、生成失敗、タイムアウト → 状態確認、wait_for_music、snake_case／camelCase 両対応、download_track、存在しないタスク。

## 手動で動作確認する

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_credits","arguments":{}}}' \
| SUNO_API_KEY=<キー> python3 ~/suno-mcp/suno_mcp_server.py
```

## 注意

- 生成のたびにクレジットを消費します。
- ログは標準エラー出力に出ます（標準出力は MCP 通信専用）。
- 歌詞は必ずオリジナルにしてください。既存曲の歌詞を使うと権利上の問題になります。
