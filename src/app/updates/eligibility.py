# SPDX-License-Identifier: GPL-3.0-or-later

"""Optional helper support; a full installer remains usable without .NET 4.8."""

import os


def delta_runtime_available() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            release, _ = winreg.QueryValueEx(key, "Release")
        return type(release) is int and release >= 528040
    except OSError:
        return False
