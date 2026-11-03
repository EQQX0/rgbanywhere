# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Input and port checks on rgb_app.pyw found in review."""
import socket
import unittest
from unittest import mock

import rgb
from test_app import bare_app, m


class CalibrationInput(unittest.TestCase):
    def api(self):
        a = bare_app()
        a.commit = mock.Mock()
        a.jobs = mock.Mock()
        return a, m.Api(a, source="phone")

    def test_gains_must_be_three_numbers(self):
        for bad in ([], [100], [1, 2, 3, 4], "abc", None, ["x", 1, 2]):
            a, api = self.api()
            self.assertIn("error", api.set_calibration("fans", bad) or {}, bad)
            self.assertNotIn("fans", a.state.get("calibration", {}))
            a.commit.assert_not_called()

    def test_good_gains_are_stored_and_clamped(self):
        a, api = self.api()
        self.assertIsNone(api.set_calibration("fans", [300, 50, -5]))
        self.assertEqual(a.state["calibration"]["fans"], [200, 50, 0])

    def test_test_color_must_be_a_color(self):
        a, api = self.api()
        self.assertIn("error", api.calibrate("zz") or {})
        self.assertIsNone(getattr(a, "test_color", None))
        self.assertIsNone(api.calibrate("00ff00"))
        self.assertEqual(a.test_color, "00FF00")


class Ports(unittest.TestCase):
    def test_a_second_server_cannot_share_the_port(self):
        """Windows lets SO_REUSEADDR sockets bind a port that is already listening, so another process could sit on it."""
        first = m.ExclusiveServer(("127.0.0.1", 0), m.BaseHTTPRequestHandler)
        self.addCleanup(first.server_close)
        port = first.server_address[1]
        with self.assertRaises(OSError):
            second = m.ExclusiveServer(("127.0.0.1", port), m.BaseHTTPRequestHandler)
            second.server_close()


if __name__ == "__main__":
    unittest.main()
