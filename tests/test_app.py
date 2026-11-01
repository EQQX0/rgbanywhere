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

    def test_updates_stay_on_the_desktop(self):
        """A phone or the control port must not be able to start a download and run Setup."""
        for name in ("check_update", "install_update", "open_release_notes"):
            self.assertNotIn(name, m.PHONE_METHODS | m.CONTROL_METHODS)


def update_app(available=True):
    a = bare_app()
    a.update_info = {"current": "0.2.1", "latest": "0.3.0", "available": available,
                     "release": {"version": "0.3.0", "page": "", "url": "https://example.invalid/s.exe",
                                 "size": 9, "sha256": "ab" * 32}}
    a.update_checked = time.time()
    a.update_told = None
    a._update_lock = threading.Lock()
    a._install_lock = threading.Lock()
    a.js = mock.Mock()
    return a


class UpdateTest(unittest.TestCase):
    def test_nothing_to_install(self):
        a = update_app(available=False)
        self.assertIn("error", a.install_update())

    def test_second_click_while_downloading_is_ignored(self):
        a = update_app()
        release = threading.Event()
        with mock.patch.object(m.updater, "download", side_effect=lambda *_: release.wait(5) and "C:/x/Setup.exe"), \
                mock.patch.object(m.subprocess, "Popen") as popen:
            self.assertEqual(a.install_update(), {"started": True})
            self.assertEqual(a.install_update(), {"busy": True})
            release.set()
            for _ in range(100):
                if not a._install_lock.locked():
                    break
                real_sleep(0.02)
        popen.assert_called_once()
        a.js.assert_called_with("rgbUpdate", {"phase": "setup"})

    def test_bad_checksum_reaches_the_page(self):
        a = update_app()
        with mock.patch.object(m.updater, "download", side_effect=ValueError("the download did not match")), \
                mock.patch.object(m.subprocess, "Popen") as popen:
            a.install_update()
            for _ in range(100):
                if not a._install_lock.locked():
                    break
                real_sleep(0.02)
        popen.assert_not_called()
        a.js.assert_called_with("rgbUpdate", {"phase": "error", "error": "the download did not match"})

    def test_answer_is_reused_until_forced(self):
        a = update_app()
        with mock.patch.object(m.updater, "check", return_value=dict(a.update_info, latest="0.3.1")) as check:
            self.assertEqual(a.check_update()["latest"], "0.3.0")
            check.assert_not_called()
            self.assertEqual(a.check_update(force=True)["latest"], "0.3.1")
            check.assert_called_once()

    def test_offline_check_says_so(self):
        a = update_app()
        with mock.patch.object(m.updater, "check", side_effect=OSError("no route")):
            view = a.check_update(force=True)
        self.assertIn("error", view)
        self.assertTrue(view["available"])  # what it knew before still stands

    def test_release_notes_open_only_this_project(self):
        a = update_app()
        a.update_info["release"]["page"] = "https://evil.invalid/"
        with mock.patch.object(m.os, "startfile", create=True) as startfile:
            m.Api(a).open_release_notes()
        startfile.assert_called_once_with(m.updater.PAGES + "latest")


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


    def test_exit_choices_are_checked_and_saved(self):
        a = bare_app()
        a.commit = mock.Mock()
        api = m.Api(a, source="cli")
        self.assertEqual(api.set_exit("ram", "effect"), {"ram": "effect"})
        self.assertEqual(api.set_exit("fans", "#ff6000"), {"ram": "effect", "fans": "FF6000"})
        self.assertIn("error", api.set_exit("toaster", "leave"))
        self.assertIn("error", api.set_exit("mouse", "sparkly"))
        self.assertEqual(api.set_exit("all", "leave"), {"all": "leave"})  # one choice for everything replaces the rest
        self.assertEqual(a.state["exit"], {"all": "leave"})

    def test_original_mode_is_accepted(self):
        a = bare_app()
        a.commit = mock.Mock()
        m.Api(a, source="cli").set_mode("original")
        self.assertEqual(a.state["mode"], "original")


class LegacyCleanupTest(unittest.TestCase):
    def test_old_program_files_are_removed_and_settings_kept(self):
        with tempfile.TemporaryDirectory() as data:
            for d in ("python", "openrgb", os.path.join("cache", "core"), os.path.join("cache", "uploads"), "profiles"):
                os.makedirs(os.path.join(data, d))
            for f in ("rgbd.py", "rgb_app.pyw", "install.ps1", "Uninstall.exe", "rgbd.token", "config.json",
                      "state.json", "control.token", "accent_backup.json"):
                open(os.path.join(data, f), "w").close()
            with mock.patch.object(m.applog, "PROTECTED", True):
                gone = m.remove_legacy_code(data)
            left = sorted(os.listdir(data))
            self.assertEqual(left, ["accent_backup.json", "cache", "control.token", "profiles", "state.json"])
            self.assertEqual(os.listdir(os.path.join(data, "cache")), ["uploads"])
            self.assertIn("rgbd.py", gone)

    def test_nothing_is_removed_from_a_source_checkout_or_a_clean_folder(self):
        with tempfile.TemporaryDirectory() as data:
            open(os.path.join(data, "rgbd.py"), "w").close()
            with mock.patch.object(m.applog, "PROTECTED", False):
                self.assertEqual(m.remove_legacy_code(data), [])
            self.assertTrue(os.path.exists(os.path.join(data, "rgbd.py")))
            os.remove(os.path.join(data, "rgbd.py"))
            open(os.path.join(data, "state.json"), "w").close()
            with mock.patch.object(m.applog, "PROTECTED", True):
                self.assertEqual(m.remove_legacy_code(data), [])  # no rgbd.py: not an old install


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

    def test_lights_saved_as_off_wait_for_input_after_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = os.path.join(tmp, "accent_backup.json")
            open(backup, "w").close()
            for mode, armed in (("off", True), ("static", False)):
                state = {"mode": mode, "color": "0000FF", "groups": {}, "phone_token": "t" * 22}
                with mock.patch.object(m.rgb, "load_state", return_value=state), mock.patch.object(m.rgb, "save_state"), \
                        mock.patch.object(m.App, "_drop_orphan_profile_files"), mock.patch.object(m, "ACCENT_BACKUP", backup):
                    a = m.App()
                self.assertEqual(bool(a.wake_armed_at), armed, mode)

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


class KeepColorsTest(unittest.TestCase):
    def watch(self, a, rounds):
        """Run keep_watch for the given (idle seconds, full-screen program) rounds."""
        it = iter(rounds)
        cur = [None]

        def nxt():
            cur[0] = next(it)
        nxt()

        def sleep(_):
            try:
                nxt()
            except StopIteration:
                raise StopLoop
        with mock.patch.object(m, "idle_seconds", lambda: cur[0][0]), mock.patch.object(m, "fullscreen_program", lambda: cur[0][1]), \
                mock.patch.object(m.time, "sleep", sleep):
            with self.assertRaises(StopLoop):
                a.keep_watch()

    def test_coming_back_from_idle_sends_the_colors_again(self):
        a = bare_app()
        a.resend_colors = mock.Mock()
        self.watch(a, [(0, None), (m.KEEP_IDLE_AFTER + 5, None), (0, None), (0, None)])
        a.resend_colors.assert_called_once_with("back from idle", m.KEEP_AFTER_IDLE)

    def test_a_short_pause_is_not_idle(self):
        a = bare_app()
        a.resend_colors = mock.Mock()
        self.watch(a, [(0, None), (m.KEEP_IDLE_AFTER - 1, None), (0, None)])
        a.resend_colors.assert_not_called()

    def test_a_game_going_full_screen_sends_the_colors_once(self):
        a = bare_app()
        a.resend_colors = mock.Mock()
        self.watch(a, [(0, None), (0, "Fortnite.exe"), (0, "Fortnite.exe"), (0, None), (0, "Fortnite.exe")])
        self.assertEqual([c.args[1] for c in a.resend_colors.call_args_list], [m.KEEP_AFTER_GAME] * 2)

    def test_the_watcher_survives_an_error(self):
        a = bare_app()
        a.resend_colors = mock.Mock()
        calls = []

        def boom():
            calls.append(1)
            raise OSError("no foreground window")
        with mock.patch.object(m, "idle_seconds", return_value=0), mock.patch.object(m, "fullscreen_program", boom), \
                mock.patch.object(m.time, "sleep", sleeps_then_stops(2)):
            with self.assertRaises(StopLoop):
                a.keep_watch()
        self.assertGreaterEqual(len(calls), 2)

    def resend(self, a, delays):
        class Now:  # a Timer that fires straight away
            def __init__(self, delay, fn):
                self.fn, self.daemon = fn, False

            def start(self):
                self.fn()
        a.jobs = mock.Mock()
        with mock.patch.object(m.threading, "Timer", Now):
            a.resend_colors("test", delays)
        return a.jobs.put

    def test_resend_applies_once_per_delay(self):
        a = bare_app()
        a.client = object()
        put = self.resend(a, (1, 2, 3))
        self.assertEqual(put.call_args_list, [mock.call("apply")] * 3)

    def test_resend_leaves_the_vendor_lighting_alone_in_original_mode(self):
        a = bare_app(mode="original")
        a.client = object()
        self.resend(a, (1,)).assert_not_called()

    def test_resend_waits_for_a_connection(self):
        a = bare_app()
        self.resend(a, (1,)).assert_not_called()


class Dev:
    def __init__(self, name):
        self.name = name


def health_app(known, present, **state):
    """An App whose client shows the `present` devices while the `known` ones are expected."""
    a = bare_app(device_names=list(known), **state)
    a.client = mock.Mock(devices=[Dev(n) for n in present])
    a.health = {"state": "starting", "devices": 0, "missing": [], "note": ""}
    a.auto_fixes, a.conflicts, a.status = 0, [], ["", "ok"]
    a.rescan_timer, a.rescan_check = None, False
    return a


class HealthTest(unittest.TestCase):
    def setUp(self):
        for target, kw in ((m.rgb, {"prepare": mock.DEFAULT}), (m.rgb, {"save_state": mock.DEFAULT}),
                           (m.icue, {"running": mock.DEFAULT})):
            p = mock.patch.multiple(target, **kw)
            self.mocks = p.start()
            self.addCleanup(p.stop)
        m.icue.running.return_value = False

    def test_all_devices_present_is_ok(self):
        a = health_app(["Keyboard", "Mouse"], ["Keyboard", "Mouse"])
        self.assertEqual(a.check_health()["state"], "ok")
        a.rescan.assert_not_called()

    def test_a_missing_keyboard_is_rescanned_by_itself(self):
        a = health_app(["Keyboard", "Mouse"], ["Mouse"])
        h = a.check_health()
        self.assertEqual((h["state"], h["missing"]), ("recovering", ["Keyboard"]))
        a.rescan.assert_called_once()

    def test_after_two_tries_it_waits_for_the_fix_it_button(self):
        a = health_app(["Keyboard", "Mouse"], ["Mouse"])
        for _ in range(m.HEALTH_AUTO_FIXES):
            a.check_health()
        self.assertEqual(a.rescan.call_count, m.HEALTH_AUTO_FIXES)
        self.assertEqual(a.check_health()["state"], "lost")
        self.assertEqual(a.rescan.call_count, m.HEALTH_AUTO_FIXES)

    def test_the_fix_it_button_starts_again(self):
        a = health_app(["Keyboard", "Mouse"], ["Mouse"])
        a.auto_fixes = m.HEALTH_AUTO_FIXES
        a.fix_it()
        self.assertEqual(a.auto_fixes, 0)
        a.rescan.assert_called_once()

    def test_coming_back_resets_the_tries(self):
        a = health_app(["Keyboard", "Mouse"], ["Mouse"])
        a.check_health()
        a.client.devices = [Dev("Keyboard"), Dev("Mouse")]
        self.assertEqual(a.check_health()["state"], "ok")
        self.assertEqual(a.auto_fixes, 0)

    def test_vendor_app_lighting_is_never_rescanned_over(self):
        a = health_app(["Keyboard", "Mouse"], ["Mouse"], mode="original")
        self.assertEqual(a.check_health()["state"], "lost")
        a.rescan.assert_not_called()

    def test_icue_open_means_no_automatic_rescan(self):
        m.icue.running.return_value = True
        a = health_app(["Keyboard", "Mouse"], ["Mouse"])
        self.assertEqual(a.check_health()["state"], "lost")
        a.rescan.assert_not_called()

    def test_a_new_device_is_remembered_not_reported(self):
        a = health_app(["Keyboard"], ["Keyboard", "Mouse"])
        self.assertEqual(a.check_health()["state"], "ok")
        self.assertEqual(a.state["device_names"], ["Keyboard", "Mouse"])

    def test_a_silent_daemon_is_down(self):
        a = health_app(["Keyboard"], ["Keyboard"])
        m.rgb.prepare.side_effect = OSError("pipe closed")
        self.assertEqual(a.check_health()["state"], "down")
        a.rescan.assert_not_called()

    def test_connecting_at_startup_is_not_a_fault(self):
        a = health_app(["Keyboard"], [])
        a.client, a.status = None, ["Connecting…", "warn"]
        self.assertEqual(a.check_health()["state"], "starting")
        a.status = ["Lighting service not running", "bad"]
        self.assertEqual(a.check_health()["state"], "down")

    def test_no_client_while_a_rescan_runs_is_recovering(self):
        a = health_app(["Keyboard"], [])
        a.client, a.rescan_check = None, True
        self.assertEqual(a.check_health()["state"], "recovering")

    def test_forgetting_a_device_stops_the_alarm(self):
        a = health_app(["Keyboard", "Mouse"], ["Mouse"])
        a.auto_fixes = m.HEALTH_AUTO_FIXES
        self.assertEqual(a.forget_missing()["state"], "ok")
        self.assertEqual(a.state["device_names"], ["Mouse"])

    def test_close_it_closes_only_a_listed_conflict(self):
        a = health_app([], [])
        a.conflicts, a.conflicts_at = [{"name": "Logitech G HUB", "fix": "x"}], 0
        fake = mock.Mock(close_conflict=mock.Mock(return_value={"closed": ["lghub.exe"], "failed": []}),
                         _running_conflicts=lambda: [], CONFLICT_FIX={})
        with mock.patch.dict("sys.modules", {"diag": fake}):
            res = a.close_conflict("Logitech G HUB")
            with self.assertRaises(ValueError):
                a.close_conflict("explorer.exe")
        fake.close_conflict.assert_called_once_with("Logitech G HUB")
        self.assertEqual(res["closed"], ["lghub.exe"])
        self.assertEqual(res["health"]["conflicts"], [])

    def test_a_phone_cannot_close_programs(self):
        self.assertNotIn("close_conflict", m.PHONE_METHODS | m.CONTROL_METHODS)

    def test_our_own_openrgb_is_not_a_conflict(self):
        a = health_app([], [])
        with mock.patch.dict("sys.modules", {"diag": mock.Mock(_running_conflicts=lambda: ["OpenRGB", "Logitech G HUB"],
                                                                 CONFLICT_FIX={"Logitech G HUB": "turn it off"})}):
            a.check_conflicts()
        self.assertEqual([c["name"] for c in a.conflicts], ["Logitech G HUB"])
        self.assertEqual(a.conflicts[0]["fix"], "turn it off")


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
