# PawnIO files

Prebuilt files copied unchanged from the OpenRGB tree. They were not built here.

- `PawnIOLib.dll`, `PawnIOLib.h`, `PawnIOLib.lib`: PawnIOLib, Copyright (C) 2023 namazso. The header states LGPL-2.1-or-later. Upstream: https://github.com/namazso/PawnIOLib (archived; the code moved into https://github.com/namazso/PawnIO).
- `modules/*.bin`: compiled PawnIO modules. Upstream: https://github.com/namazso/PawnIO.Modules, which lists LGPL-2.1 in its `COPYING` file. Whether the repository says "or later" was not checked.
- `LGPL-2.1.txt`: the license text for the files above, copied from the GNU text shipped with Debian/Ubuntu.

The DLL is a separate file next to `rgbcore.dll`, so you can replace it with your own build of PawnIOLib. The PawnIO driver is not included. Install it from https://pawnio.eu.

Not verified: the exact upstream release these files came from, and the license wording of the prebuilt binaries. Check the upstream repositories before a public release.
