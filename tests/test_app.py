# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""rgb_app.pyw's App, Api and control server, loaded with stand-ins for the window, tray and Windows calls (see
app_stub.py). Starts no window, touches no lights and no DeepCool.

  python -m unittest discover -s tests -v
"""
import importlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

importlib.import_module("sdk_stub")  # puts the repo on sys.path, keeps logs out of the checkout
import app_stub

m = app_stub.load()
real_sleep = time.sleep  # some tests replace time.sleep


def bare_app(**state):
    """An App without __init__ (which would read state.json and write beside the checkout)."""
    a = m.App.__new__(m.App)
    a.state = dict({"mode": "static", "color": "0000FF", "groups": {}, "brightness": 100}, **state)
    a.presets = [{"name": "A", "color": "FF0000"}, {"name": "B", "color": "00FF00"}]
    a.profiles = []
    a.window = a.tray = None
    a.screen_busy = False
    a._screen_lock = threading.Lock()
    a._screen_dark = None
    a._screen_gave_up = None
    a.busy_text, a.last_msg, a.last_cooler = "", ["", ""], None
    a.wake_armed_at = 0
    a.client = None
    a.rescan = mock.Mock()
    return a


class StopLoop(Exception):
    pass


def sleeps_then_stops(n):
    """A time.sleep that lets a watcher loop run n rounds and then ends it."""
    left = [n]

    def sleep(_):
        left[0] -= 1
        if left[0] < 0:
            raise StopLoop
    return sleep


class WhitelistTest(unittest.TestCase):
    def test_every_allowed_method_exists(self):
        for name in sorted(m.PHONE_METHODS | m.CONTROL_METHODS):
            self.assertTrue(callable(getattr(m.Api, name, None)), name)


class ApiTest(unittest.TestCase):
    def test_preset_number_out_of_range_is_refused(self):
        a = bare_app()
        a.apply_preset = mock.Mock()
        api = m.Api(a, source="cli")
        for i in (-1, 2, 99):
            self.assertIn("error", api.apply_preset(i))
        a.apply_preset.assert_not_called()
        api.apply_preset(1)
        a.apply_preset.assert_called_once_with(a.presets[1])


class ScreenTest(unittest.TestCase):
    def test_two_callers_cannot_both_start_a_screen_job(self):
        class Slow(m.App):
            _busy = False

            @property
            def screen_busy(self):
                v = self._busy
                real_sleep(0.05)  # widens the gap between looking and setting
                return v

            @screen_busy.setter
            def screen_busy(self, v):
                self._busy = v

        a = bare_app()
        a.__class__ = Slow
        a._busy = False
        a.js = mock.Mock()
        a.sync_screen = mock.Mock()
        a.cooler_info = mock.Mock(return_value={})
        started, release = [], threading.Event()

        def job():
            started.append(1)
            release.wait(5)
        with mock.patch.object(m.time, "sleep"):
            ts = [threading.Thread(target=a.run_screen, args=(job, "busy", "done")) for _ in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            real_sleep(0.2)
            release.set()
        self.assertEqual(len(started), 1)

    def test_no_cooler_no_screen_sync(self):
        a = bare_app(mode="off")
        a.run_screen = mock.Mock()
        with mock.patch.object(m.cooler, "installed", return_value=False), \
                mock.patch.object(m.cooler, "is_blank", side_effect=OSError("no log")) as blank:
            a.sync_screen()
        blank.assert_not_called()
        a.run_screen.assert_not_called()

    def test_picture_chosen_with_lights_off_is_parked_again(self):
        a = bare_app(mode="off")
        a._screen_dark = True          # the cooler shows the black clip
        jobs = []
        a.run_screen = lambda fn, *args, **kw: jobs.append(fn)
        a.set_screen("pic.png", "pic")
        with mock.patch.object(m.cooler, "set_screen"), mock.patch.object(m, "cooler_preview", return_value=("k", "", "")), \
                mock.patch.object(m.rgb, "save_state"):
            jobs.pop()()               # the job: cooler.set_screen put the picture over the black clip
        self.assertFalse(a._screen_dark)
        with mock.patch.object(m.cooler, "installed", return_value=True):
            a.sync_screen(after_job=True)
        self.assertEqual(len(jobs), 1)  # lights are off, so the screen goes dark again


class WatchTest(unittest.TestCase):
    def test_wake_watch_survives_an_error(self):
        a = bare_app(mode="off")
        a.wake_armed_at = time.time() - 60
        a.set_mode = mock.Mock(side_effect=[OSError("state.json locked"), None])
        with mock.patch.object(m, "idle_ms", return_value=0), mock.patch.object(m.time, "sleep", sleeps_then_stops(3)):
            with self.assertRaises(StopLoop):
                a.wake_watch()
        self.assertEqual(a.set_mode.call_count, 1)  # the error was logged and the loop went on to the next round

    def test_idle_watch_survives_an_error(self):
        a = bare_app()
        calls = []

        def idle():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("GetLastInputInfo failed")
            return 0
        with mock.patch.object(m, "idle_seconds", idle), mock.patch.object(m.time, "sleep", sleeps_then_stops(2)):
            with self.assertRaises(StopLoop):
                a.idle_watch()
        self.assertGreaterEqual(len(calls), 2)


class PreviewTest(unittest.TestCase):
    def test_two_threads_asking_at_once_both_get_the_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "cropped-1.mp4")
            with open(src, "wb") as f:
                f.write(b"x" * 1000)
            real_copy = m.shutil.copy2
            both = threading.Barrier(2, timeout=0.5)

            def slow_copy(a, b):
                real_copy(a, b)
                try:
                    both.wait()           # without the lock both copies finish before either renames
                except threading.BrokenBarrierError:
                    pass
            errors, results = [], []

            def ask():
                try:
                    results.append(m.cooler_preview())
                except Exception as e:
                    errors.append(e)
            with mock.patch.object(m, "CACHE", os.path.join(tmp, "cache")), \
                    mock.patch.object(m.cooler, "active_media", return_value=src), \
                    mock.patch.object(m.shutil, "copy2", slow_copy):
                ts = [threading.Thread(target=ask) for _ in range(2)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)


class ControlTest(unittest.TestCase):
    def test_status_failure_answers_500_instead_of_dropping_the_connection(self):
        a = bare_app()
        a.control_token = "c" * 32
        srv = ThreadingHTTPServer(("127.0.0.1", 0), m.ControlHandler)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        a.public_state = mock.Mock(side_effect=RuntimeError("boom"))
        req = urllib.request.Request("http://127.0.0.1:%d/status" % port, headers={"Authorization": "Bearer " + a.control_token})
        with mock.patch.object(m.ControlHandler, "app", a), mock.patch.object(m, "CONTROL_PORT", port):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 500)
        self.assertIn("boom", json.loads(cm.exception.read())["error"])


if __name__ == "__main__":
    unittest.main()
