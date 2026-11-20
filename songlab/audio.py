"""ファイルの確認と、ffmpeg による音声の取り出し。"""

import json
import os
import shutil
import subprocess

SUPPORTED = {".mp4", ".mov", ".m4a", ".mp3", ".wav"}
MAX_FILE_BYTES = 4 * 1024**3        # 4GB
MAX_DURATION_SEC = 20 * 60           # 20分


class UserError(Exception):
    """ユーザーに日本語でそのまま見せるエラー。"""


def probe(path):
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        raise UserError("ffmpeg が見つかりません。Claude に「ffmpeg を入れて」と頼んでください。")
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise UserError(f"ファイルを読めませんでした（壊れているか、対応していない形式です）: {r.stderr.strip()[:200]}")
    info = json.loads(r.stdout or "{}")
    types = [s.get("codec_type") for s in info.get("streams", [])]
    if "audio" not in types:
        raise UserError("このファイルには音声が入っていません。画面収録のときに「マイク／内部音声」がオンになっているか確認してください。")
    try:
        dur = float(info.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        dur = 0.0
    return dur


def check_input(path):
    if not os.path.exists(path):
        raise UserError(f"ファイルが見つかりません: {path}\ninput/ フォルダに置いてから、ファイル名を確認してください。")
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED:
        raise UserError(f"対応していない形式です（{ext}）。mp4 / mov / m4a / mp3 / wav のどれかにしてください。")
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise UserError(
            f"ファイルが大きすぎます（{size / 1024**3:.1f}GB）。"
            "画面収録を曲の部分だけに短く切るか、画質を下げて録り直してください（音だけが必要です）。")
    dur = probe(path)
    if dur > MAX_DURATION_SEC:
        raise UserError(
            f"長すぎます（{dur / 60:.1f}分）。20分以内にしてください。"
            "1曲だけになるように、写真アプリの編集で前後を切ってください。")
    if 0 < dur < 15:
        raise UserError(f"短すぎます（{dur:.1f}秒）。少なくとも30秒以上、できれば1曲まるごと録ってください。")
    return {"size_bytes": size, "duration_sec": dur}


def extract_wav(src, dst, sr=44100):
    """動画・音声から 44.1kHz ステレオの wav を作る。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "2", "-ar", str(sr),
         "-c:a", "pcm_s16le", dst],
        capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        raise UserError(f"音声を取り出せませんでした: {r.stderr.strip()[:300]}")
    return dst
