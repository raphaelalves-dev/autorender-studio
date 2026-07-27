from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from backend.config import AppConfig
from backend.daily_cleanup import (
    _is_older_than_today,
    cleanup_processed_before_today,
    cleanup_quarantine_before_today,
)


class DailyCleanupTests(unittest.TestCase):
    def test_only_items_before_current_day_are_eligible(self) -> None:
        today = date(2026, 7, 15)
        yesterday = datetime(2026, 7, 14, 23, 59, 59).timestamp()
        current = datetime(2026, 7, 15, 0, 0, 0).timestamp()

        self.assertTrue(_is_older_than_today(yesterday, today))
        self.assertFalse(_is_older_than_today(current, today))

    def test_cleanup_does_not_repeat_when_already_completed_today(self) -> None:
        cfg = AppConfig(project_root=".", processed_cleanup_last_run="2026-07-15")

        result = cleanup_processed_before_today(cfg, today=date(2026, 7, 15))

        self.assertFalse(result.performed)
        self.assertTrue(result.completed)

    def test_cleanup_removes_previous_day_and_preserves_current_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_item = root / "processados" / "render_ontem"
            current_item = root / "processados" / "render_hoje"
            old_item.mkdir(parents=True)
            current_item.mkdir(parents=True)
            old_video = old_item / "video.mp4"
            current_video = current_item / "video.mp4"
            old_video.write_bytes(b"old-render")
            current_video.write_bytes(b"current-render")

            old_timestamp = datetime(2026, 7, 14, 23, 0).timestamp()
            current_timestamp = datetime(2026, 7, 15, 10, 0).timestamp()
            for path in (old_video, old_item):
                os.utime(path, (old_timestamp, old_timestamp))
            for path in (current_video, current_item):
                os.utime(path, (current_timestamp, current_timestamp))

            cfg = AppConfig(project_root=root)
            result = cleanup_processed_before_today(cfg, today=date(2026, 7, 15))

            self.assertTrue(result.completed)
            self.assertFalse(old_item.exists())
            self.assertTrue(current_item.exists())
            self.assertEqual("2026-07-15", cfg.processed_cleanup_last_run)

    def test_quarantine_is_removed_on_the_following_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_day = root / "quarentena" / "2026-07-14"
            current_day = root / "quarentena" / "2026-07-15"
            old_day.mkdir(parents=True)
            current_day.mkdir(parents=True)
            old_file = old_day / "video_invalido.mp4"
            current_file = current_day / "video_em_analise.mp4"
            old_file.write_bytes(b"old")
            current_file.write_bytes(b"current")

            old_timestamp = datetime(2026, 7, 14, 23, 0).timestamp()
            current_timestamp = datetime(2026, 7, 15, 10, 0).timestamp()
            for path in (old_file, old_day):
                os.utime(path, (old_timestamp, old_timestamp))
            for path in (current_file, current_day):
                os.utime(path, (current_timestamp, current_timestamp))

            cfg = AppConfig(project_root=root)
            result = cleanup_quarantine_before_today(cfg, today=date(2026, 7, 15))

            self.assertTrue(result.completed)
            self.assertFalse(old_day.exists())
            self.assertTrue(current_day.exists())
            self.assertEqual("2026-07-15", cfg.quarantine_cleanup_last_run)


if __name__ == "__main__":
    unittest.main()
