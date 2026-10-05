import unittest

from _scripts_2.apps.exam.exam_processor.domain.math_quality import (
    inspect_math,
    normalize_math_section,
    prepare_audit_suggestion,
    repair_unwrapped_latex,
)


class MathQualityTests(unittest.TestCase):
    def test_repairs_missing_commands_only_inside_math(self):
        text, checks = inspect_math("본문 times `$2imes$`와 $herefore2imes5!=240$", "solution")
        self.assertIn("본문 times", text)
        self.assertIn(r"$2\times$", text)
        self.assertIn(r"$\therefore2\times5!=240$", text)
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(check["review_required"] for check in checks))

    def test_keeps_valid_tex(self):
        text, checks = inspect_math(r"$2\times 3$", "body")
        self.assertEqual(text, r"$2\times 3$")
        self.assertEqual(checks, [])

    def test_prepare_audit_suggestion_wraps_bare_latex(self):
        sug = r"{_nP_r} = \underbrace{n(n-1)}_{r\text{개}} = \frac{n!}{(n-r)!}"
        ext = r"$_nP_r = \underbrace{n(n-1)}_{r\text{개}} = \frac{n!}{(n-r)!}$"
        wrapped = prepare_audit_suggestion(sug, ext)
        self.assertTrue(wrapped.startswith("$") and wrapped.endswith("$"))
        self.assertIn(r"\underbrace", wrapped)

    def test_repair_unwrapped_latex_in_callout_line(self):
        line = (
            r"> = {_nP_r} = \underbrace{n(n-1)(n-2) \cdots (n-r+1)}_{r\text{개}} "
            r"= \frac{n!}{(n-r)!}"
        )
        fixed = repair_unwrapped_latex(line)
        self.assertIn(r"$= {_nP_r} = \underbrace", fixed)
        self.assertNotIn(r"$ \underbrace", fixed)

    def test_normalize_math_section_after_audit_apply(self):
        body = (
            "순열\n\n"
            r"> = {_nP_r} = \underbrace{n(n-1)}_{r\text{개}} = \frac{n!}{(n-r)!}"
        )
        text, checks = normalize_math_section(body, "body")
        self.assertIn(r"> $= {_nP_r} = \underbrace", text)
        self.assertNotIn("순열$", text)
        self.assertEqual(checks, [])
