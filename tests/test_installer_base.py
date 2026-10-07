from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from AutoRenderPreset_GUI import prepare_portable_config
from backend.config import AppConfig, load_config, save_config


class InstallerBaseTests(unittest.TestCase):
    def test_first_install_needs_user_preset_and_keeps_selected_external_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "app"
            app.mkdir()
            preset = root / "meu-preset.mov"
            preset.write_bytes(b"arquivo de teste")
            cfg = AppConfig(project_root=".", preset_file="", render_format="all")
            save_config(cfg, app / "config" / "settings.json")
            installed = load_config(app / "config" / "settings.json")
            installed.project_root = str(app)
            self.assertEqual(installed.available_preset_paths(), [])

            cfg.preset_file = str(preset)
            cfg.input_dir = str(root / "minha-entrada")
            cfg.output_dir = str(root / "meu-servidor")
            cfg.logs_dir = str(root / "meus-logs")
            cfg.start_with_windows = True
            cfg.auto_start_on_launch = True
            save_config(cfg, app / "config" / "settings.json")
            previous = Path.cwd()
            try:
                with patch.object(sys, "frozen", True, create=True):
                    prepare_portable_config(app)
            finally:
                os.chdir(previous)
            restored = load_config(app / "config" / "settings.json")
            self.assertEqual(restored.preset_file, str(preset))
            self.assertEqual(restored.preset_path, preset)
            self.assertEqual(restored.input_dir, str(root / "minha-entrada"))
            self.assertEqual(restored.output_dir, str(root / "meu-servidor"))
            self.assertEqual(restored.logs_dir, str(root / "meus-logs"))
            self.assertTrue(restored.start_with_windows)
            self.assertTrue(restored.auto_start_on_launch)


if __name__ == "__main__":
    unittest.main()
