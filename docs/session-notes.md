# Session notes: openrgb-bundle

## Report (kept current)

State: work in progress on branch `openrgb-bundle`. Nothing here has run on Windows or real hardware.

Fixed so far:
1. SdkBackend failure handling: reconnects after a dropped SDK connection, restarts OpenRGB when it exits (at most 3 times in 5 minutes, then waits for a rescan), ends a half-started OpenRGB, ends a leftover copy of our own OpenRGB before starting, reports another program on port 47826, rejects the silent fallback to SDK protocol 0, and closes a request stuck on a cut-off reply after 30 s.
2. Requests carry a device key (name and location), so a colour push that races an OpenRGB restart reaches the right device.
3. The daemon keeps running when OpenRGB fails to start, reports why, and retries on Rescan.

Tests: `python -m unittest discover -s tests -v` (see the test list below).

Unverified on hardware: everything that touches OpenRGB.exe, the job object, and the leftover-process cleanup.

## Needs the owner

(none yet)

## Done, step by step

1. Task 1, failure handling in rgbcore_client.py and rgbd.py, tests in tests/test_sdk_failures.py.

## Next

Tasks 2 to 8 from the session brief, in order.

## Notes for a later session

- The `stop-slop` skill named in the brief is not installed in this environment. Prose was checked by hand instead.
- openrgb-python 0.3.8 quirks worked around in SdkBackend: a 1 s protocol-version wait that silently falls back to protocol 0; a zero-byte header after the server closes leaves its lock held for 10 s; a reply cut off half way loops forever; ModeData.pack() refuses some modes OpenRGB reports.
