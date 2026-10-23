# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Repository consistency: things that went stale before. Reads files only.

  python -m unittest discover -s tests -v
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = (".py", ".pyw", ".ps1", ".cmd", ".md", ".txt", ".cpp", ".rc", ".json", ".html", "NOTICE", ".gitignore")


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    return [f for f in out if f.endswith(TEXT) and not f.startswith("android/") and os.path.exists(os.path.join(ROOT, f))]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8-sig") as f:
        return f.read()


@unittest.skipUnless(os.path.isdir(os.path.join(ROOT, ".git")), "needs a git checkout")
class RepoTest(unittest.TestCase):
    def test_no_trace_of_the_compiled_core(self):
        old = re.compile(r"rgbcore\.dll|third_party[/\\]|(?<![\w/\\])build\.ps1|(?<![\w.])core[/\\](?!logs)|CMakeLists|cmake", re.I)
        hits = []
        for f in tracked():
            if f.startswith(("tests/", "docs/")) or f == "installer/build.ps1":
                continue
            for n, line in enumerate(read(f).splitlines(), 1):
                for m in old.finditer(line):
                    # installer/build.ps1 builds the Setup exe and is meant to be named
                    if "installer/build.ps1" in line[max(0, m.start() - 10):m.end()] or "android/build.ps1" in line:
                        continue
                    hits.append("%s:%d: %s" % (f, n, line.strip()[:120]))
        self.assertEqual(hits, [])

    def test_package_lists_files_that_exist(self):
        files = re.search(r"\$files = (.*?)\nforeach", read("package.ps1"), re.S).group(1)
        for name in re.findall(r"'([^']+)'", files):
            if "*" not in name:
                self.assertTrue(os.path.exists(os.path.join(ROOT, name)), name)

    def test_secrets_are_ignored(self):
        ignore = read(".gitignore").splitlines()
        for name in ("control.token", "rgbd.token", "state.json", "config.json", "*.keystore", "*.jks", "/openrgb/", "install.log"):
            self.assertIn(name, ignore)
        for f in subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines():
            self.assertFalse(f.endswith((".token", ".keystore", ".jks")) or os.path.basename(f) == "state.json", f)

    def test_openrgb_python_is_pinned(self):
        self.assertIn("openrgb-python==0.3.8", read("requirements.txt").splitlines())

    def test_python_files_compile_without_warnings(self):
        for f in tracked():
            if f.endswith((".py", ".pyw")):
                r = subprocess.run([sys.executable, "-W", "error", "-c", "import sys; compile(open(sys.argv[1], encoding='utf-8')"
                                    ".read(), sys.argv[1], 'exec')", os.path.join(ROOT, f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, "%s: %s" % (f, r.stderr.strip()[-300:]))

    def test_task_and_port_names_agree(self):
        import rgbcore_client as rc
        for f in ("install.ps1", "uninstall.ps1", "installer/setup.cpp"):
            self.assertIn(rc.TASK_LIGHTING, read(f), f)
        self.assertIn(rc.TASK_RESCAN, read("install.ps1"))
        self.assertIn(str(rc.SDK_PORT), read("README.md"))


if __name__ == "__main__":
    unittest.main()
