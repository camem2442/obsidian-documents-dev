import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QMimeData, QUrl, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QApplication

from _scripts_2.apps.documents.svg_converter.gui import SvgConverterWindow, TextEditPasteFilter


class SvgConverterPasteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        assert cls.app.platformName() == "offscreen", "native Qt platform forbidden"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="svg-paste-")
        self.addCleanup(self.tmp.cleanup)
        self.fixture_home = Path(self.tmp.name)
        # Existing bitmap-paste cache is HOME-based; keep its writes disposable too.
        home_patch = patch.object(Path, "home", return_value=self.fixture_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        provider_patch = patch("_scripts_2.apps.documents.svg_converter.engine.AIClient",
                               side_effect=AssertionError("real provider construction forbidden"))
        self.provider_mock = provider_patch.start()
        self.addCleanup(provider_patch.stop)
        self.addCleanup(self.provider_mock.assert_not_called)
        for target in ("socket.socket.connect", "socket.getaddrinfo", "socket.create_connection"):
            network_patch = patch(target, side_effect=AssertionError("network attempt forbidden"))
            network_mock = network_patch.start()
            self.addCleanup(network_patch.stop)
            self.addCleanup(network_mock.assert_not_called)
        self.win = SvgConverterWindow(log_dir=self.fixture_home / "logs")

    def tearDown(self):
        self.win.close()

    def test_paste_direct_image_from_clipboard(self):
        """Test Cmd+V with a bitmap QImage directly in clipboard (e.g. screenshot)."""
        img = QImage(120, 80, QImage.Format.Format_RGB32)
        img.fill(QColor("blue"))

        cb = self.app.clipboard()
        cb.setImage(img)

        # Execute paste
        pasted = self.win._paste_from_clipboard()
        self.assertTrue(pasted)
        self.assertIsNotNone(self.win.current_image_path)
        self.assertTrue(self.win.current_image_path.is_file())
        self.assertTrue(self.win.btn_convert.isEnabled())
        self.assertIn("클립보드 이미지", self.win.lbl_file_info.text())
        self.assertIn("120×80px", self.win.lbl_file_info.text())

    def test_paste_file_url_from_clipboard(self):
        """Test Cmd+V with a file URL copied in Finder."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            temp_path = Path(f.name)

        try:
            img = QImage(64, 64, QImage.Format.Format_RGB32)
            img.fill(QColor("green"))
            img.save(str(temp_path), "PNG")

            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(temp_path))])
            self.app.clipboard().setMimeData(mime)

            pasted = self.win._paste_from_clipboard()
            self.assertTrue(pasted)
            self.assertEqual(self.win.current_image_path, temp_path)
            self.assertTrue(self.win.btn_convert.isEnabled())
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_paste_file_path_text_from_clipboard(self):
        """Test Cmd+V when user copied a file path string."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            temp_path = Path(f.name)

        try:
            img = QImage(32, 32, QImage.Format.Format_RGB32)
            img.fill(QColor("red"))
            img.save(str(temp_path), "JPEG")

            self.app.clipboard().setText(str(temp_path))

            pasted = self.win._paste_from_clipboard()
            self.assertTrue(pasted)
            self.assertEqual(self.win.current_image_path, temp_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_paste_empty_or_text_only(self):
        """Test paste when clipboard only has non-path plain text."""
        self.app.clipboard().setText("Just regular note text")
        pasted = self.win._paste_from_clipboard()
        self.assertFalse(pasted)
        self.assertIn("없습니다", self.win.lbl_status.text())

    def test_btn_paste_click(self):
        """Test clicking the '붙여넣기' button triggers image load."""
        img = QImage(50, 50, QImage.Format.Format_RGB32)
        img.fill(QColor("yellow"))
        self.app.clipboard().setImage(img)

        self.win.btn_paste.click()
        self.assertIsNotNone(self.win.current_image_path)
        self.assertTrue(self.win.btn_convert.isEnabled())

    def test_paste_in_text_edit_with_image_routes_to_converter(self):
        """When text edit is focused and clipboard has an image, Cmd+V routes to image load."""
        img = QImage(40, 40, QImage.Format.Format_RGB32)
        img.fill(QColor("cyan"))
        self.app.clipboard().setImage(img)

        self.win.edit_context.setFocus()
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        handled = self.win._paste_filter.eventFilter(self.win.edit_context, event)
        self.assertTrue(handled)
        self.assertIsNotNone(self.win.current_image_path)

    def test_paste_in_text_edit_with_text_stays_in_text_edit(self):
        """When text edit is focused and clipboard has text, it is not intercepted."""
        self.app.clipboard().setText("Some context description")
        self.win.edit_context.setFocus()
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        handled = self.win._paste_filter.eventFilter(self.win.edit_context, event)
        self.assertFalse(handled)


if __name__ == "__main__":
    unittest.main()
