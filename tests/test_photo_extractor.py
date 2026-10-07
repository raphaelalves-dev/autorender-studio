from __future__ import annotations

import subprocess
import logging
import shutil
import tempfile
import unittest
from pathlib import Path

from backend.config import AppConfig, app_config_from_dict
from backend.ffmpeg_builder import require_ffmpeg
from backend.photo_extractor import camera_name, extract_photos, photo_times
from frontend.ui import parse_photo_centers


class PhotoExtractorTests(unittest.TestCase):
    def test_config_and_triplets(self) -> None:
        cfg = app_config_from_dict({
            "photo_enabled": True,
            "photo_group_count": 2,
            "photo_center_seconds": [10, 25],
        })
        self.assertEqual(photo_times(cfg), [(9, 10, 11), (24, 25, 26)])
        self.assertEqual(camera_name(Path("85_GL017292.mp4")), "85")
        self.assertEqual(camera_name(Path("outra.mp4")), "SEM_ID")
        self.assertEqual(parse_photo_centers(["10", " 25 "]), [10, 25])
        self.assertEqual(parse_photo_centers(["0"]), [0])
        self.assertEqual(app_config_from_dict({"photo_group_count": 1, "photo_center_seconds": [0]}).photo_center_seconds, [0])
        for values in (["0", "10"], ["10", "10"], ["texto"], []):
            with self.assertRaises(ValueError):
                parse_photo_centers(values)

    def test_extracts_three_jpegs_from_original_and_skips_out_of_range_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "85_camera.mp4"
            output = root / "saida" / "render.mp4"
            subprocess.run([
                require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=80x60:rate=2:duration=4",
                "-c:v", "mpeg4", str(source),
            ], check=True, capture_output=True)
            second_source = root / "86_camera.mp4"
            shutil.copyfile(source, second_source)
            cfg = AppConfig(
                project_root=temp, photo_enabled=True, photo_group_count=2,
                photo_center_seconds=[1, 5], local_staging_enabled=False,
            )
            report = extract_photos([source, second_source], output, cfg)
            self.assertEqual(len(report.created), 6)
            self.assertEqual(len(report.warnings), 2)
            self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in report.created))
            self.assertEqual(
                [path.name for path in report.created],
                [
                    "render__85__85_camera__0000s.jpg", "render__85__85_camera__0001s.jpg", "render__85__85_camera__0002s.jpg",
                    "render__86__86_camera__0000s.jpg", "render__86__86_camera__0001s.jpg", "render__86__86_camera__0002s.jpg",
                ],
            )
            self.assertTrue(all(path.parent == root / "saida" / "fotos" for path in report.created))
            logger = logging.getLogger("autorender")
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_zero_extracts_each_second_into_one_folder_per_camera(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "85_camera.mp4"
            second_source = root / "86_camera.mp4"
            output = root / "saida" / "render.mp4"
            subprocess.run([
                require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=80x60:rate=10:duration=4.4",
                "-c:v", "mpeg4", str(source),
            ], check=True, capture_output=True)
            shutil.copyfile(source, second_source)
            cfg = AppConfig(project_root=temp, photo_enabled=True, photo_group_count=1,
                            photo_center_seconds=[0], local_staging_enabled=False)
            self.assertEqual(photo_times(cfg), [])
            report = extract_photos([source, second_source], output, cfg)
            self.assertEqual(report.warnings, [])
            self.assertEqual(len(report.created), 10)
            self.assertEqual({path.parent.name for path in report.created}, {"85", "86"})
            self.assertTrue(all(path.parent.parent == root / "saida" / "fotos" for path in report.created))
            for camera in ("85", "86"):
                self.assertEqual(
                    [path.name for path in report.created if path.parent.name == camera],
                    [f"render__{camera}__{camera}_camera__{second:04d}s.jpg" for second in range(5)],
                )
            self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in report.created))
            logger = logging.getLogger("autorender")
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
