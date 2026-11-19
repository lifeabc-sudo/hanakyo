---
description: テーマから作詞してSunoで曲を生成し、mp3を保存する
argument-hint: <テーマ>（例: ラーメン）
---

テーマ「$ARGUMENTS」で曲を作ります。次の手順で進めてください。

## 1. 曲名とジャンルを決める
- テーマに合う曲名（80文字以内）とジャンルを決める。
- テーマが空なら、何の曲にしたいかをユーザーに聞く。

## 2. 日本語で歌詞を書く
- オリジナルの日本語歌詞を書く。**既存曲の歌詞やフレーズは使わない**。
- `[Intro]` `[Verse]` `[Pre-Chorus]` `[Chorus]` `[Bridge]` `[Outro]` などのセクションタグを付ける。
- 目安は2〜3分の曲（Verse ×2、Chorus ×2〜3、Bridge ×1 程度）。

## 3. style を英語で書く
- ジャンル、ムード、楽器、ボーカル、BPM を英語のカンマ区切りで書く。
  例: `J-pop, upbeat, cheerful, electric guitar, synth, female vocal, 128 BPM`
- 必要なら negative_tags（避けたい要素）と vocal_gender（m / f）も決める。

## 4. 内容を見せて確認を取る
次の形でまとめて表示し、「この内容で生成してよいですか？」と確認する。
**OKが出るまで generate_music は呼ばない。** 修正の依頼があれば直して再確認する。

- 曲名:
- ジャンル:
- style:
- vocal_gender / negative_tags（あれば）:
- 歌詞:

## 5. 生成する
OKが出たら `mcp__suno__generate_music` を次の引数で呼ぶ:
- `custom_mode`: true
- `title`: 曲名
- `prompt`: 歌詞
- `style`: style
- 決めた場合は `vocal_gender`, `negative_tags`

完成まで最大5分ほどかかることを伝えてから呼ぶ。

## 6. 報告する
- 保存した mp3 のパス、audio_url / stream_url、曲の長さを報告する。
- タイムアウトした場合は task_id を伝え、`mcp__suno__wait_for_music` で続きを待つ。
- エラーの場合はその内容を日本語で伝え、対処を提案する。
