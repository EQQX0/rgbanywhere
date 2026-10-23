# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""The daemon's start, retry and rescan logic (rgbd.py) with SdkBackend replaced by mocks. Nothing here can start
OpenRGB or touch hardware.

  python -m unittest discover -s tests -v
"""
import unittest
from unittest import mock

import sdk_stub  # noqa: F401  (puts the repo on sys.path, keeps logs out of the checkout)
import rgb
import rgbcore_client as rc
import rgbd


class DaemonLogicTest(unittest.TestCase):
    def setUp(self):
        for obj, name, value in ((rgbd, "_core", None), (rgbd, "_client", None), (rgbd, "_start_error", None),
                                 (rgb, "stop_helper", lambda: False), (rgb, "start_helper", lambda: None),
                                 (rgbd, "_log_diagnosis", lambda: None), (rgbd, "time", mock.Mock())):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.applied = []
        p = mock.patch.object(rgbd, "_refresh_and_apply", lambda: self.applied.append(1))
        p.start()
        self.addCleanup(p.stop)

    def test_failed_start_keeps_the_reason(self):
        with mock.patch.object(rc, "SdkBackend", side_effect=TimeoutError("no SDK server")):
            rgbd._start_core()
        self.assertIsNone(rgbd._core)
        self.assertEqual(rgbd._start_error, "TimeoutError: no SDK server")
        self.assertEqual(self.applied, [])
        with self.assertRaisesRegex(RuntimeError, r"OpenRGB did not start \(TimeoutError: no SDK server\)"):
            with mock.patch.object(rgbd, "_ready", mock.Mock(is_set=lambda: True)):
                rgbd._need_core()

    def test_backend_that_starts_but_cannot_list_devices_is_shut_down(self):
        core = mock.Mock()
        core.devices.side_effect = ConnectionError("gone")
        with mock.patch.object(rc, "SdkBackend", return_value=core):
            rgbd._start_core()
        core.shutdown.assert_called_once()
        self.assertIsNone(rgbd._core)
        self.assertIn("gone", rgbd._start_error)

    def test_rescan_retries_a_failed_start(self):
        with mock.patch.object(rc, "SdkBackend", side_effect=OSError("port")):
            rgbd._start_core()
            with self.assertRaisesRegex(RuntimeError, "OpenRGB did not start: OSError: port"):
                rgbd._rescan()
        core = mock.Mock()
        core.devices.return_value = []
        with mock.patch.object(rc, "SdkBackend", return_value=core) as make:
            rgbd._rescan()
        make.assert_called_once()
        self.assertIs(rgbd._core, core)
        self.assertIsNone(rgbd._start_error)
        self.assertEqual(self.applied, [1])
        core.rescan.assert_not_called()  # a fresh start detects anyway

    def test_rescan_of_a_running_core(self):
        core = mock.Mock()
        core.devices.return_value = []
        with mock.patch.object(rgbd, "_core", core), mock.patch.object(rgbd, "_client", rc.Client(core)):
            rgbd._rescan()
        core.rescan.assert_called_once()
        self.assertEqual(self.applied, [1])

    def test_helper_is_started_again_even_when_the_rescan_fails(self):
        started = []
        core = mock.Mock()
        core.rescan.side_effect = RuntimeError("boom")
        with mock.patch.object(rgbd, "_core", core), mock.patch.object(rgb, "stop_helper", lambda: True), \
                mock.patch.object(rgb, "start_helper", lambda: started.append(1)):
            with self.assertRaises(RuntimeError):
                rgbd._rescan()
        self.assertEqual(started, [1])

    def test_after_restart_reapplies_in_a_thread(self):
        threads = []
        with mock.patch.object(rgbd.threading, "Thread", lambda target, **kw: threads.append(target) or mock.Mock()):
            rgbd._after_restart()
        self.assertEqual(len(threads), 1)
        threads[0]()
        self.assertEqual(self.applied, [1])


if __name__ == "__main__":
    unittest.main()
