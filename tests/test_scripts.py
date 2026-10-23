# Copyright (C) 2026 EQQX2. SPDX-License-Identifier: GPL-3.0-or-later
"""Every PowerShell script, and the update script built into installer/setup.cpp, must parse. Uses PowerShell's own
parser only: nothing is run. Skipped where no PowerShell is installed (set RGB_PWSH to a pwsh binary to force one).

  python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PWSH = os.environ.get("RGB_PWSH") or shutil.which("pwsh") or shutil.which("powershell")
PARSE = """$out = @{}
foreach ($f in $args) {
    $t = $null; $e = $null
    [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e) | Out-Null
    $out[$f] = @($e | ForEach-Object { "line $($_.Extent.StartLineNumber): $($_.Message)" })
}
$out | ConvertTo-Json -Depth 3 -Compress
"""


@unittest.skipUnless(PWSH, "no PowerShell here")
class ScriptsParseTest(unittest.TestCase):
    def test_every_script_parses(self):
        files = [os.path.join(ROOT, f) for f in ("install.ps1", "uninstall.ps1", "package.ps1", "fetch-openrgb.ps1",
                                                  os.path.join("installer", "build.ps1"))]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(ROOT, "installer", "setup.cpp"), encoding="utf-8") as f:
                src = f.read()
            update = os.path.join(tmp, "update.ps1")
            with open(update, "w", encoding="utf-8") as f:
                f.write(src[src.index('R"PS(') + 5:src.index(')PS"')])
            files.append(update)
            parser = os.path.join(tmp, "parse.ps1")
            with open(parser, "w", encoding="utf-8") as f:
                f.write(PARSE)
            r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", parser, *files],
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        errors = {os.path.basename(f): e for f, e in json.loads(r.stdout).items() if e}
        self.assertEqual(errors, {})


if __name__ == "__main__":
    unittest.main()
