# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""How SdkBackend copes when OpenRGB crashes, hangs, starts slowly or cannot get its port. Uses the stub SDK server
and a fake process object; starts no OpenRGB and touches no hardware.

  python -m unittest discover -s tests -v
"""
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from unittest import mock

import sdk_stub as st
import rgbcore_client as rc


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("SETTLE", 0), ("START_TIMEOUT", 5), ("CONNECT_TIMEOUT", 2), ("PORT_WAIT", 0.5)):
            p = mock.patch.object(rc.SdkBackend, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.servers, self.procs = [], []
        self.addCleanup(lambda: [s.stop() for s in self.servers])

    def server(self, **kw):
        srv = st.Server(**kw)
        self.servers.append(srv)
        return srv

    def backend(self, **kw):
        b = rc.SdkBackend(config_dir=self.tmp.name, **kw)
        self.addCleanup(b.shutdown)
        return b

    def spawning(self, start_server=True, code=None):
        """Patch process start-up: each "OpenRGB start" makes a FakeProc and (optionally) a stub server on the port."""
        self.port = free_port()
        self.exe = tempfile.NamedTemporaryFile(dir=self.tmp.name, suffix=".exe", delete=False).name

        def popen(args, **kw):
            assert args[0] == self.exe and "--server-port" in args
            if start_server:
                self.server(port=self.port)
            p = st.FakeProc(code)
            self.procs.append(p)
            return p
        for obj, name, value in ((rc.subprocess, "Popen", popen), (rc, "_own_openrgb_pids", lambda exe: []),
                                 (rc, "_kill_pid", self.fail_kill)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fail_kill(self, pid):
        raise AssertionError("tried to end pid %s" % pid)

    def crash(self):
        """OpenRGB exits: the process reports an exit code and the SDK server goes away."""
        self.procs[-1].returncode = 1
        self.servers[-1].stop()


class ConnectionTest(Base):
    def test_reconnects_after_the_connection_drops(self):
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        srv.drop_all()
        t0 = time.time()
        self.assertEqual(b.set_colors(1, [0xFF0000]), 0)
        self.assertLess(time.time() - t0, 5)  # noticed before sending, not after openrgb-python's 10 s wait
        self.assertEqual(srv.connects, 2)
        self.assertEqual(len(b.devices()), 3)

    def test_late_protocol_answer_does_not_leave_protocol_0(self):
        srv = self.server()
        srv.fail.add("slow_version")
        b = self.backend(port=srv.port, spawn=False)
        self.assertEqual(b.debug()["protocol"], 3)
        self.assertEqual([d["name"] for d in b.devices()], ["Board", "Keyboard", "Fan"])
        self.assertGreaterEqual(srv.connects, 2)

    def test_reply_cut_off_half_way_does_not_hang(self):
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        b.CALL_TIMEOUT = 2
        srv.fail.add("cut_data")
        self.assertEqual(len(b.devices()), 3)
        self.assertIn("last_error", b.debug())

    def test_connection_reset_half_way_is_retried(self):
        # openrgb-python then releases its lock twice, so the error arrives as "RuntimeError: release unlocked lock"
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        srv.fail.add("reset_data")
        self.assertEqual(len(b.devices()), 3)
        self.assertIn("unlocked lock", b.debug()["last_error"])

    def test_unanswered_request_times_out_and_is_retried(self):
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        srv.fail.add("hang_data")
        self.assertEqual(len(b.devices()), 3)  # openrgb-python gives up after 10 s, then the retry succeeds
        self.assertIsNotNone(b.debug()["last_error"])

    def test_packets_openrgb_python_cannot_read_are_skipped(self):
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        srv.notify(160, b"\x0a\x00\x00\x00\x01\x00\x02\x00A\x00")  # PROFILE_LIST_UPDATED, sent to every client
        srv.notify(304, b"x" * 300)
        srv.notify(100)  # DEVICE_LIST_UPDATED: openrgb-python handles this one itself
        t0 = time.time()
        self.assertEqual(len(b.devices()), 3)
        self.assertEqual(b.set_colors(1, [0x00FF00], fill=True), 0)
        srv.notify(160, b"\x04\x00\x00\x00")
        self.assertEqual(b.set_colors(1, [0xFF0000], fill=True), 0)
        self.assertLess(time.time() - t0, 3)
        self.assertEqual(srv.connects, 1)  # no reconnect was needed
        self.assertIsNone(b.debug()["last_error"])

    def test_gone_server_fails_with_a_clear_error(self):
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        srv.stop()
        with self.assertRaises(TimeoutError) as e:
            b.devices()
        self.assertIn("did not open its SDK server", str(e.exception))


class StartTest(Base):
    def test_start_and_shutdown(self):
        self.spawning()
        b = self.backend(exe=self.exe, port=self.port)
        self.assertEqual(len(b.devices()), 3)
        self.assertEqual(b.debug()["pid"], st.FakeProc.pid)
        b.shutdown()
        self.assertTrue(self.procs[0].terminated)

    def test_exit_while_starting(self):
        self.spawning(start_server=False, code=3)
        with self.assertRaises(RuntimeError) as e:
            self.backend(exe=self.exe, port=self.port)
        self.assertIn("exited with code 3", str(e.exception))
        self.assertIn("logs", str(e.exception))

    def test_stuck_start_is_ended(self):
        self.spawning(start_server=False)
        with mock.patch.object(rc.SdkBackend, "START_TIMEOUT", 1):
            with self.assertRaises(TimeoutError):
                self.backend(exe=self.exe, port=self.port)
        self.assertTrue(self.procs[0].terminated)

    def test_missing_exe(self):
        with self.assertRaises(FileNotFoundError):
            self.backend(exe=self.tmp.name + "/nope/OpenRGB.exe")

    def test_port_used_by_another_program(self):
        self.spawning()
        other = socketserver.TCPServer(("127.0.0.1", self.port), socketserver.BaseRequestHandler)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        with self.assertRaises(RuntimeError) as e:
            self.backend(exe=self.exe, port=self.port)
        self.assertIn("port %d" % self.port, str(e.exception))
        self.assertEqual(self.procs, [])  # OpenRGB was not started

    def test_leftover_openrgb_is_ended_before_start(self):
        self.spawning()
        ended = []
        with mock.patch.object(rc, "_own_openrgb_pids", lambda exe: [77] if exe == self.exe else []), \
                mock.patch.object(rc, "_kill_pid", ended.append):
            b = self.backend(exe=self.exe, port=self.port)
        self.assertEqual(ended, [77])
        self.assertEqual(len(b.devices()), 3)


class RestartTest(Base):
    def test_restart_after_exit_calls_on_restart(self):
        self.spawning()
        calls = []
        b = self.backend(exe=self.exe, port=self.port, on_restart=lambda: calls.append(1))
        self.crash()
        self.assertEqual(b.set_colors(1, [0x00FF00] * 4), 0)
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(calls, [1])
        self.assertEqual(b.debug()["restarts"], 1)

    def test_restart_limit(self):
        self.spawning()
        b = self.backend(exe=self.exe, port=self.port)
        b.MAX_RESTARTS = 1
        self.crash()
        b.devices()
        self.crash()
        with self.assertRaises(RuntimeError) as e:
            b.devices()
        self.assertIn("not started again", str(e.exception))
        b.rescan()  # asking for it resets the count
        self.assertEqual(len(b.devices()), 3)

    def test_key_finds_the_device_after_renumbering(self):
        self.spawning()
        b = self.backend(exe=self.exe, port=self.port)
        board_key = ["Board", "loc"]
        self.crash()
        b.devices()
        srv = self.servers[-1]
        srv.devices.reverse()  # OpenRGB came back and listed the devices in another order
        b.devices()
        self.assertEqual(b.set_colors(0, [0x0000FF] * 3, key=board_key), 1)
        self.assertEqual(srv.log[-1], ("mode", 2, "Static"))

    def test_unknown_key(self):
        srv = self.server()
        b = self.backend(port=srv.port, spawn=False)
        with self.assertRaises(LookupError):
            b.set_colors(0, [1], key=["Gone", "loc"])


class RemoteBackendTest(unittest.TestCase):
    def test_daemon_closing_without_an_answer(self):
        class Closer(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()

        srv = socketserver.TCPServer(("127.0.0.1", 0), Closer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        r = object.__new__(rc.RemoteBackend)
        r.port, r.timeout, r._token = srv.server_address[1], 5, "t"
        with self.assertRaises(ConnectionError) as e:
            r.request("devices")
        self.assertIn("without answering", str(e.exception))


if __name__ == "__main__":
    unittest.main()
