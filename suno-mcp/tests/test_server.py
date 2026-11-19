#!/usr/bin/env python3
"""偽APIサーバーを立てて、MCPサーバーの全ツールを stdio 経由でテストする。

実行: python3 ~/suno-mcp/tests/test_server.py
本物の sunoapi.org には一切アクセスしない。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "suno_mcp_server.py")
FAKE = os.path.join(HERE, "fake_suno_api.py")


class MCPClient:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=env, text=True, encoding="utf-8")
        self.next_id = 0

    def send_raw(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))
        return json.loads(self.p.stdout.readline())

    def notify(self, method):
        self.send_raw(json.dumps({"jsonrpc": "2.0", "method": method}))

    def call(self, name, args=None):
        r = self.request("tools/call", {"name": name, "arguments": args or {}})
        res = r["result"]
        return res["content"][0]["text"], res["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)
        self.p.stdout.close()


class TestSunoMCP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = subprocess.Popen([sys.executable, FAKE], stdout=subprocess.PIPE, text=True)
        cls.port = int(cls.fake.stdout.readline())
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "music")
        env = dict(os.environ, SUNO_API_KEY="test-key", SUNO_API_BASE_URL=cls.base,
                   SUNO_OUTPUT_DIR=cls.out)
        env.pop("HTTPS_PROXY", None); env.pop("https_proxy", None)
        env.pop("HTTP_PROXY", None); env.pop("http_proxy", None)
        env["NO_PROXY"] = "127.0.0.1,localhost"
        cls.env = env
        cls.c = MCPClient(env)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.fake.terminate()
        cls.fake.wait()
        cls.fake.stdout.close()
        cls.tmp.cleanup()

    def last_generate(self):
        with urllib.request.urlopen(self.base + "/__last_generate") as r:
            return json.loads(r.read())

    # --- protocol
    def test_01_initialize_and_ping(self):
        r = self.c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "0"}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "suno")
        self.assertIn("tools", r["result"]["capabilities"])
        self.c.notify("notifications/initialized")  # 返事が来ないこと
        r = self.c.request("ping")
        self.assertEqual(r["result"], {})

    def test_02_tools_list(self):
        r = self.c.request("tools/list")
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(names, {"generate_music", "wait_for_music", "get_task_status",
                                 "download_track", "get_credits"})

    def test_03_unknown_method_and_parse_error(self):
        r = self.c.request("foo/bar")
        self.assertEqual(r["error"]["code"], -32601)
        self.c.send_raw("{not json")
        r = json.loads(self.c.p.stdout.readline())
        self.assertEqual(r["error"]["code"], -32700)

    # --- tools
    def test_10_get_credits(self):
        text, err = self.c.call("get_credits")
        self.assertFalse(err)
        self.assertIn("123.5", text)

    def test_11_generate_custom_and_save(self):
        text, err = self.c.call("generate_music", {
            "title": "ラーメン/讃歌", "prompt": "[Verse]\n麺をすする\n[Chorus]\nラーメン",
            "style": "J-rock, 140 BPM", "vocal_gender": "m", "negative_tags": "metal",
            "style_weight": 0.7, "weirdness_constraint": 0.3, "audio_weight": 0.5,
            "poll_interval_seconds": 1})
        self.assertFalse(err, text)
        self.assertIn("曲ができました", text)
        body = self.last_generate()
        self.assertEqual(body["customMode"], True)
        self.assertEqual(body["instrumental"], False)
        self.assertEqual(body["model"], "V6")
        self.assertTrue(body["callBackUrl"].startswith("https://"))
        self.assertEqual(body["vocalGender"], "m")
        self.assertEqual(body["negativeTags"], "metal")
        self.assertEqual(body["styleWeight"], 0.7)
        self.assertEqual(body["weirdnessConstraint"], 0.3)
        self.assertEqual(body["audioWeight"], 0.5)
        files = sorted(os.listdir(self.out))
        self.assertIn("ラーメン_讃歌_1.mp3", files)
        self.assertIn("ラーメン_讃歌_2.mp3", files)
        with open(os.path.join(self.out, "ラーメン_讃歌_1.mp3"), "rb") as f:
            self.assertTrue(f.read().startswith(b"ID3"))

    def test_12_generate_same_title_not_overwritten(self):
        args = {"title": "重複", "prompt": "歌詞", "style": "pop", "poll_interval_seconds": 1}
        self.c.call("generate_music", args)
        self.c.call("generate_music", args)
        files = os.listdir(self.out)
        self.assertIn("重複_1.mp3", files)
        self.assertIn("重複_1(2).mp3", files)

    def test_13_generate_simple_mode(self):
        text, err = self.c.call("generate_music", {
            "custom_mode": False, "prompt": "とんこつラーメンの元気な歌", "wait": False})
        self.assertFalse(err, text)
        self.assertIn("task_id: task", text)
        body = self.last_generate()
        self.assertEqual(body["customMode"], False)
        self.assertNotIn("style", body)

    def test_14_generate_instrumental_without_lyrics(self):
        text, err = self.c.call("generate_music", {
            "title": "Inst", "prompt": "", "style": "lofi", "instrumental": True,
            "poll_interval_seconds": 1})
        self.assertFalse(err, text)
        self.assertTrue(self.last_generate()["instrumental"])

    def test_15_validation(self):
        cases = [
            ({"prompt": "x", "title": "t"}, "style"),
            ({"prompt": "x", "style": "s"}, "title"),
            ({"prompt": "x", "style": "s", "title": "t" * 81}, "80"),
            ({"prompt": "x", "style": "s", "title": "t", "style_weight": 1.5}, "0〜1"),
            ({"prompt": "x", "style": "s", "title": "t", "vocal_gender": "x"}, "vocal_gender"),
            ({"custom_mode": False, "prompt": "あ" * 3001}, "3000"),
        ]
        for args, word in cases:
            text, err = self.c.call("generate_music", args)
            self.assertTrue(err, args)
            self.assertIn(word, text)

    def test_16_api_error_codes(self):
        expected = {413: "長すぎ", 429: "クレジット", 430: "多すぎ", 455: "メンテナンス"}
        for code, word in expected.items():
            text, err = self.c.call("generate_music", {"title": f"ERR{code}", "prompt": "x", "style": "s"})
            self.assertTrue(err)
            self.assertIn(word, text)
            self.assertIn(str(code), text)

    def test_17_invalid_key(self):
        c = MCPClient(dict(self.env, SUNO_API_KEY="bad-key"))
        try:
            text, err = c.call("get_credits")
            self.assertTrue(err)
            self.assertIn("APIキーが無効", text)
        finally:
            c.close()

    def test_18_missing_key(self):
        env = dict(self.env)
        env.pop("SUNO_API_KEY")
        c = MCPClient(env)
        try:
            text, err = c.call("get_credits")
            self.assertTrue(err)
            self.assertIn("SUNO_API_KEY", text)
        finally:
            c.close()

    def test_19_sensitive_failure(self):
        text, err = self.c.call("generate_music", {
            "title": "x", "prompt": "FAIL_SENSITIVE", "style": "s", "poll_interval_seconds": 1})
        self.assertTrue(err)
        self.assertIn("使えない言葉", text)

    def test_20_timeout_then_status(self):
        text, err = self.c.call("generate_music", {
            "title": "x", "prompt": "NEVER_DONE", "style": "s",
            "poll_interval_seconds": 1, "max_wait_seconds": 2})
        self.assertFalse(err)
        self.assertIn("まだ完成していません", text)
        self.assertIn("wait_for_music", text)
        tid = text.split("task_id: ")[1].split()[0]
        text, err = self.c.call("get_task_status", {"task_id": tid})
        self.assertFalse(err)
        self.assertIn("PENDING", text)

    def test_21_wait_for_music(self):
        text, _ = self.c.call("generate_music", {"title": "待つ曲", "prompt": "x", "style": "s", "wait": False})
        tid = text.split("task_id: ")[1].split()[0]
        text, err = self.c.call("get_task_status", {"task_id": tid})
        self.assertIn("PENDING", text)
        text, err = self.c.call("wait_for_music", {"task_id": tid, "poll_interval_seconds": 1})
        self.assertFalse(err, text)
        self.assertIn("SUCCESS", text)
        self.assertIn("待つ曲_1.mp3", text)
        self.assertTrue(os.path.exists(os.path.join(self.out, "待つ曲_2.mp3")))

    def test_22_snake_case_response(self):
        text, err = self.c.call("generate_music", {
            "title": "SNAKE曲", "prompt": "x", "style": "s", "poll_interval_seconds": 1})
        self.assertFalse(err, text)
        self.assertTrue(os.path.exists(os.path.join(self.out, "SNAKE曲_1.mp3")))
        self.assertIn("2:31", text)

    def test_23_download_track(self):
        text, err = self.c.call("download_track", {"url": self.base + "/files/abc.mp3", "filename": "手動保存"})
        self.assertFalse(err, text)
        self.assertTrue(os.path.exists(os.path.join(self.out, "手動保存.mp3")))
        text, err = self.c.call("download_track", {"url": self.base + "/files/missing.mp3"})
        self.assertTrue(err)
        self.assertIn("404", text)
        text, err = self.c.call("download_track", {"url": "file:///etc/passwd"})
        self.assertTrue(err)

    def test_24_task_not_found(self):
        text, err = self.c.call("get_task_status", {"task_id": "nope"})
        self.assertTrue(err)
        self.assertIn("見つかりません", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
