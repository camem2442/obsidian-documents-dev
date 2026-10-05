import unittest

try:
    from _scripts_2.apps.exam.exam_processor.ingestion.curriculum import (
        PROBABILITY_STATISTICS_CURRICULUM_ID,
        exam_ai_classification_plan,
        workbook_toc_classification,
    )
except ImportError:
    from exam_processor.ingestion.curriculum import (
        PROBABILITY_STATISTICS_CURRICULUM_ID,
        exam_ai_classification_plan,
        workbook_toc_classification,
    )


class CurriculumClassificationTests(unittest.TestCase):
    def test_confirmed_workbook_toc_maps_known_section(self):
        result = workbook_toc_classification("2-2", "조건부확률")
        self.assertEqual(result["classification_status"], "confirmed")
        self.assertEqual(result["classification_method"], "workbook_toc_confirmed")
        self.assertEqual(result["curriculum_id"], PROBABILITY_STATISTICS_CURRICULUM_ID)
        self.assertIn("curriculum_unit:ps.conditional-probability", result["classification_evidence"])

    def test_unknown_workbook_section_stays_unclassified(self):
        result = workbook_toc_classification("G", "핵심", "경우의 수")
        self.assertEqual(result["classification_status"], "confirmed")
        self.assertEqual(result["classification_method"], "workbook_toc_confirmed")
        self.assertEqual(result["curriculum_id"], "")

    def test_exam_plan_is_candidate_path_without_candidates(self):
        result = exam_ai_classification_plan("수학")
        self.assertEqual(result["classification_status"], "unclassified")
        self.assertEqual(result["classification_method"], "curriculum_ai_candidate")
        self.assertEqual(result["classification_candidates"], [])


if __name__ == "__main__":
    unittest.main()
