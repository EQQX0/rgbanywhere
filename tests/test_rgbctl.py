# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""rgbctl against a fake control server (the app's ControlHandler is not started). Changes no lights.

  python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import importlib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

importlib.import_module("sdk_stub")  # puts the repo on sys.path
import rgbctl

TOKEN = "t" * 32


class FakeApp(BaseHTTPRequestHandler):
    calls = []
    answer = None

    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.reply({"error": "unauthorized"}, 401)
        self.reply({"state": {"mode": "static", "color": "0000FF", "groups": {}, "brightness": 80}, "presets": [{"name": "A"}],
                    "profiles": [{"id": "p1"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeApp.calls.append((self.path, body["args"]))
        self.reply({"ok": True, "result": FakeApp.answer})


class RgbctlTest(unittest.TestCase):
    def setUp(self):
        FakeApp.calls, FakeApp.answer = [], None
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeApp)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.token = os.path.join(tmp.name, "control.token")
        with open(self.token, "w") as f:
            f.write(TOKEN)
        env = {"RGBCTL_TOKEN_FILE": self.token, "RGBCTL_PORT": str(self.srv.server_address[1])}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def run_ctl(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            rgbctl.main(list(argv))
        return e.exception.code, json.loads(out.getvalue())

    def test_color_with_target_and_name(self):
        code, res = self.run_ctl("color", "#ff3c00", "--target", "ram")
        self.assertEqual((code, res["ok"]), (0, True))
        self.assertEqual(FakeApp.calls, [("/call/set_color", ["ram", "FF3C00"])])
        self.run_ctl("color", "warm")
        self.assertEqual(FakeApp.calls[-1][1], ["all", "FFB070"])

    def test_bad_input_never_reaches_the_app(self):
        self.assertEqual(self.run_ctl("color", "notacolor")[0], 1)
        self.assertEqual(self.run_ctl("brightness", "3")[0], 1)
        self.assertEqual(self.run_ctl("preset", "-1")[0], 1)  # would pick the last preset if it got through
        self.assertEqual(FakeApp.calls, [])

    def test_original_and_exit_choices(self):
        self.assertEqual(self.run_ctl("original")[0], 0)
        self.assertEqual(FakeApp.calls[-1], ("/call/set_mode", ["original"]))
        self.run_ctl("exit", "fans", "orange")
        self.assertEqual(FakeApp.calls[-1], ("/call/set_exit", ["fans", "FF6000"]))
        self.run_ctl("exit", "all", "effect")
        self.assertEqual(FakeApp.calls[-1], ("/call/set_exit", ["all", "effect"]))
        n = len(FakeApp.calls)
        self.assertEqual(self.run_ctl("exit", "mouse", "sparkly")[0], 1)  # not a colour: refused before the app
        self.assertEqual(len(FakeApp.calls), n)

    def test_refusal_from_the_app(self):
        FakeApp.answer = {"error": "Couldn't turn the monitors off: SendNotifyMessage failed (error 5)"}
        code, res = self.run_ctl("monitors-off")
        self.assertEqual((code, res["ok"]), (1, False))
        self.assertIn("SendNotifyMessage failed", res["error"])

    def test_status_presets_profiles_describe(self):
        self.assertEqual(self.run_ctl("status")[1]["state"]["brightness"], 80)
        self.assertEqual(self.run_ctl("presets")[1]["presets"], [{"number": 0, "name": "A"}])
        self.assertEqual(self.run_ctl("profiles")[1]["profiles"], [{"id": "p1"}])
        self.assertIn("monitors-off", [c["name"] for c in self.run_ctl("describe")[1]["commands"]])

    def test_wrong_token(self):
        with open(self.token, "w") as f:
            f.write("wrong")
        code, res = self.run_ctl("status")
        self.assertEqual(code, 1)
        self.assertIn("401", res["error"])

    def test_app_not_running(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with mock.patch.dict(os.environ, {"RGBCTL_PORT": str(port)}):
            code, res = self.run_ctl("status")
        self.assertEqual(code, 2)
        self.assertIn("not running", res["error"])

    def test_app_that_does_not_answer_is_not_called_not_running(self):
        with socket.socket() as s:  # accepts the connection (the backlog) and never replies
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            with mock.patch.dict(os.environ, {"RGBCTL_PORT": str(s.getsockname()[1])}), mock.patch.object(rgbctl, "TIMEOUT", 0.3):
                code, res = self.run_ctl("status")
        self.assertEqual(code, 1)
        self.assertIn("did not answer", res["error"])

    def test_missing_token_file(self):
        os.remove(self.token)
        self.assertEqual(self.run_ctl("on")[0], 2)


if __name__ == "__main__":
    unittest.main()
