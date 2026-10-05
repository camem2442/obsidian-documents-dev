import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor import launcher


class LauncherTests(unittest.TestCase):
    def test_rejects_external_urls(self):
        with patch.object(launcher.urllib.request, "urlopen") as request:
            self.assertFalse(launcher.is_ready("https://example.com"))
            self.assertFalse(launcher.is_ready("http://127.0.0.1:7893/other"))
            request.assert_not_called()

    def test_rejects_legacy_health_without_capabilities(self):
        payload = json.dumps({"app": "exam-processor", "data_root": str(launcher.config.DATA_ROOT.resolve())}).encode()
        response = unittest.mock.Mock()
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *args: None
        response.read = lambda: payload
        response.status = 200
        with patch.object(launcher.urllib.request, "urlopen", return_value=response):
            self.assertFalse(launcher.is_ready("http://127.0.0.1:7893"))

    def test_rejects_health_when_store_not_ready(self):
        payload = json.dumps({
            "app": "exam-processor",
            "data_root": str(launcher.config.DATA_ROOT.resolve()),
            "ready": False,
            "capabilities": list(launcher.REQUIRED_CAPABILITIES),
        }).encode()
        response = unittest.mock.Mock()
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *args: None
        response.read = lambda: payload
        response.status = 503
        with patch.object(launcher.urllib.request, "urlopen", return_value=response):
            self.assertFalse(launcher.is_ready("http://127.0.0.1:7893"))

    def test_start_and_reuse_same_server(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            children = []
            spawn = subprocess.Popen

            def start(*args, **kwargs):
                child = spawn(*args, **kwargs)
                children.append(child)
                return child

            try:
                with patch.object(launcher.config, "DATA_ROOT", root), \
                     patch.object(launcher, "SERVER_PORT", port), \
                     patch.object(launcher, "SERVER_URL", url), \
                     patch.dict(os.environ, {"EXAM_PROCESSOR_DATA": temp}), \
                     patch.object(launcher.subprocess, "Popen", side_effect=start):
                    started_url = launcher.ensure_server()
                    self.assertEqual(started_url, url)
                    self.assertTrue(launcher.is_ready(started_url))
                    self.assertEqual(launcher.ensure_server(), started_url)
                    self.assertEqual(len(children), 1)
                    self.assertEqual((root / "launcher-url.txt").read_text(), started_url)
            finally:
                for child in children:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()

    def test_catalog_entry_and_shortcuts(self):
        root = next(
            parent for parent in Path(__file__).resolve().parents
            if (parent / "program_catalog.json").is_file()
        )
        programs = json.loads((root / "program_catalog.json").read_text())["programs"]
        entry = next(p for p in programs if p["id"] == "exam-processor")
        self.assertTrue((root / entry["entrypoint"]).is_file())
        self.assertEqual(entry["launch_type"], "shell")
        shortcuts = [p["shortcut"] for p in programs if p.get("enabled") and p.get("shortcut")]
        self.assertEqual(len(shortcuts), len(set(shortcuts)))
