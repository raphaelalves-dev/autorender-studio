from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.logger import append_jsonl, append_text_line
from backend.config import AppConfig
from backend.ffmpeg_builder import _green_key_filter, _video_graph_segment
from backend.renderer import RenderResult, _result_log_row


class LogRetentionTests(unittest.TestCase):
    def test_auto_keyer_keeps_fast_colorkey_for_prores_422(self) -> None:
        cfg = AppConfig()
        cfg.export.chromakey.method = "auto"

        chain = _green_key_filter(cfg, None)

        self.assertIn("format=rgba,colorkey=", chain)
        self.assertNotIn("chromakey=", chain)

    def test_blur_background_is_optimized_without_reducing_foreground(self) -> None:
        cfg = AppConfig()
        cfg.export.scale_mode = "fit_blur"

        graph = _video_graph_segment(cfg, "[0:v]", "base", 1080, 1920)

        self.assertIn("scale=270:480", graph)
        self.assertIn("boxblur=8:1,scale=1080:1920[base_bg]", graph)
        self.assertIn("[base_fgsrc]scale=1080:1920", graph)

    def test_text_log_rotates_with_bounded_backups(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "janela_teste.log"
            append_text_line(path, "1234567890", max_bytes=5, backup_count=2)
            append_text_line(path, "nova", max_bytes=5, backup_count=2)

            self.assertEqual("nova\n", path.read_text(encoding="utf-8"))
            self.assertTrue(path.with_name("janela_teste.log.1").exists())

    def test_jsonl_rotation_does_not_create_unlimited_timestamp_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            path = logs / "renders.jsonl"
            path.write_text("x" * (10 * 1024 * 1024 + 1), encoding="utf-8")
            append_jsonl(logs, "renders.jsonl", {"success": True})

            row = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(row["success"])
            self.assertTrue(path.with_name("renders.jsonl.1").exists())

    def test_render_log_removes_verbose_stream_arrays(self) -> None:
        result = RenderResult(
            success=True,
            input_file="in.mp4",
            output_file="out.mp4",
            started_at="2026-07-21T10:00:00",
            ended_at="2026-07-21T10:00:10",
            elapsed_seconds=10.0,
            input_size_bytes=1,
            output_size_bytes=1,
            return_code=0,
            command="ffmpeg",
            error=None,
            input_info={"duration": 10, "streams": [{"large": "metadata"}]},
            output_info={"duration": 10, "streams": [{"large": "metadata"}]},
            preset_info={"duration": 10, "streams": [{"large": "metadata"}]},
        )

        row = _result_log_row(result)
        self.assertNotIn("streams", row["input_info"])
        self.assertNotIn("streams", row["output_info"])
        self.assertNotIn("streams", row["preset_info"])


if __name__ == "__main__":
    unittest.main()
