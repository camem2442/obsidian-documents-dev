import unittest

from _scripts_2.apps.exam.exam_processor.text_layout import normalize_markdown


class TextLayoutTests(unittest.TestCase):
    def test_choices_and_korean_word_continuation(self):
        source = "① ㄱ을 활용하여, 최근의 관광 정책들이 지역 내 맛집 홍보에\n집중돼 있다는 것을, 국내 지역들이 진행 중인 노력이 음식\n관광에 머물러 있다고 보는 이유로 5문단을 보강한다.\n② ㄷ을 활용하여, 지역 대표 미식 식당이 관광객들의 여행 기간\n결정에 미치는 영향력을, 지역 식문화를 대표하는 미식 식당이\n갖춰야 할 요건으로 3문단에 추가한다."
        output = normalize_markdown(source)
        self.assertEqual(len([row for row in output.splitlines() if row]), 2)
        self.assertIn("음식 관광에", output)
        self.assertIn("\n②", output)
        self.assertNotIn("\n\n②", output)
        self.assertEqual(normalize_markdown(output), output)

    def test_stranded_korean_syllable_at_line_end(self):
        source = "청중의 질\n문을 듣고 그에 답하는 내\n용이지만, 위기 위험 정\n도에 따라 구분한다."
        output = normalize_markdown(source)
        self.assertIn("청중의 질문을", output)
        self.assertIn("답하는 내용이지만", output)
        self.assertIn("위기 위험 정도에", output)

    def test_passage_sections_and_real_paragraphs(self):
        source = "[43～45] 학생이 작성한 초고\n이다. 물음에 답하시오.\n[작문 상황]\n학생이 자신의 진로와 관련 있는\n사회 현상을 소개한다.\n[초고]\n여행 경험의 증가와\n요구의 다변화가 나타난다.\n\n두 번째 문단은\n새로운 내용이다."
        self.assertEqual(normalize_markdown(source), "[43～45] 학생이 작성한 초고이다. 물음에 답하시오.\n\n[작문 상황]\n학생이 자신의 진로와 관련 있는 사회 현상을 소개한다.\n\n[초고]\n여행 경험의 증가와 요구의 다변화가 나타난다.\n\n두 번째 문단은 새로운 내용이다.")

    def test_speakers_and_box_entries(self):
        source = "**진행자:** 제도를\n소개해주세요.\n**기자:** 내용을\n안내합니다.\n<보 기>\nㄱ. 전문가\n인터뷰\nㄴ. 연구 보고서\n① 정답을\n선택한다."
        output = normalize_markdown(source)
        self.assertIn("**진행자:** 제도를 소개해주세요.\n\n**기자:**", output)
        self.assertIn("<보 기>\nㄱ. 전문가 인터뷰\nㄴ. 연구 보고서\n\n①", output)

    def test_markdown_preserved(self):
        source = "<!-- MATERIAL:fig1 START -->\n![[assets/fig1.png]]\n\n| 항목 | 값 |\n| --- | --- |\n| A | 1 |\n<!-- MATERIAL:fig1 END -->\n\n> [!quote] 보기\n> <u>긴\n> 문장</u>\n>\n> 다음 문단\n\n```text\na\nb\n```\n\n$$\nx + y\n= 1\n$$"
        output = normalize_markdown(source)
        self.assertIn("| 항목 | 값 |\n| --- | --- |\n| A | 1 |", output)
        self.assertIn("> [!quote] 보기\n> <u>긴 문장</u>\n>\n> 다음 문단", output)
        self.assertIn("```text\na\nb\n```", output)
        self.assertIn("$$\nx + y\n= 1\n$$", output)
        self.assertEqual(normalize_markdown(output), output)
