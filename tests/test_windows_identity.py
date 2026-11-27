# SPDX-License-Identifier: GPL-3.0-or-later

"""Verify the actual Windows process identity used to group the taskbar icon."""

import ctypes
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@unittest.skipUnless(sys.platform == "win32", "Windows shell integration")
class WindowsIdentityTests(unittest.TestCase):
    def test_process_identity_matches_shared_installer_identity(self):
        from app.metadata import APP_USER_MODEL_ID
        from app.runtime import configure_windows_identity

        configure_windows_identity()
        shell = ctypes.WinDLL("shell32")
        get_app_id = shell.GetCurrentProcessExplicitAppUserModelID
        get_app_id.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
        get_app_id.restype = ctypes.c_long
        value = ctypes.c_void_p()
        self.assertEqual(get_app_id(ctypes.byref(value)), 0)
        try:
            self.assertEqual(ctypes.wstring_at(value), APP_USER_MODEL_ID)
        finally:
            free = ctypes.WinDLL("ole32").CoTaskMemFree
            free.argtypes = (ctypes.c_void_p,)
            free.restype = None
            free(value)
