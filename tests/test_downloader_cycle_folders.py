from __future__ import annotations

import unittest
from pathlib import Path

from backend.config import AppConfig, is_recognized_cycle_folder_name
from backend.ffmpeg_builder import cycle_output_folder_name
from backend.input_selector import candidate_videos, is_cycle_like_folder, source_mode_for_path


class DownloaderCycleFolderTests(unittest.TestCase):
    fixtures_root = Path(__file__).resolve().parent / "fixtures"

    def _config(self) -> AppConfig:
        return AppConfig(project_root=str(self.fixtures_root / "cycle_case"), input_dir="entrada", output_dir="saida")

    def test_manual_todas_folder_is_a_cycle(self) -> None:
        cfg = self._config()
        folder = cfg.input_path / "1025_MANUAL_TODAS"

        self.assertTrue(is_cycle_like_folder(folder, cfg))

    def test_cycle_videos_are_selected_and_loose_files_wait(self) -> None:
        cfg = self._config()
        cycle_video = cfg.input_path / "1025_MANUAL_TODAS" / "82_camera.mp4"

        self.assertEqual(candidate_videos(cfg), [cycle_video])
        self.assertEqual(source_mode_for_path(cycle_video, cfg), "cycle")

    def test_manual_todas_output_uses_render_folder(self) -> None:
        self.assertEqual(cycle_output_folder_name("1205_MANUAL_TODAS"), "1205_RENDER")
        self.assertEqual(cycle_output_folder_name("MANUAL_TODAS"), "RENDER")

    def test_existing_cycle_names_keep_working(self) -> None:
        self.assertTrue(is_recognized_cycle_folder_name("1234_AUTO"))
        self.assertTrue(is_recognized_cycle_folder_name("Manual"))
        self.assertTrue(is_recognized_cycle_folder_name("Manual_Retry"))
        self.assertEqual(cycle_output_folder_name("1234_AUTO"), "1234_RENDER")


if __name__ == "__main__":
    unittest.main()
