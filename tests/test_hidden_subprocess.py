from __future__ import annotations

import subprocess
import sys
import unittest

from backend.subprocess_utils import hidden_console_kwargs


class HiddenSubprocessTests(unittest.TestCase):
    def test_windows_process_uses_no_window_flags(self) -> None:
        kwargs = hidden_console_kwargs()
        if not sys.platform.startswith("win"):
            self.assertEqual(kwargs, {})
            return

        self.assertEqual(
            kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW,
            subprocess.CREATE_NO_WINDOW,
        )
        startupinfo = kwargs["startupinfo"]
        self.assertEqual(
            startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW,
            subprocess.STARTF_USESHOWWINDOW,
        )
        self.assertEqual(startupinfo.wShowWindow, subprocess.SW_HIDE)


if __name__ == "__main__":
    unittest.main()
