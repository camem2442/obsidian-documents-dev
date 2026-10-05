import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

from _scripts_2.apps.documents.svg_converter.engine import SvgConverter, SvgOptions, SvgResult
from _scripts_2.apps.documents.svg_converter.gui import SvgConverterWindow


class SvgConverterLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        assert cls.app.platformName() == "offscreen", "native Qt platform forbidden"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="svg-logging-")
        self.addCleanup(self.tmp.cleanup)
        self.log_dir = Path(self.tmp.name) / "logs"
        # Even a swallowed Path.home() assertion must fail this test at cleanup.
        self.home_guard = patch.object(Path, "home", side_effect=AssertionError("production HOME resolution forbidden"))
        self.home_mock = self.home_guard.start()
        self.addCleanup(self.home_guard.stop)
        self.addCleanup(self.home_mock.assert_not_called)
        self.provider_guard = patch("_scripts_2.apps.documents.svg_converter.engine.AIClient",
                                    side_effect=AssertionError("real provider construction forbidden"))
        self.provider_mock = self.provider_guard.start()
        self.addCleanup(self.provider_guard.stop)
        self.addCleanup(self.provider_mock.assert_not_called)
        for target in ("socket.socket.connect", "socket.getaddrinfo", "socket.create_connection"):
            network_patch = patch(target, side_effect=AssertionError("network attempt forbidden"))
            network_mock = network_patch.start()
            self.addCleanup(network_patch.stop)
            self.addCleanup(network_mock.assert_not_called)
        self.win = SvgConverterWindow(log_dir=self.log_dir)

    def tearDown(self):
        self.win.close()

    def test_log_tab_exists(self):
        """Verify the 3rd tab is '📜 실시간 변환 로그'."""
        self.assertEqual(self.win.tabs.count(), 3)
        self.assertEqual(self.win.tabs.tabText(2), "📜 실시간 변환 로그")
        self.assertIsNotNone(self.win.edit_log)
        self.assertIsNotNone(self.win.btn_save_log)
        self.assertIsNotNone(self.win.btn_copy_log)
        self.assertIsNotNone(self.win.btn_open_log_folder)
        self.assertIsNotNone(self.win.btn_clear_log)

    def test_append_log_and_persistent_write(self):
        """Verify _append_log writes to UI and appends to persistent cache log file."""
        test_msg = "테스트 로그 메시지 12345"
        self.win._append_log(test_msg)

        content = self.win.edit_log.toPlainText()
        self.assertIn(test_msg, content)

        today_str = datetime.date.today().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"svg_converter_{today_str}.log"
        self.assertTrue(log_file.exists())
        file_text = log_file.read_text(encoding="utf-8")
        self.assertIn(test_msg, file_text)

    def test_clear_log(self):
        """Verify _clear_log resets log view."""
        self.win._append_log("Temp message")
        log_file = next(self.log_dir.glob("*.log"))
        disk_before = log_file.read_bytes()
        self.win._clear_log()
        self.assertEqual(log_file.read_bytes(), disk_before)
        self.assertEqual(self.win.edit_log.toPlainText(), "")

    def test_copy_log(self):
        """Verify _copy_log puts log text into clipboard."""
        self.win._append_log("Clipboard test log")
        self.win._copy_log()
        cb_text = self.app.clipboard().text()
        self.assertIn("Clipboard test log", cb_text)

    def test_resolution_and_construction_are_read_only(self):
        self.assertEqual(self.win._persistent_log_dir(), self.log_dir)
        self.assertFalse(self.log_dir.exists())

    def test_default_path_compatibility_without_real_home(self):
        fixture_home = Path(self.tmp.name) / "default-home"
        with patch.object(Path, "home", return_value=fixture_home):
            default_window = SvgConverterWindow()
            try:
                expected = fixture_home / ".cache" / "svg_converter" / "logs"
                self.assertEqual(default_window._persistent_log_dir(), expected)
                self.assertFalse(expected.exists())
                default_window._append_log("default semantics marker")
                log_file = expected / f"svg_converter_{datetime.date.today():%Y-%m-%d}.log"
                self.assertIn("default semantics marker", log_file.read_text(encoding="utf-8"))
            finally:
                default_window.close()

    def test_two_windows_append_to_same_dated_file(self):
        other = SvgConverterWindow(log_dir=self.log_dir)
        try:
            self.win._append_log("first window marker")
            other._append_log("second window marker")
            files = list(self.log_dir.glob("*.log"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("first window marker", text)
            self.assertIn("second window marker", text)
        finally:
            other.close()

    def test_open_folder_uses_explicit_root(self):
        with patch("_scripts_2.apps.documents.svg_converter.gui.QDesktopServices.openUrl", return_value=True) as opened:
            self.win._open_log_folder()
        self.assertTrue(self.log_dir.is_dir())
        self.assertEqual(opened.call_args[0][0].toLocalFile(), str(self.log_dir))

    def test_append_failures_keep_ui_logging_and_do_not_raise(self):
        for target in ("pathlib.Path.mkdir", "builtins.open"):
            with self.subTest(target=target), patch(target, side_effect=PermissionError("fixture failure")):
                self.win._append_log("failure still visible")
                self.assertIn("failure still visible", self.win.edit_log.toPlainText())

    def test_open_folder_mkdir_failure_is_reported_without_launch(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("fixture failure")), \
                patch("_scripts_2.apps.documents.svg_converter.gui.QDesktopServices.openUrl") as opened:
            self.win._open_log_folder()
        opened.assert_not_called()
        self.assertIn("만들 수 없습니다", self.win.lbl_status.text())
        self.assertEqual(self.win.edit_log.toPlainText(), "")

    def test_open_folder_rejected_is_reported(self):
        with patch("_scripts_2.apps.documents.svg_converter.gui.QDesktopServices.openUrl", return_value=False) as opened:
            self.win._open_log_folder()
        opened.assert_called_once()
        self.assertTrue(self.log_dir.is_dir())
        self.assertIn("열 수 없습니다", self.win.lbl_status.text())

    def test_open_folder_can_retry_after_rejection(self):
        with patch("_scripts_2.apps.documents.svg_converter.gui.QDesktopServices.openUrl", side_effect=[False, True]):
            self.win._open_log_folder()
            self.assertIn("열 수 없습니다", self.win.lbl_status.text())
            self.win._open_log_folder()
        self.assertEqual(self.win.lbl_status.text(), f"📂 로그 폴더 열기: {self.log_dir}")

    def test_open_folder_existing_file_is_preserved(self):
        self.log_dir.write_bytes(b"existing file must survive")
        with patch("_scripts_2.apps.documents.svg_converter.gui.QDesktopServices.openUrl") as opened:
            self.win._open_log_folder()
        opened.assert_not_called()
        self.assertEqual(self.log_dir.read_bytes(), b"existing file must survive")
        self.assertIn("만들 수 없습니다", self.win.lbl_status.text())

    def test_engine_log_callback(self):
        """Verify engine's convert_image calls log_callback during processing."""
        logs = []
        mock_client = MagicMock()
        mock_client.generate_images.return_value = "<svg><rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/><path d=\"M10 10\"/></svg>"

        converter = SvgConverter(ai_client=mock_client)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            temp_path = Path(f.name)

        try:
            temp_path.write_bytes(b"\x89PNG\r\n\x1a\n...")
            opts = SvgOptions(validate=False, auto_repair=False)
            res = converter.convert_image(temp_path, options=opts, log_callback=logs.append)
            self.assertTrue(len(logs) > 0)
            self.assertTrue(any("AI 요청 전송 중" in m and "svg_graph (precision)" in m for m in logs))
            self.assertTrue(any("AI 응답 수신 완료" in m and "수신 데이터:" in m for m in logs))
            mock_client.generate_images.assert_called_once()
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
