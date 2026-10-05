"""Documentation ownership inventory failures, without app/runtime writes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_docs.py'
spec = importlib.util.spec_from_file_location('exam_docs_verifier', SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class DocumentationInventoryTests(unittest.TestCase):
    def check_inventory(self, entries, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, text in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
            errors = []
            with patch.object(verifier, 'DOCS_ROOT', root), patch.object(verifier, 'DOC_INVENTORY', entries):
                verifier.check_doc_inventory(errors)
            return errors

    def test_missing_and_unregistered_documents_fail_even_when_counts_match(self):
        errors = self.check_inventory(
            [{'path': 'old.md', 'role': 'guide', 'status': None}],
            {'new.md': '# New\n'},
        )
        self.assertTrue(any('Missing declared' in error for error in errors))
        self.assertTrue(any('Unregistered' in error for error in errors))

    def test_proposal_is_not_a_current_contract(self):
        errors = self.check_inventory(
            [{'path': 'contract.md', 'role': 'contract', 'status': 'proposal'}],
            {'contract.md': '---\nstatus: proposal\n---\n'},
        )
        self.assertTrue(any('Contract must declare current' in error for error in errors))

    def test_status_drift_is_detected(self):
        errors = self.check_inventory(
            [{'path': 'plan.md', 'role': 'plan', 'status': 'proposal'}],
            {'plan.md': '---\nstatus: current\n---\n'},
        )
        self.assertTrue(any('Status mismatch' in error for error in errors))

    def test_duplicate_paths_and_unknown_roles_fail(self):
        entry = {'path': 'a.md', 'role': 'arbitrary', 'status': None}
        errors = self.check_inventory([entry, entry], {'a.md': '# A\n'})
        self.assertTrue(any('Duplicate' in error for error in errors))
        self.assertTrue(any('Unknown role' in error for error in errors))

    def test_evidence_and_proposal_have_distinct_valid_statuses(self):
        entries = [
            {'path': 'plan.md', 'role': 'plan', 'status': 'proposal'},
            {'path': 'review.md', 'role': 'evidence', 'status': 'historical'},
        ]
        files = {'plan.md': '---\nstatus: proposal\n---\n', 'review.md': '---\nstatus: historical\n---\n'}
        self.assertEqual([], self.check_inventory(entries, files))

    def test_current_contract_architecture_and_metadata_checks_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / 'contract.md'
            document.write_text('---\nstatus: proposal\narchitecture: wrong\n---\n')
            errors, warnings = [], []
            with patch.object(verifier, 'DOCS_ROOT', root), patch.object(verifier, 'ACTIVE_CONTRACT_DOCS', [document]):
                verifier.check_frontmatter_metadata(errors, warnings)
            self.assertEqual(2, len(errors))
            self.assertTrue(any('last_verified_commit' in warning for warning in warnings))


if __name__ == '__main__':
    unittest.main()
