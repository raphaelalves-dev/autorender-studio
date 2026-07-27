from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import AppConfig
from backend.delivery import (
    DeliveryTicket,
    _deliver_once,
    _write_manifest,
    defer_deliveries_until_batch_complete,
    deliveries_are_deferred,
    queue_delivery,
    should_stage_output,
)


class DeliveryTests(unittest.TestCase):
    def test_delivery_waits_until_render_batch_finishes(self) -> None:
        self.assertFalse(deliveries_are_deferred())
        with defer_deliveries_until_batch_complete():
            self.assertTrue(deliveries_are_deferred())
            with defer_deliveries_until_batch_complete():
                self.assertTrue(deliveries_are_deferred())
            self.assertTrue(deliveries_are_deferred())
        self.assertFalse(deliveries_are_deferred())

    def test_output_on_another_drive_uses_local_staging(self) -> None:
        cfg = AppConfig(project_root="C:\\AutoRender")
        self.assertTrue(should_stage_output(Path("Z:\\21-07-26\\video.mp4"), cfg))
        self.assertFalse(should_stage_output(Path("C:\\saida\\video.mp4"), cfg))

    def test_each_finished_folder_keeps_its_own_delivery_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cfg = AppConfig(project_root=folder)
            first_local = Path(folder) / "primeira.mp4"
            second_local = Path(folder) / "segunda.mp4"
            first_local.write_bytes(b"primeira")
            second_local.write_bytes(b"segunda")

            with patch("backend.delivery._enqueue"):
                with defer_deliveries_until_batch_complete():
                    first = queue_delivery(
                        first_local, Path(folder) / "saida" / "primeira.mp4", cfg
                    )
                    self.assertFalse(first.ready_event.is_set())

                self.assertTrue(first.ready_event.is_set())

                with defer_deliveries_until_batch_complete():
                    second = queue_delivery(
                        second_local, Path(folder) / "saida" / "segunda.mp4", cfg
                    )
                    self.assertTrue(first.ready_event.is_set())
                    self.assertFalse(second.ready_event.is_set())

                self.assertTrue(second.ready_event.is_set())

    def test_delivery_copies_atomically_and_removes_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / "staging" / "video.mp4"
            final = root / "saida" / "video.mp4"
            local.parent.mkdir(parents=True)
            local.write_bytes(b"video-final")
            manifest = _write_manifest(local, final)

            _deliver_once(DeliveryTicket(local, final, manifest))

            self.assertEqual(b"video-final", final.read_bytes())
            self.assertFalse(local.exists())
            self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()
