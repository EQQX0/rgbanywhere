# Directories not compiled by default.
#  - PhilipsHueController: needs extra libraries (Hue++, mbedtls). FaustusController, LinuxLEDController: Linux only.
#  - License: the combined work is GPL-3.0-or-later, which cannot include GPL-2.0-only code. These folders contain
#    files marked "SPDX-License-Identifier: GPL-2.0-only": HIDLampArray, Mountain, GigabyteRGBFusion2USB, LianLi.
#    These contain source files with no license header at all, so their terms are unclear: Sinowealth, AsusAuraCore.
set(RGBCORE_EXCLUDE
    PhilipsHueController FaustusController LinuxLEDController
    HIDLampArrayController MountainKeyboardController GigabyteRGBFusion2USBController LianLiController
    SinowealthController AsusAuraCoreController
)
