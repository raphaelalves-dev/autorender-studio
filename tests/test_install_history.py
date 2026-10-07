from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.install_history import (
    RESTORE_LAUNCHER,
    ensure_baseline_history,
    snapshot_before_update,
)
from backend.updater import UpdatePackageError, prepare_update_zip


def _fake_install(root: Path) -> None:
    (root / "AutoRenderPreset.exe").write_bytes(b"old exe")
    internal = root / "_internal"
    internal.mkdir()
    (internal / "base_library.zip").write_bytes(b"old runtime")
    (root / "entrada").mkdir()
    (root / "entrada" / "video.mp4").write_bytes(b"user video")


class InstallationHistoryTests(unittest.TestCase):
    def test_baseline_is_idempotent_and_rollback_restores_runtime_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _fake_install(root)
            baseline = ensure_baseline_history(root, "1.2.4")
            self.assertEqual(baseline, ensure_baseline_history(root, "1.2.4"))
            self.assertEqual("baseline", json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))["kind"])

            previous = snapshot_before_update(root, "1.2.4", "next.zip")
            launcher = (root / RESTORE_LAUNCHER).read_text(encoding="utf-8")
            self.assertIn(previous.name, launcher)
            (root / "AutoRenderPreset.exe").write_bytes(b"new exe")
            (root / "_internal" / "base_library.zip").write_bytes(b"new runtime")
            (root / "_internal" / "new.dll").write_bytes(b"new file")
            ensure_baseline_history(root, "1.2.5")
            self.assertIn(previous.name, (root / RESTORE_LAUNCHER).read_text(encoding="utf-8"))

            if sys.platform == "win32":
                env = {**os.environ, "AUTORENDER_UPDATE_NO_RESTART": "1"}
                result = subprocess.run(["cmd.exe", "/c", str(root / RESTORE_LAUNCHER)], env=env, capture_output=True)
                self.assertEqual(0, result.returncode, result.stdout.decode(errors="replace"))
                self.assertEqual(b"old exe", (root / "AutoRenderPreset.exe").read_bytes())
                self.assertEqual(b"old runtime", (root / "_internal" / "base_library.zip").read_bytes())
                self.assertFalse((root / "_internal" / "new.dll").exists())
            self.assertEqual(b"user video", (root / "entrada" / "video.mp4").read_bytes())

    def test_update_preparation_requires_complete_runtime_and_saves_previous_version(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _fake_install(root)
            package = root / "package.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("AutoRenderPreset/AutoRenderPreset.exe", b"new exe")
                archive.writestr("AutoRenderPreset/_internal/base_library.zip", b"new runtime")
            prepared = prepare_update_zip(package, root)
            history = Path(prepared.history_entry)
            self.assertTrue((history / "snapshot" / "AutoRenderPreset.exe").is_file())
            self.assertIn("RESTAURAR.bat", Path(prepared.apply_script).read_text(encoding="utf-8"))
            self.assertEqual(b"old exe", (history / "snapshot" / "AutoRenderPreset.exe").read_bytes())

            incomplete = root / "incomplete.zip"
            with zipfile.ZipFile(incomplete, "w") as archive:
                archive.writestr("AutoRenderPreset.exe", b"new exe")
            with self.assertRaises(UpdatePackageError):
                prepare_update_zip(incomplete, root)


if __name__ == "__main__":
    unittest.main()
