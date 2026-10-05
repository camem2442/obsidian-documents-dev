import unittest

from _scripts_2.apps.exam.exam_processor.pipeline.concept_extract import (
    flatten_concept_material_body,
    merge_concept_diagrams_into_body,
)


class ConceptExtractTests(unittest.TestCase):
    def test_merge_diagrams_into_body_clears_assets(self):
        data = {
            "body": "서문\n\n{{diagram:fig1}}\n\n후속",
            "diagrams": [{"id": "fig1", "region": 0, "box": [0, 0, 500, 500],
                          "markdown": "**제목**\n\n본문"}],
        }
        merged, flag = merge_concept_diagrams_into_body(data)
        self.assertTrue(flag)
        self.assertEqual(merged["diagrams"], [])
        self.assertNotIn("{{diagram:", merged["body"])
        self.assertIn("**제목**", merged["body"])
        self.assertIn("서문", merged["body"])
        self.assertIn("후속", merged["body"])

    def test_flatten_material_removes_image_link(self):
        body = (
            "앞\n\n<!-- MATERIAL:fig1 START -->\n"
            "![[assets/x/fig1.png]]\n\n"
            "교과서 개념\n\n등식\n"
            "<!-- MATERIAL:fig1 END -->\n\n뒤"
        )
        flat = flatten_concept_material_body(body)
        self.assertNotIn("MATERIAL:", flat)
        self.assertNotIn("![[", flat)
        self.assertIn("교과서 개념", flat)
        self.assertIn("등식", flat)
        self.assertIn("앞", flat)
        self.assertIn("뒤", flat)


if __name__ == "__main__":
    unittest.main()
