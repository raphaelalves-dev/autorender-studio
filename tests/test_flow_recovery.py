from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import ffmpeg_builder
from backend.config import AppConfig, app_config_from_dict
from backend.history import RenderHistory
from backend.input_selector import candidate_videos
from backend.renderer import RenderError, _move_files_transactionally, render_one
from backend.watcher import IdleRescanTracker, rescan_pending_input


class FlowRecoveryTests(unittest.TestCase):
    def test_reused_cycle_name_is_reactivated_for_new_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cfg = AppConfig(project_root=str(root))
            cycle = cfg.input_path / "0956_MANUAL_TODAS"
            cycle.mkdir(parents=True)
            source = cycle / "video.mp4"
            output = cfg.output_path / "0956_RENDER" / "video_render.mp4"
            output.parent.mkdir(parents=True)
            source.write_bytes(b"old-video")
            output.write_bytes(b"old-output")

            history = RenderHistory(cfg.history_path, cfg.input_path)
            history.mark_video_success(
                source,
                output,
                source_mode="cycle",
                cycle_folder=cycle,
                preset_name="preset",
                elapsed_seconds=8.0,
            )
            history.mark_cycle_complete(cycle, video_count=1)
            source.unlink()
            source.write_bytes(b"new-video-with-different-size")

            refreshed = RenderHistory(cfg.history_path, cfg.input_path)

            self.assertFalse(refreshed.is_cycle_done(cycle))
            self.assertEqual([source], candidate_videos(cfg, refreshed))

    def test_empty_cycle_folder_does_not_block_loose_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cfg = AppConfig(project_root=str(root))
            (cfg.input_path / "1000_MANUAL_TODAS").mkdir(parents=True)
            loose = cfg.input_path / "video_solto.mp4"
            loose.write_bytes(b"video")

            self.assertEqual([loose], candidate_videos(cfg))

    def test_deep_rescan_reopens_history_when_output_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cfg = AppConfig(project_root=str(root), render_format="pair")
            cfg.input_path.mkdir(parents=True)
            source = cfg.input_path / "video_pendente.mp4"
            output = cfg.output_path / "video_render.mp4"
            output.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            output.write_bytes(b"output")

            history = RenderHistory(cfg.history_path, cfg.input_path)
            history.mark_video_success(
                source,
                output,
                source_mode="loose",
                cycle_folder=None,
                preset_name="preset",
                elapsed_seconds=8.0,
            )
            output.unlink()

            result = rescan_pending_input(cfg)

            self.assertEqual(1, result.scanned_videos)
            self.assertEqual(1, result.reopened_history_records)
            self.assertEqual(1, result.pending_jobs)
            self.assertFalse(RenderHistory(cfg.history_path, cfg.input_path).is_video_done(source))

    def test_group_move_rolls_back_when_second_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "entrada" / "81_video.mp4"
            missing = root / "entrada" / "82_video.mp4"
            processed = root / "processados"
            first.parent.mkdir(parents=True)
            first.write_bytes(b"first")

            with self.assertRaises(RenderError):
                _move_files_transactionally([(first, processed), (missing, processed)])

            self.assertTrue(first.exists())
            self.assertFalse((processed / first.name).exists())

    def test_render_does_not_mark_history_before_original_move(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cfg = AppConfig(project_root=str(root), preset_file="preset.mov")
            cfg.input_path.mkdir(parents=True)
            cfg.preset_path.parent.mkdir(parents=True, exist_ok=True)
            source = cfg.input_path / "video.mp4"
            output = cfg.output_path / "video_render.mp4"
            source.write_bytes(b"source")
            cfg.preset_path.write_bytes(b"preset")
            media_info = SimpleNamespace(to_dict=lambda: {})
            logger = SimpleNamespace(info=lambda *_args: None, error=lambda *_args: None, warning=lambda *_args: None)

            def finish_render(_cmd, _config):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"render")
                return 0, ""

            with (
                patch("backend.renderer.setup_logger", return_value=logger),
                patch("backend.renderer.build_output_path", return_value=output),
                patch("backend.renderer.build_ffmpeg_command", return_value=["ffmpeg"]),
                patch("backend.renderer.probe_media", return_value=media_info),
                patch("backend.renderer._run_ffmpeg_command", side_effect=finish_render),
                patch("backend.renderer._move_files_transactionally", side_effect=RenderError("move failed")),
                patch("backend.renderer.RenderHistory.mark_video_success") as mark_success,
            ):
                with self.assertRaises(RenderError):
                    render_one(source, cfg)

            mark_success.assert_not_called()

    def test_rescan_check_count_is_clamped(self) -> None:
        self.assertEqual(6, app_config_from_dict({}).idle_rescan_checks)
        self.assertEqual(1, app_config_from_dict({"idle_rescan_checks": 0}).idle_rescan_checks)
        self.assertEqual(120, app_config_from_dict({"idle_rescan_checks": 999}).idle_rescan_checks)

    def test_idle_tracker_runs_recovery_on_sixth_empty_check(self) -> None:
        tracker = IdleRescanTracker(threshold=6)

        self.assertEqual([False, False, False, False, False, True], [tracker.record(False) for _ in range(6)])
        self.assertFalse(tracker.record(True))
        self.assertEqual(0, tracker.idle_checks)

    def test_temporary_gpu_failure_is_retested_after_thirty_seconds(self) -> None:
        cache_key = "test-runtime-retry"
        ffmpeg_builder._RUNTIME_TEST_CACHE.pop(cache_key, None)
        probe_results = iter([(False, "driver starting"), (True, "")])
        probe_calls = 0

        def probe():
            nonlocal probe_calls
            probe_calls += 1
            return next(probe_results)

        with patch("backend.ffmpeg_builder.time.monotonic", side_effect=[0.0, 0.0, 10.0, 31.0, 31.0]):
            first = ffmpeg_builder._cached_runtime_test(cache_key, probe)
            cached = ffmpeg_builder._cached_runtime_test(cache_key, probe)
            retried = ffmpeg_builder._cached_runtime_test(cache_key, probe)

        ffmpeg_builder._RUNTIME_TEST_CACHE.pop(cache_key, None)
        self.assertFalse(first[0])
        self.assertFalse(cached[0])
        self.assertTrue(retried[0])
        self.assertEqual(2, probe_calls)


if __name__ == "__main__":
    unittest.main()
