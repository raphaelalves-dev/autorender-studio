from __future__ import annotations

import unittest

from backend.app_info import APP_USER_MODEL_ID, APP_VERSION
from frontend.ui import (
    WS_EX_APPWINDOW,
    WS_EX_TOOLWINDOW,
    _calculate_resized_geometry,
    _server_probe_target,
    _taskbar_extended_style,
    _validated_window_geometry,
    _visual_activity_state,
)


class TaskbarWindowTests(unittest.TestCase):
    def test_taskbar_style_adds_appwindow_and_removes_toolwindow(self) -> None:
        original = WS_EX_TOOLWINDOW | 0x00000100
        updated = _taskbar_extended_style(original)

        self.assertEqual(updated & WS_EX_APPWINDOW, WS_EX_APPWINDOW)
        self.assertEqual(updated & WS_EX_TOOLWINDOW, 0)
        self.assertEqual(updated & 0x00000100, 0x00000100)

    def test_app_user_model_id_is_stable_between_versions(self) -> None:
        self.assertEqual(APP_USER_MODEL_ID, "ClicksDaSerra.AutoRenderStudio")
        self.assertNotIn(APP_VERSION, APP_USER_MODEL_ID)

    def test_visual_activity_reflects_render_without_running_heavy_media(self) -> None:
        self.assertEqual(_visual_activity_state("Sistema pronto", False), "idle")
        self.assertEqual(_visual_activity_state("Auto iniciado: formato Ambos.", True), "monitoring")
        self.assertEqual(_visual_activity_state("Renderizando ambos", True), "rendering")
        self.assertEqual(_visual_activity_state("Enviando para a saída", True), "delivery")
        self.assertEqual(_visual_activity_state("ERRO no FFprobe", True), "error")

    def test_saved_window_geometry_is_validated_before_restore(self) -> None:
        self.assertEqual(
            _validated_window_geometry(
                "1320x780+120+80",
                screen_width=1920,
                screen_height=1080,
            ),
            "1320x780+120+80",
        )
        self.assertEqual(
            _validated_window_geometry("invalida", screen_width=1920, screen_height=1080),
            "1180x760",
        )

    def test_resize_from_left_preserves_opposite_edge_and_minimum_size(self) -> None:
        self.assertEqual(
            _calculate_resized_geometry(
                "w",
                delta_x=300,
                delta_y=0,
                x=100,
                y=50,
                width=1180,
                height=760,
            ),
            (300, 50, 980, 760),
        )

    def test_server_probe_uses_drive_or_unc_share_root(self) -> None:
        self.assertEqual(_server_probe_target(r"Z:\22-07-26"), "Z:\\")
        self.assertEqual(
            _server_probe_target(r"\\servidor\videos\22-07-26"),
            "\\\\servidor\\videos\\",
        )
        self.assertEqual(_server_probe_target(""), "")


if __name__ == "__main__":
    unittest.main()
