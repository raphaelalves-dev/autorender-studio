from __future__ import annotations

import unittest
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.config import AppConfig, app_config_from_dict, effective_render_format
from backend.ffprobe_reader import ProbeError
from backend.input_selector import candidate_all_videos, candidate_video_groups
from backend.renderer import render_both
from backend.watcher import render_configured_candidates, rescan_pending_input


class RenderBothTests(unittest.TestCase):
    fixtures_root = Path(__file__).resolve().parent / "fixtures" / "both_case"

    def _config(self) -> AppConfig:
        return AppConfig(
            project_root=str(self.fixtures_root),
            input_dir="entrada",
            output_dir="saida",
            history_enabled=False,
            move_original_on_success=False,
        )

    def test_config_accepts_all_render_formats_and_keeps_pair_as_default(self) -> None:
        self.assertEqual(app_config_from_dict({"render_format": "single"}).render_format, "single")
        self.assertEqual(app_config_from_dict({"render_format": "pair"}).render_format, "pair")
        self.assertEqual(app_config_from_dict({"render_format": "both"}).render_format, "both")
        self.assertEqual(app_config_from_dict({"render_format": "all"}).render_format, "all")
        self.assertEqual(app_config_from_dict({"render_format": "invalid"}).render_format, "pair")

    def test_all_mode_selects_every_video_from_81_through_86_individually(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(project_root=temp_dir, input_dir="entrada", history_enabled=False)
            cycle = cfg.input_path / "1300_MANUAL_TODAS"
            cycle.mkdir(parents=True)
            expected = []
            for number in range(81, 87):
                video = cycle / f"{number}_camera.mp4"
                video.write_bytes(b"video")
                expected.append(video)

            self.assertEqual(candidate_all_videos(cfg), expected)
            recovery = rescan_pending_input(AppConfig(
                project_root=temp_dir,
                input_dir="entrada",
                history_enabled=False,
                render_format="all",
            ))
            self.assertEqual(recovery.pending_jobs, 6)

    def test_one_available_preset_automatically_selects_all_videos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(project_root=temp_dir, render_format="pair")
            cfg.preset_path.parent.mkdir(parents=True)
            cfg.preset_path.write_bytes(b"single-preset")

            self.assertEqual(effective_render_format(cfg), "all")

    def test_two_available_presets_keep_the_configured_pair_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(
                project_root=temp_dir,
                preset_file="individual.mov",
                pair_preset_file="duplo.mov",
                render_format="pair",
            )
            preset_dir = cfg.resolve_path(cfg.preset_dir)
            preset_dir.mkdir(parents=True)
            (preset_dir / "individual.mov").write_bytes(b"individual")
            (preset_dir / "duplo.mov").write_bytes(b"pair")

            self.assertEqual(effective_render_format(cfg), "pair")

    def test_one_preset_dispatches_six_individual_renders_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(
                project_root=temp_dir,
                input_dir="entrada",
                render_format="pair",
                history_enabled=False,
                move_original_on_success=False,
                parallel_workers=1,
            )
            cfg.input_path.mkdir(parents=True)
            cfg.preset_path.parent.mkdir(parents=True)
            cfg.preset_path.write_bytes(b"single-preset")
            videos = []
            for number in range(81, 87):
                video = cfg.input_path / f"{number}_camera.mp4"
                video.write_bytes(b"video")
                videos.append(video)

            with (
                patch("backend.watcher.wait_for_stable_file", return_value=True),
                patch("backend.watcher.probe_media", return_value=SimpleNamespace()),
                patch(
                    "backend.watcher.render_one",
                    side_effect=[SimpleNamespace(elapsed_seconds=1.0) for _ in videos],
                ) as render_mock,
            ):
                processed = render_configured_candidates(cfg)

            self.assertEqual(processed, 6)
            self.assertEqual([call.args[0] for call in render_mock.call_args_list], videos)

    def test_each_input_folder_finishes_before_the_next_folder_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(
                project_root=temp_dir,
                input_dir="entrada",
                render_format="all",
                history_enabled=False,
                move_original_on_success=False,
                parallel_workers=2,
            )
            events = []
            for folder_name in ("1000_AUTO", "1010_AUTO"):
                cycle = cfg.input_path / folder_name
                cycle.mkdir(parents=True)
                for number in range(81, 84):
                    (cycle / f"{number}_camera.mp4").write_bytes(b"video")

            @contextmanager
            def record_folder_gate():
                events.append("gate:open")
                try:
                    yield
                finally:
                    events.append("gate:release")

            def record_render(path, _config):
                events.append(f"render:{path.parent.name}")
                return SimpleNamespace(elapsed_seconds=1.0, delivery_pending=True)

            with (
                patch("backend.watcher.wait_for_stable_file", return_value=True),
                patch("backend.watcher.probe_media", return_value=SimpleNamespace()),
                patch("backend.watcher.render_one", side_effect=record_render),
                patch("backend.watcher.defer_deliveries_until_batch_complete", record_folder_gate),
            ):
                processed = render_configured_candidates(cfg)

            self.assertEqual(processed, 6)
            first_release = events.index("gate:release")
            second_folder_start = events.index("render:1010_AUTO")
            self.assertLess(first_release, second_folder_start)
            self.assertEqual(events.count("gate:release"), 2)

    def test_incomplete_mp4_waits_without_starting_render_or_repeating_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(
                project_root=temp_dir,
                input_dir="entrada",
                render_format="both",
                history_enabled=False,
                move_original_on_success=False,
            )
            cycle = cfg.input_path / "1307_MANUAL_TODAS"
            cycle.mkdir(parents=True)
            bad_video = cycle / "81_camera.mp4"
            good_video = cycle / "82_camera.mp4"
            bad_video.write_bytes(b"mp4-incompleto")
            good_video.write_bytes(b"mp4-valido")
            statuses = []

            def probe_side_effect(path, _timeout):
                if Path(path).name.startswith("81"):
                    raise ProbeError("moov atom not found; Invalid data found when processing input")
                return SimpleNamespace()

            with (
                patch("backend.watcher.wait_for_stable_file", return_value=True),
                patch("backend.watcher.probe_media", side_effect=probe_side_effect) as probe_mock,
                patch("backend.watcher.render_both") as render_mock,
            ):
                first = render_configured_candidates(cfg, statuses.append)
                second = render_configured_candidates(cfg, statuses.append)

            bad_probes = [call for call in probe_mock.call_args_list if Path(call.args[0]).name.startswith("81")]
            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertEqual(len(bad_probes), 1)
            render_mock.assert_not_called()
            self.assertTrue(any("Aguardando arquivo válido: 81_camera.mp4" in msg for msg in statuses))
            self.assertFalse(any(msg.lower().startswith("erro") for msg in statuses))

    def test_changed_incomplete_file_is_validated_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(
                project_root=temp_dir,
                input_dir="entrada",
                render_format="both",
                history_enabled=False,
                move_original_on_success=False,
            )
            cycle = cfg.input_path / "1310_MANUAL_TODAS"
            cycle.mkdir(parents=True)
            video_81 = cycle / "81_camera.mp4"
            video_82 = cycle / "82_camera.mp4"
            video_81.write_bytes(b"incompleto")
            video_82.write_bytes(b"valido")
            bad_attempts = 0

            def probe_side_effect(path, _timeout):
                nonlocal bad_attempts
                if Path(path).name.startswith("81"):
                    bad_attempts += 1
                    if bad_attempts == 1:
                        raise ProbeError("moov atom not found")
                return SimpleNamespace()

            render_result = SimpleNamespace(elapsed_seconds=2.0)
            with (
                patch("backend.watcher.wait_for_stable_file", return_value=True),
                patch("backend.watcher.probe_media", side_effect=probe_side_effect),
                patch("backend.watcher.render_both", return_value=render_result) as render_mock,
            ):
                self.assertEqual(render_configured_candidates(cfg), 0)
                video_81.write_bytes(b"arquivo-agora-finalizado")
                self.assertEqual(render_configured_candidates(cfg), 1)

            self.assertEqual(bad_attempts, 2)
            render_mock.assert_called_once()

    def test_invalid_pair_moves_to_quarantine_after_attempt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = AppConfig(
                project_root=temp_dir,
                input_dir="entrada",
                render_format="both",
                history_enabled=False,
                move_original_on_success=False,
                quarantine_attempts=2,
            )
            cycle = cfg.input_path / "1320_MANUAL_TODAS"
            cycle.mkdir(parents=True)
            video_81 = cycle / "81_camera.mp4"
            video_82 = cycle / "82_camera.mp4"
            video_81.write_bytes(b"corrompido")
            video_82.write_bytes(b"valido")
            clock = [0.0]

            def monotonic_clock():
                clock[0] += 1000.0
                return clock[0]

            def probe_side_effect(path, _timeout):
                if Path(path).name.startswith("81"):
                    raise ProbeError("moov atom not found")
                return SimpleNamespace()

            with (
                patch("backend.watcher.wait_for_stable_file", return_value=True),
                patch("backend.watcher.probe_media", side_effect=probe_side_effect),
                patch("backend.watcher.time.monotonic", side_effect=monotonic_clock),
                patch("backend.watcher.render_both") as render_mock,
            ):
                self.assertEqual(render_configured_candidates(cfg), 0)
                self.assertTrue(video_81.exists())
                self.assertEqual(render_configured_candidates(cfg), 0)

            render_mock.assert_not_called()
            self.assertFalse(video_81.exists())
            self.assertFalse(video_82.exists())
            quarantined = list(cfg.quarantine_path.rglob("*"))
            self.assertTrue(any(path.name == "81_camera.mp4" for path in quarantined))
            self.assertTrue(any(path.name == "82_camera.mp4" for path in quarantined))
            self.assertTrue(any(path.suffix == ".json" for path in quarantined))

    def test_pair_is_selected_for_both_mode(self) -> None:
        groups = candidate_video_groups(self._config())

        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].is_pair)
        self.assertEqual([path.name for path in groups[0].paths], ["81_camera.mp4", "82_camera.mp4"])

    def test_both_renders_pair_then_single_with_same_output_timestamp(self) -> None:
        cfg = self._config()
        input_81 = cfg.input_path / "1300_MANUAL_TODAS" / "81_camera.mp4"
        input_82 = cfg.input_path / "1300_MANUAL_TODAS" / "82_camera.mp4"
        pair_result = SimpleNamespace(output_file="saida/1300_RENDER/par.mp4", elapsed_seconds=8.0)
        single_result = SimpleNamespace(output_file="saida/1300_RENDER/individual.mp4", elapsed_seconds=7.5)

        with (
            patch("backend.renderer.render_pair", return_value=pair_result) as pair_mock,
            patch("backend.renderer.render_one", return_value=single_result) as single_mock,
        ):
            result = render_both(input_81, input_82, cfg)

        self.assertIs(result.pair_result, pair_result)
        self.assertIs(result.single_result, single_result)
        self.assertEqual(result.elapsed_seconds, 15.5)
        self.assertEqual(
            pair_mock.call_args.kwargs["render_started_at"],
            single_mock.call_args.kwargs["render_started_at"],
        )
        self.assertFalse(pair_mock.call_args.kwargs["record_history"])
        self.assertFalse(single_mock.call_args.kwargs["record_history"])
        self.assertFalse(pair_mock.call_args.kwargs["move_original"])
        self.assertFalse(single_mock.call_args.kwargs["move_original"])


if __name__ == "__main__":
    unittest.main()
