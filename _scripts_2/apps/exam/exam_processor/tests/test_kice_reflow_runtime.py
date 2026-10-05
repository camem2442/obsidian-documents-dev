"""Reflow runs on disposable PDF geometry mocks, without user reference PDFs."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from _scripts_2.apps.exam.exam_processor.ingestion.formats.korean import kice_korean


class KiceReflowRuntime(unittest.TestCase):
    def test_paragraph_question_and_source_mismatch(self):
        rows = [(10, "첫 문장."), (20, "둘째 문장.")]
        raw = "첫 문장.\n둘째 문장."
        for kind, reference, body, expected in [
            ("passage", raw, raw, "첫 문장.\n\n둘째 문장."),
            ("question", raw, raw, "첫 문장. 둘째 문장."),
            ("passage", "다른 원문", "보존할 초안", "보존할 초안"),
        ]:
            with self.subTest(kind=kind, reference=reference), tempfile.TemporaryDirectory() as temp:
                column = object()
                pdf = MagicMock()
                pdf.__enter__.return_value = SimpleNamespace(pages=[SimpleNamespace(crop=lambda bbox: column)])
                job = {"documents": {"problem": "fixture.pdf"}}
                item = {"kind": kind, "regions": [{"document": "problem", "page": 1, "bbox": [0, 0, 1, 1]}],
                        "reference_text": reference, "body": body}
                with patch.object(kice_korean.pdfplumber, "open", return_value=pdf), \
                     patch.object(kice_korean, "lines", return_value=rows), \
                     patch.object(kice_korean, "passage_breaks", return_value={20}) as paragraph_breaks:
                    result = kice_korean.reflow_saved_draft(job, item, Path(temp))
                self.assertEqual(result, expected)
                self.assertEqual(paragraph_breaks.call_count, int(kind == "passage"))
