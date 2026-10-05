"""Unit tests for ExamSchemeResolver (KICE historical exam structure SSOT)."""
from __future__ import annotations

import unittest

from _scripts_2.apps.exam.exam_processor.domain.services.exam_scheme_resolver import (
    ExamSchemeResolver,
)


class TestExamSchemeResolver(unittest.TestCase):
    def test_2022_2027_korean_common_and_electives(self) -> None:
        # Common Reading / Literature (1..34)
        req_common = ExamSchemeResolver.resolve("2026", "09", "korean", "15")
        self.assertTrue(req_common.is_supported)
        self.assertTrue(req_common.is_valid_session)
        self.assertTrue(req_common.is_valid_question_range)
        self.assertFalse(req_common.needs_track)
        self.assertFalse(req_common.needs_exam_form)
        self.assertEqual(req_common.default_track, "")

        # Elective Speech & Writing / Lang & Media (35..45)
        req_elec = ExamSchemeResolver.resolve("2026", "09", "korean", "35")
        self.assertTrue(req_elec.is_supported)
        self.assertTrue(req_elec.needs_track)
        self.assertIsNone(req_elec.default_track)

    def test_2022_2027_math_common_and_electives(self) -> None:
        # Common Math I & II (1..22)
        req_common = ExamSchemeResolver.resolve("2024", "06", "math", "20")
        self.assertTrue(req_common.is_supported)
        self.assertFalse(req_common.needs_track)
        self.assertFalse(req_common.needs_exam_form)
        self.assertEqual(req_common.default_track, "")

        # Elective Calculus / ProbStat / Geometry (23..30)
        req_elec = ExamSchemeResolver.resolve("2024", "06", "math", "29")
        self.assertTrue(req_elec.is_supported)
        self.assertTrue(req_elec.needs_track)
        self.assertIsNone(req_elec.default_track)

    def test_2017_2021_korean_integrated_and_math_ga_na(self) -> None:
        # Korean is fully integrated (no track, no form needed)
        req_kor = ExamSchemeResolver.resolve("2018", "09", "korean", "13")
        self.assertTrue(req_kor.is_supported)
        self.assertFalse(req_kor.needs_track)
        self.assertFalse(req_kor.needs_exam_form)
        self.assertEqual(req_kor.default_track, "")

        # Math requires exam_form (Ga/Na) for all questions 1..30
        req_math = ExamSchemeResolver.resolve("2018", "09", "math", "21")
        self.assertTrue(req_math.is_supported)
        self.assertFalse(req_math.needs_track)
        self.assertTrue(req_math.needs_exam_form)

    def test_2015_2016_ab_differentiated(self) -> None:
        req_kor = ExamSchemeResolver.resolve("2015", "11", "korean", "10")
        self.assertTrue(req_kor.is_supported)
        self.assertTrue(req_kor.needs_exam_form)

        req_eng = ExamSchemeResolver.resolve("2015", "11", "english", "33")
        self.assertTrue(req_eng.is_supported)
        self.assertFalse(req_eng.needs_exam_form)  # English integrated in 2015

    def test_2014_level_differentiated(self) -> None:
        # 2014 was A/B differentiated for Korean, Math, AND English
        req_eng = ExamSchemeResolver.resolve("2014", "11", "english", "33")
        self.assertTrue(req_eng.is_supported)
        self.assertTrue(req_eng.needs_exam_form)

    def test_2028_reform_integrated_and_session_08(self) -> None:
        # 2028: Integrated (no track, no form)
        req_math = ExamSchemeResolver.resolve("2028", "08", "math", "29")
        self.assertTrue(req_math.is_supported)
        self.assertTrue(req_math.is_valid_session)  # August session is valid in 2028!
        self.assertFalse(req_math.needs_track)
        self.assertFalse(req_math.needs_exam_form)

        # 09 session in 2028 is invalid (replaced by 08)
        req_math_09 = ExamSchemeResolver.resolve("2028", "09", "math", "29")
        self.assertFalse(req_math_09.is_valid_session)

    def test_unsupported_pre2014_and_post2028(self) -> None:
        # Pre-2014 is unsupported (fail-closed)
        req_old = ExamSchemeResolver.resolve("2013", "09", "math", "21")
        self.assertFalse(req_old.is_supported)
        self.assertFalse(req_old.is_fully_valid)

        # Post-2028 is unsupported (fail-closed)
        req_fut = ExamSchemeResolver.resolve("2030", "08", "math", "21")
        self.assertFalse(req_fut.is_supported)
        self.assertFalse(req_fut.is_fully_valid)

    def test_question_range_validation(self) -> None:
        # Math max is 30, Q31 is invalid
        req_q31 = ExamSchemeResolver.resolve("2026", "09", "math", "31")
        self.assertFalse(req_q31.is_valid_question_range)
        self.assertFalse(req_q31.is_fully_valid)

        # Korean max is 45, Q46 is invalid
        req_q46 = ExamSchemeResolver.resolve("2026", "09", "korean", "46")
        self.assertFalse(req_q46.is_valid_question_range)


if __name__ == "__main__":
    unittest.main()
