#!/usr/bin/env python3
"""Suno AI作曲 MCPサーバー (sunoapi.org)

標準ライブラリのみ。stdio 上の JSON-RPC 2.0 (MCP) で動作する。

環境変数:
  SUNO_API_KEY       必須。sunoapi.org の APIキー
  SUNO_API_BASE_URL  任意。既定 https://api.sunoapi.org
  SUNO_OUTPUT_DIR    任意。mp3の保存先。既定 ~/Music/suno
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER_NAME = "suno"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

DEFAULT_BASE_URL = "https://api.sunoapi.org"
DEFAULT_MODEL = "V6"
DUMMY_CALLBACK_URL = "https://example.com/suno-callback"
POLL_INTERVAL = 8
MAX_WAIT = 300
HTTP_TIMEOUT = 60
USER_AGENT = "suno-mcp/1.0"

SUCCESS_STATUSES = {"SUCCESS"}
PARTIAL_STATUSES = {"TEXT_SUCCESS", "FIRST_SUCCESS"}
FAILED_STATUSES = {
    "CREATE_TASK_FAILED",
    "GENERATE_AUDIO_FAILED",
    "CALLBACK_EXCEPTION",
    "SENSITIVE_WORD_ERROR",
}

STATUS_JA = {
    "PENDING": "生成待ち",
    "TEXT_SUCCESS": "歌詞の生成完了（音声は作成中）",
    "FIRST_SUCCESS": "1曲目が完成（2曲目を作成中）",
    "SUCCESS": "全曲完成",
    "CREATE_TASK_FAILED": "タスクの作成に失敗しました",
    "GENERATE_AUDIO_FAILED": "音声の生成に失敗しました",
    "CALLBACK_EXCEPTION": "コールバック処理でエラーが起きました",
    "SENSITIVE_WORD_ERROR": "歌詞・スタイルに使えない言葉が含まれていました",
}

ERROR_JA = {
    400: "リクエストの内容が正しくありません（パラメーターを確認してください）",
    401: "APIキーが無効です。SUNO_API_KEY を確認してください",
    404: "指定したタスクやURLが見つかりません",
    405: "リクエスト回数の上限に達しました。しばらく待ってから試してください",
    413: "歌詞・スタイル・タイトルなどが長すぎます。短くしてください",
    429: "クレジットが足りません。sunoapi.org でクレジットを追加してください",
    430: "リクエストが多すぎます。少し時間をおいてから試してください",
    455: "sunoapi.org がメンテナンス中です。しばらくしてから試してください",
    500: "sunoapi.org のサーバーでエラーが起きました",
}


class SunoError(Exception):
    pass


# ---------------------------------------------------------------- utilities

def log(*args):
    print("[suno-mcp]", *args, file=sys.stderr, flush=True)


def get_api_key():
    key = os.environ.get("SUNO_API_KEY", "").strip()
    if not key:
        raise SunoError("環境変数 SUNO_API_KEY が設定されていません")
    return key


def get_base_url():
    return (os.environ.get("SUNO_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def get_output_dir(override=None):
    d = override or os.environ.get("SUNO_OUTPUT_DIR") or "~/Music/suno"
    return os.path.abspath(os.path.expanduser(os.path.expandvars(d)))


def pick(d, *names, default=None):
    """snake_case / camelCase のどちらのキーでも値を取り出す。"""
    if not isinstance(d, dict):
        return default
    for name in names:
        variants = {name}
        if "_" in name:
            parts = name.split("_")
            variants.add(parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:]))
        else:
            variants.add(re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), name))
        for v in variants:
            if v in d and d[v] not in (None, ""):
                return d[v]
    return default


def error_message(code, msg=None):
    base = ERROR_JA.get(code)
    if base is None:
        base = f"エラーが起きました（コード {code}）"
    if msg and str(msg) not in base:
        return f"{base}（code={code}: {msg}）"
    return f"{base}（code={code}）"


def safe_filename(name, max_len=80):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "")).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return (name or "suno")[:max_len]


# ---------------------------------------------------------------- HTTP

def api_request(method, path, params=None, body=None):
    url = get_base_url() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": "Bearer " + get_api_key(),
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            j = json.loads(raw.decode("utf-8"))
            code = j.get("code", e.code)
            msg = j.get("msg") or j.get("message")
        except Exception:
            code, msg = e.code, e.reason
        raise SunoError(error_message(int(code) if str(code).isdigit() else e.code, msg))
    except urllib.error.URLError as e:
        raise SunoError(f"sunoapi.org に接続できませんでした: {e.reason}")
    except TimeoutError:
        raise SunoError("sunoapi.org からの応答がタイムアウトしました")

    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception:
        raise SunoError("sunoapi.org から想定外の応答が返りました（JSONではありません）")
    code = j.get("code") if isinstance(j, dict) else None
    if code is not None and code != 200:
        try:
            code = int(code)
        except (TypeError, ValueError):
            pass
        raise SunoError(error_message(code, j.get("msg") or j.get("message")))
    return j


def download_file(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = path + ".part"
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, path)
    except urllib.error.HTTPError as e:
        _cleanup(tmp)
        raise SunoError(f"ダウンロードに失敗しました（HTTP {e.code}）: {url}")
    except urllib.error.URLError as e:
        _cleanup(tmp)
        raise SunoError(f"ダウンロード先に接続できませんでした: {e.reason}")
    except OSError as e:
        _cleanup(tmp)
        raise SunoError(f"ファイルを保存できませんでした: {e}")
    return path


def _cleanup(p):
    try:
        os.remove(p)
    except OSError:
        pass


def unique_path(directory, base, ext=".mp3"):
    os.makedirs(directory, exist_ok=True)
    p = os.path.join(directory, base + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(directory, f"{base}({n}){ext}")
        n += 1
    return p


# ---------------------------------------------------------------- Suno API

def fetch_record(task_id):
    j = api_request("GET", "/api/v1/generate/record-info", params={"taskId": task_id})
    data = j.get("data") or {}
    status = pick(data, "status", default="PENDING")
    response = pick(data, "response", default={}) or {}
    tracks_raw = pick(response, "suno_data", default=None)
    if tracks_raw is None:
        tracks_raw = pick(data, "suno_data", default=[]) or []
    tracks = []
    for t in tracks_raw or []:
        tracks.append({
            "id": pick(t, "id"),
            "title": pick(t, "title"),
            "audio_url": pick(t, "audio_url", "source_audio_url"),
            "stream_audio_url": pick(t, "stream_audio_url", "source_stream_audio_url"),
            "image_url": pick(t, "image_url", "source_image_url"),
            "duration": pick(t, "duration"),
            "tags": pick(t, "tags"),
        })
    return {
        "task_id": pick(data, "task_id", default=task_id),
        "status": status,
        "status_ja": STATUS_JA.get(status, status),
        "error_message": pick(data, "error_message"),
        "error_code": pick(data, "error_code"),
        "tracks": tracks,
    }


def save_tracks(record, output_dir=None, fallback_title=None):
    out = get_output_dir(output_dir)
    saved = []
    for i, t in enumerate(record["tracks"], 1):
        url = t.get("audio_url")
        if not url:
            t["saved_path"] = None
            t["save_note"] = "audio_url がまだありません"
            continue
        base = f"{safe_filename(t.get('title') or fallback_title or 'suno')}_{i}"
        path = unique_path(out, base)
        try:
            download_file(url, path)
            t["saved_path"] = path
            saved.append(path)
        except SunoError as e:
            t["saved_path"] = None
            t["save_note"] = str(e)
    return saved


def wait_and_save(task_id, max_wait=MAX_WAIT, poll_interval=POLL_INTERVAL,
                  download=True, output_dir=None, fallback_title=None):
    start = time.monotonic()
    max_wait = max(0, min(float(max_wait), 3600))
    poll_interval = max(1, float(poll_interval))
    while True:
        rec = fetch_record(task_id)
        status = rec["status"]
        if status in FAILED_STATUSES:
            msg = rec["status_ja"]
            if rec.get("error_message"):
                msg += f"（{rec['error_message']}）"
            raise SunoError(f"曲の生成に失敗しました: {msg}  task_id={task_id}")
        if status in SUCCESS_STATUSES:
            break
        elapsed = time.monotonic() - start
        if elapsed + poll_interval > max_wait:
            rec["timed_out"] = True
            rec["elapsed_seconds"] = round(elapsed)
            return rec
        log(f"task {task_id}: {status} ({round(elapsed)}秒経過)")
        time.sleep(poll_interval)
    rec["timed_out"] = False
    rec["elapsed_seconds"] = round(time.monotonic() - start)
    if download:
        rec["saved_files"] = save_tracks(rec, output_dir, fallback_title)
    return rec


def fmt_duration(sec):
    try:
        s = int(round(float(sec)))
        return f"{s // 60}:{s % 60:02d}"
    except (TypeError, ValueError):
        return "?"


def format_record(rec, header=None):
    lines = []
    if header:
        lines.append(header)
    lines.append(f"task_id: {rec['task_id']}")
    lines.append(f"状態: {rec['status']}（{rec['status_ja']}）")
    if rec.get("timed_out"):
        lines.append(f"※ {rec.get('elapsed_seconds')}秒待ちましたが、まだ完成していません。"
                     f"あとで wait_for_music に task_id を渡して続きを待てます。")
    for i, t in enumerate(rec.get("tracks", []), 1):
        lines.append("")
        lines.append(f"[{i}] {t.get('title') or '(無題)'}  長さ {fmt_duration(t.get('duration'))}")
        if t.get("saved_path"):
            lines.append(f"  保存先: {t['saved_path']}")
        elif t.get("save_note"):
            lines.append(f"  保存できませんでした: {t['save_note']}")
        if t.get("audio_url"):
            lines.append(f"  audio_url: {t['audio_url']}")
        if t.get("stream_audio_url"):
            lines.append(f"  stream_url: {t['stream_audio_url']}")
        if t.get("image_url"):
            lines.append(f"  画像: {t['image_url']}")
        if t.get("id"):
            lines.append(f"  id: {t['id']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- tools

def _bool(v, default):
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _weight(args, name):
    v = args.get(name)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SunoError(f"{name} は 0〜1 の数値で指定してください")
    if not 0 <= f <= 1:
        raise SunoError(f"{name} は 0〜1 の範囲で指定してください（指定値: {v}）")
    return round(f, 2)


def tool_generate_music(args):
    custom = _bool(args.get("custom_mode"), True)
    instrumental = _bool(args.get("instrumental"), False)
    model = args.get("model") or DEFAULT_MODEL
    prompt = (args.get("prompt") or "").strip()
    body = {
        "customMode": custom,
        "instrumental": instrumental,
        "model": model,
        "callBackUrl": args.get("callback_url") or DUMMY_CALLBACK_URL,
    }
    title = (args.get("title") or "").strip()
    if custom:
        style = (args.get("style") or "").strip()
        if not style:
            raise SunoError("カスタムモードでは style（曲調）が必要です")
        if not title:
            raise SunoError("カスタムモードでは title（曲名）が必要です")
        if len(title) > 80:
            raise SunoError(f"title は80文字までです（現在 {len(title)} 文字）")
        if not instrumental and not prompt:
            raise SunoError("歌ありのカスタムモードでは prompt（歌詞）が必要です")
        body["style"] = style
        body["title"] = title
        if prompt:
            body["prompt"] = prompt
        if args.get("negative_tags"):
            body["negativeTags"] = str(args["negative_tags"])
        vg = args.get("vocal_gender")
        if vg:
            vg = str(vg).lower()
            if vg not in ("m", "f"):
                raise SunoError('vocal_gender は "m" か "f" で指定してください')
            body["vocalGender"] = vg
        for src, dst in (("style_weight", "styleWeight"),
                         ("weirdness_constraint", "weirdnessConstraint"),
                         ("audio_weight", "audioWeight")):
            w = _weight(args, src)
            if w is not None:
                body[dst] = w
    else:
        if not prompt:
            raise SunoError("おまかせモードでは prompt（曲のイメージ）が必要です")
        if len(prompt) > 3000:
            raise SunoError(f"おまかせモードの prompt は3000文字までです（現在 {len(prompt)} 文字）")
        body["prompt"] = prompt

    j = api_request("POST", "/api/v1/generate", body=body)
    task_id = pick(j.get("data") or {}, "task_id")
    if not task_id:
        raise SunoError(f"taskId が返ってきませんでした: {json.dumps(j, ensure_ascii=False)[:300]}")

    if not _bool(args.get("wait"), True):
        return (f"生成を開始しました。\ntask_id: {task_id}\n"
                f"完成を待つには wait_for_music、状態だけ見るには get_task_status を使ってください。")

    rec = wait_and_save(
        task_id,
        max_wait=args.get("max_wait_seconds", MAX_WAIT),
        poll_interval=args.get("poll_interval_seconds", POLL_INTERVAL),
        download=_bool(args.get("download"), True),
        output_dir=args.get("output_dir"),
        fallback_title=title or None,
    )
    header = "曲ができました！" if not rec.get("timed_out") else "生成中です（まだ完成していません）"
    return format_record(rec, header)


def tool_wait_for_music(args):
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        raise SunoError("task_id を指定してください")
    rec = wait_and_save(
        task_id,
        max_wait=args.get("max_wait_seconds", MAX_WAIT),
        poll_interval=args.get("poll_interval_seconds", POLL_INTERVAL),
        download=_bool(args.get("download"), True),
        output_dir=args.get("output_dir"),
    )
    header = "曲ができました！" if not rec.get("timed_out") else "生成中です（まだ完成していません）"
    return format_record(rec, header)


def tool_get_task_status(args):
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        raise SunoError("task_id を指定してください")
    rec = fetch_record(task_id)
    text = format_record(rec)
    if rec["status"] in FAILED_STATUSES and rec.get("error_message"):
        text += f"\nエラー内容: {rec['error_message']}"
    return text


def tool_download_track(args):
    url = (args.get("url") or "").strip()
    if not url:
        raise SunoError("url を指定してください")
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise SunoError("url は http:// または https:// で始まる必要があります")
    name = args.get("filename")
    if name:
        base = safe_filename(re.sub(r"\.mp3$", "", str(name), flags=re.I))
    else:
        base = safe_filename(os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0] or "suno")
    path = unique_path(get_output_dir(args.get("output_dir")), base)
    download_file(url, path)
    size = os.path.getsize(path)
    return f"保存しました: {path}（{size / 1024 / 1024:.2f} MB）"


def tool_get_credits(args):
    j = api_request("GET", "/api/v1/generate/credit")
    data = j.get("data")
    if isinstance(data, dict):
        credits = pick(data, "credits", "credit", "remaining_credits", default=data)
    else:
        credits = data
    return f"残りクレジット: {credits}"


TOOLS = [
    {
        "name": "generate_music",
        "description": (
            "Suno で曲を生成する。既定では完成まで8秒ごとに確認し（最大300秒）、"
            "mp3 を SUNO_OUTPUT_DIR（既定 ~/Music/suno）に「曲名_番号.mp3」で保存する。"
            "カスタムモード（既定）は prompt=歌詞, style, title が必要。"
            "おまかせモード（custom_mode=false）は prompt=曲のイメージ だけでよい。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "カスタムモード: 歌詞（[Verse][Chorus]等のタグ付き）。おまかせモード: 曲のイメージ（3000字まで）"},
                "style": {"type": "string", "description": "曲調（英語推奨。ジャンル、ムード、楽器、ボーカル、BPM）。カスタムモードで必須"},
                "title": {"type": "string", "description": "曲名（80字まで）。カスタムモードで必須"},
                "custom_mode": {"type": "boolean", "default": True, "description": "true=カスタムモード、false=おまかせモード"},
                "instrumental": {"type": "boolean", "default": False, "description": "true でインスト（歌なし）"},
                "model": {"type": "string", "default": DEFAULT_MODEL, "description": "モデル（例: V6, V5, V4_5PLUS, V4_5, V4）"},
                "negative_tags": {"type": "string", "description": "避けたい要素（例: Heavy Metal, Screaming）"},
                "vocal_gender": {"type": "string", "enum": ["m", "f"], "description": "ボーカルの性別 m=男性, f=女性"},
                "style_weight": {"type": "number", "minimum": 0, "maximum": 1, "description": "style をどれだけ守るか（0〜1）"},
                "weirdness_constraint": {"type": "number", "minimum": 0, "maximum": 1, "description": "実験的な度合いの制約（0〜1）"},
                "audio_weight": {"type": "number", "minimum": 0, "maximum": 1, "description": "音声の重み（0〜1）"},
                "wait": {"type": "boolean", "default": True, "description": "false にすると完成を待たずに task_id だけ返す"},
                "download": {"type": "boolean", "default": True, "description": "完成した mp3 を保存するか"},
                "max_wait_seconds": {"type": "number", "default": MAX_WAIT, "description": "最大待ち時間（秒）"},
                "poll_interval_seconds": {"type": "number", "default": POLL_INTERVAL, "description": "確認間隔（秒）"},
                "output_dir": {"type": "string", "description": "保存先（省略時は SUNO_OUTPUT_DIR）"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "wait_for_music",
        "description": "task_id を指定して完成を待ち、mp3 を保存する（generate_music がタイムアウトした時の続きなど）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "generate_music が返した task_id"},
                "max_wait_seconds": {"type": "number", "default": MAX_WAIT},
                "poll_interval_seconds": {"type": "number", "default": POLL_INTERVAL},
                "download": {"type": "boolean", "default": True},
                "output_dir": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_task_status",
        "description": "待たずに生成タスクの状態と曲のURLだけを確認する。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "download_track",
        "description": "URL を指定して mp3 を SUNO_OUTPUT_DIR に保存する。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "mp3 の URL（audio_url など）"},
                "filename": {"type": "string", "description": "保存するファイル名（拡張子不要）"},
                "output_dir": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_credits",
        "description": "sunoapi.org の残りクレジットを確認する。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

HANDLERS = {
    "generate_music": tool_generate_music,
    "wait_for_music": tool_wait_for_music,
    "get_task_status": tool_get_task_status,
    "download_track": tool_download_track,
    "get_credits": tool_get_credits,
}


# ---------------------------------------------------------------- JSON-RPC

def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(id_, res):
    send({"jsonrpc": "2.0", "id": id_, "result": res})


def error(id_, code, message):
    send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def handle(msg):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        return
    method = msg.get("method")
    id_ = msg.get("id")
    is_notification = "id" not in msg
    params = msg.get("params") or {}

    if method is None:
        return  # クライアントからのレスポンスは無視
    if is_notification:
        return  # notifications/initialized など

    if method == "initialize":
        result(id_, {
            "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Suno (sunoapi.org) で作曲する。/song コマンドから使うと便利。",
        })
    elif method == "ping":
        result(id_, {})
    elif method == "tools/list":
        result(id_, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            error(id_, -32602, f"Unknown tool: {name}")
            return
        try:
            text = fn(args)
            result(id_, {"content": [{"type": "text", "text": text}], "isError": False})
        except SunoError as e:
            result(id_, {"content": [{"type": "text", "text": f"エラー: {e}"}], "isError": True})
        except Exception as e:  # 想定外のエラーもツールエラーとして返す
            log("unexpected error:", repr(e))
            result(id_, {"content": [{"type": "text", "text": f"想定外のエラー: {e!r}"}], "isError": True})
    elif method in ("resources/list",):
        result(id_, {"resources": []})
    elif method in ("prompts/list",):
        result(id_, {"prompts": []})
    else:
        error(id_, -32601, f"Method not found: {method}")


def main():
    log(f"started (base={get_base_url()}, output={get_output_dir()})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            error(None, -32700, "Parse error")
            continue
        if isinstance(msg, list):
            for m in msg:
                handle(m)
        else:
            handle(msg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
