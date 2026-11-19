#!/usr/bin/env python3
"""テスト用の偽 sunoapi.org サーバー（標準ライブラリのみ）。

挙動はプロンプトやタイトルに含まれる合言葉で切り替える:
  FAIL_SENSITIVE  -> SENSITIVE_WORD_ERROR になる
  NEVER_DONE      -> いつまでも PENDING のまま
  ERR413 / ERR429 / ERR430 / ERR455 -> generate がそのコードを返す
  SNAKE           -> record-info を snake_case で返す
APIキーが "bad-key" なら 401 を返す。
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FAKE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 256

TASKS = {}
LOCK = threading.Lock()
COUNTER = [0]
LAST_GENERATE = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        auth = self.headers.get("Authorization", "")
        if auth == "Bearer bad-key":
            self._json({"code": 401, "msg": "Unauthorized"}, 401)
            return False
        if not auth.startswith("Bearer ") or len(auth) <= 7:
            self._json({"code": 401, "msg": "missing key"}, 401)
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/files/"):
            if u.path.endswith("missing.mp3"):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(FAKE_MP3)))
            self.end_headers()
            self.wfile.write(FAKE_MP3)
            return
        if u.path == "/__last_generate":
            return self._json(LAST_GENERATE)
        if not self._auth_ok():
            return
        if u.path == "/api/v1/generate/credit":
            return self._json({"code": 200, "msg": "success", "data": 123.5})
        if u.path == "/api/v1/generate/record-info":
            tid = parse_qs(u.query).get("taskId", [""])[0]
            with LOCK:
                t = TASKS.get(tid)
                if t is None:
                    return self._json({"code": 404, "msg": "task not found"})
                t["polls"] += 1
                status = self._status(t)
            host = f"http://{self.headers.get('Host')}"
            tracks = []
            if status in ("FIRST_SUCCESS", "SUCCESS"):
                n = 1 if status == "FIRST_SUCCESS" else 2
                for i in range(n):
                    tracks.append({
                        "id": f"{tid}-{i}",
                        "title": t["title"],
                        "audioUrl": f"{host}/files/{tid}-{i}.mp3",
                        "streamAudioUrl": f"{host}/stream/{tid}-{i}",
                        "imageUrl": f"{host}/img/{tid}-{i}.jpg",
                        "duration": 151.4 + i,
                    })
            if t["snake"]:
                data = {"task_id": tid, "status": status,
                        "response": {"suno_data": [
                            {"id": x["id"], "title": x["title"], "audio_url": x["audioUrl"],
                             "stream_audio_url": x["streamAudioUrl"], "image_url": x["imageUrl"],
                             "duration": x["duration"]} for x in tracks]},
                        "error_message": "bad words" if status == "SENSITIVE_WORD_ERROR" else None}
            else:
                data = {"taskId": tid, "status": status, "response": {"sunoData": tracks},
                        "errorMessage": "bad words" if status == "SENSITIVE_WORD_ERROR" else None}
            return self._json({"code": 200, "msg": "success", "data": data})
        self._json({"code": 404, "msg": "not found"}, 404)

    @staticmethod
    def _status(t):
        if t["mode"] == "sensitive":
            return "SENSITIVE_WORD_ERROR" if t["polls"] >= 2 else "PENDING"
        if t["mode"] == "never":
            return "PENDING"
        return ["PENDING", "TEXT_SUCCESS", "FIRST_SUCCESS"][t["polls"] - 1] if t["polls"] <= 3 else "SUCCESS"

    def do_POST(self):
        u = urlparse(self.path)
        if not self._auth_ok():
            return
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path != "/api/v1/generate":
            return self._json({"code": 404, "msg": "not found"}, 404)
        LAST_GENERATE.clear()
        LAST_GENERATE.update(body)
        for k in ("customMode", "instrumental", "model", "callBackUrl"):
            if k not in body:
                return self._json({"code": 400, "msg": f"missing {k}"})
        text = (body.get("prompt") or "") + (body.get("title") or "")
        for c in (413, 429, 430, 455):
            if f"ERR{c}" in text:
                return self._json({"code": c, "msg": "fake error"})
        with LOCK:
            COUNTER[0] += 1
            tid = f"task{COUNTER[0]}"
            TASKS[tid] = {
                "polls": 0,
                "title": body.get("title") or "Auto Song",
                "mode": "sensitive" if "FAIL_SENSITIVE" in text else "never" if "NEVER_DONE" in text else "ok",
                "snake": "SNAKE" in text,
            }
        self._json({"code": 200, "msg": "success", "data": {"taskId": tid}})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(srv.server_address[1], flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
