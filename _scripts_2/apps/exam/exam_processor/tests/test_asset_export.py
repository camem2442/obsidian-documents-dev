import copy
import json
import re
import shutil
import tempfile
import unittest
import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from _scripts_2.apps.exam.exam_processor.models import digest, new_item
from _scripts_2.apps.exam.exam_processor.set_export import write_sets, verify_export_assets
from _scripts_2.apps.exam.exam_processor.exporters.rebuild import rebuild_markdown_batch
from _scripts_2.apps.exam.exam_processor.ingestion.asset_crops import complete_image_crop


class AssetExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "output"
        self.stage.mkdir()
        self.item = new_item("source", "35-37", "35", "문제")
        self.item["review"] = "approved"
        self.job = {"source": "검증", "items": [self.item]}

    def asset(self, owner, section, filename, color, size=(30,30)):
        relative = f"assets/{owner}/{filename}"
        source = self.root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, color).save(source)
        name = f"fig{len(self.item['assets'])+1}"
        self.item["assets"].append({"id": name, "path": relative, "section": section})
        self.item[section] += f"\n<!-- MATERIAL:{name} START -->\n![[{relative}]]\n자료 전사\n<!-- MATERIAL:{name} END -->\n"

    def test_same_name_body_solution_and_revisions_keep_distinct_bytes(self):
        self.asset("one", "body", "fig1.png", "red")
        self.asset("two", "solution", "fig1.png", "blue")
        self.asset("three", "body", "fig1.png", "green")
        self.asset("four", "body", "fig1-2.png", "yellow")
        write_sets(self.stage, self.job, "TEST", self.root)
        manifest = json.loads((self.stage/"asset-manifest.json").read_text())
        self.assertEqual(len({m["output"] for m in manifest}),4)
        for entry in manifest:
            self.assertEqual(digest(self.root/entry["source"]),digest(self.stage/entry["output"]))
        combined=(self.stage/"TEST 1/TEST_1.md").read_text()
        self.assertNotIn("/solution/",combined)
        self.assertIn("/solution/",(self.stage/"TEST 1/TEST35.md").read_text())
        sidecar = json.loads((self.stage/"TEST 1/TEST35.json").read_text())
        self.assertEqual(sidecar["question"]["display_code"], "TEST35")
        for section in ("body", "solution"):
            for link in re.findall(r"!\[\[([^\]]+)\]\]", sidecar[section]):
                self.assertTrue((self.stage / "TEST 1" / link).is_file(), link)
                self.assertNotIn("assets/one/", link)

    def test_tall_passage_image_and_all_material_markers_survive(self):
        self.item = new_item("source", "35-37", "35-37", "문제", kind="passage")
        self.item["review"] = "approved"
        self.item["number"] = "35-37"
        self.job = {"source": "검증", "items": [self.item]}
        self.asset("one", "body", "fig1.png", "white", (500,2000))
        self.asset("two", "body", "fig2.png", "black")
        question = new_item("source","35-37","35","문제")
        question["review"] = "approved"
        self.job["items"].append(question)
        before=copy.deepcopy(self.job)
        write_sets(self.stage,self.job,"TEST",self.root)
        combined=(self.stage/"TEST 1/TEST_1.md").read_text()
        self.assertEqual(combined.count("<!-- MATERIAL:"),4)
        self.assertEqual(len(list(self.stage.rglob("*.png"))),2)
        self.assertEqual(self.job,before)

    def test_corrupt_copy_is_rejected(self):
        self.asset("one","body","fig1.png","red")
        with patch("_scripts_2.apps.exam.exam_processor.set_export.shutil.copy2",side_effect=lambda src,dst: Path(dst).write_bytes(b"corrupt")):
            with self.assertRaisesRegex(ValueError,"출력 이미지가 다릅니다"):
                write_sets(self.stage,self.job,"TEST",self.root)

    def test_missing_and_escaping_links_are_rejected(self):
        for link in ("missing.png","../outside.png"):
            (self.stage/"note.md").write_text(f"![[{link}]]")
            with self.assertRaises(ValueError):
                verify_export_assets(self.stage,[])

    def test_json_only_broken_image_is_rejected(self):
        (self.stage / "q.json").write_text(json.dumps({
            "schema_version": "0.3.0", "question": {}, "body": "![[missing.png]]"}))
        with self.assertRaises(ValueError):
            verify_export_assets(self.stage, [])

    def test_canonical_batch_rebuilds_question_passage_and_solution_assets(self):
        from _scripts_2.apps.exam.exam_processor.domain.models import new_item

        passage = new_item("source", "35-37", "35-37", "공통 지문 본문", kind="passage")
        passage["review"] = "approved"
        question = new_item("source", "35-37", "35", "문제 본문")
        question["review"] = "approved"
        question["passage_id"] = passage["id"]
        self.job = {"source": "검증", "subject": "국어", "items": [passage, question]}

        for owner, item, section, color in (
            ("passage", passage, "body", "blue"),
            ("question", question, "body", "red"),
            ("question", question, "solution", "green"),
        ):
            relative = f"assets/{owner}/{section}-fig.png"
            source = self.root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 12), color).save(source)
            asset_id = f"{owner}-{section}"
            item["assets"].append({"id": asset_id, "path": relative, "section": section})
            item[section] += f"\n![[{relative}]]\n"

        write_sets(self.stage, self.job, "TEST", self.root)
        portable = self.root / "portable-batch"
        portable.mkdir()
        manifest_path = self.stage / "canonical-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shutil.copy2(manifest_path, portable / manifest_path.name)
        for entry in manifest["records"]:
            source_json = self.stage / entry["json_path"]
            target_json = portable / entry["json_path"]
            target_json.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_json, target_json)
            record = json.loads(source_json.read_text(encoding="utf-8"))
            for asset in record["assets"]:
                source_asset = source_json.parent / asset["path"]
                target_asset = target_json.parent / asset["path"]
                target_asset.parent.mkdir(parents=True, exist_ok=True)
                if not target_asset.exists():
                    shutil.copy2(source_asset, target_asset)
        rebuilt = self.root / "rebuilt"
        outputs = rebuild_markdown_batch(portable, rebuilt)
        self.assertEqual(len(outputs), 2)
        question_md = (rebuilt / "TEST 1/TEST35.md").read_text(encoding="utf-8")
        passage_md = (rebuilt / f"TEST 1/passage-{passage['id']}.md").read_text(encoding="utf-8")
        self.assertIn("공통 지문", question_md)
        self.assertIn("![[passage-", question_md)
        self.assertIn("공통 지문 본문", passage_md)
        self.assertIn("assets/35/solution/", question_md)
        for image in rebuilt.rglob("*.png"):
            self.assertTrue(image.is_file())

    def test_canonical_rebuild_rejects_changed_asset_bytes(self):
        self.asset("one", "body", "fig1.png", "red")
        write_sets(self.stage, self.job, "TEST", self.root)
        asset = next((self.stage / "TEST 1/assets").rglob("*.png"))
        asset.write_bytes(b"changed after export")
        with self.assertRaisesRegex(ValueError, "에셋 해시가 정규 JSON과 다릅니다"):
            rebuild_markdown_batch(self.stage, self.root / "rebuilt")

    def test_canonical_rebuild_refuses_nonempty_destination(self):
        destination = self.root / "existing"
        destination.mkdir()
        sentinel = destination / "keep.md"
        sentinel.write_text("user content", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "비어 있는 재생성 폴더"):
            rebuild_markdown_batch(self.stage, destination)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user content")


class CropGeometryTests(unittest.TestCase):
    def test_clipped_bottom_completes_source_image(self):
        pixels, meta=complete_image_crop((1000,1000),[100,100,280,900],[0,0,100,100],[[10,10,90,35]])
        self.assertEqual(pixels,[98,98,902,352])
        self.assertEqual(len(meta["completed_image_boxes"]),1)

    def test_page_scan_and_neighbor_are_not_pulled_into_crop(self):
        pixels, meta=complete_image_crop((1000,1000),[100,100,300,900],[0,0,100,100],[[0,0,100,100],[10,40,90,60]])
        self.assertEqual(pixels,[98,98,902,302])
        self.assertEqual(meta["completed_image_boxes"],[])

    def test_crop_stays_within_region_and_invalid_input_fails(self):
        pixels,_=complete_image_crop((100,100),[0,0,1000,1000],[10,10,20,20],[[0,0,30,30]])
        self.assertEqual(pixels,[0,0,100,100])
        with self.assertRaises(ValueError):
            complete_image_crop((100,100),[0,0,1001,1000],[0,0,10,10],[])

    def test_manual_pixel_box_does_not_snap_or_pad(self):
        from _scripts_2.apps.exam.exam_processor.ingestion.asset_crops import pixel_box_from_proposed
        self.assertEqual(pixel_box_from_proposed((1000,500),[100,200,400,600]),[200,50,600,200])

    def test_asset_region_matches_crop_source(self):
        from _scripts_2.apps.exam.exam_processor.ingestion.asset_crops import region_for_asset
        first={"document":"problem","page":43,"bbox":[10,10,80,80],"image":"a.png"}
        second={"document":"problem","page":44,"bbox":[193,76,573,380],"image":"b.png"}
        item={"solution_regions":[first,second]}
        asset={"section":"solution","crop":{"document":"problem","page":44,"source_region":[193,76,573,380]}}
        self.assertEqual(region_for_asset(item,asset),second)
        with self.assertRaises(ValueError):
            region_for_asset({"solution_regions":[first,second]},{"section":"solution","crop":{}})

    def test_asset_region_uses_region_index_when_ambiguous(self):
        from _scripts_2.apps.exam.exam_processor.ingestion.asset_crops import region_for_asset
        first={"document":"problem","page":44,"bbox":[10,10,80,80],"image":"a.png"}
        second={"document":"problem","page":44,"bbox":[193,76,573,380],"image":"b.png"}
        item={"regions":[first,second]}
        fig2={"section":"body","crop":{"document":"problem","page":44,"region_index":1}}
        self.assertEqual(region_for_asset(item,fig2),second)

    def test_ink_refine_drops_paragraph_lines_below_figure(self):
        from PIL import ImageDraw
        from _scripts_2.apps.exam.exam_processor.ingestion.asset_crops import refine_extract_diagram_box
        image = Image.new("RGB", (400, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 30, 180, 120), outline="black", width=3)
        draw.rectangle((220, 40, 360, 130), outline="black", width=3)
        for y in (280, 310, 340, 370):
            draw.rectangle((30, y, 370, y + 14), fill="black")
        proposed = [50, 30, 900, 900]
        refined, meta = refine_extract_diagram_box(image, proposed, [0, 0, 400, 500], None, None, [])
        self.assertEqual(meta["box_refine"], "ink_pdf_text")
        self.assertLess(refined[2], proposed[2] - 100)
        self.assertGreater(refined[2], 120)

    def test_embedded_snap_skips_ink_refine(self):
        from _scripts_2.apps.exam.exam_processor.ingestion.asset_crops import refine_extract_diagram_box
        image = Image.new("RGB", (1000, 1000), "white")
        proposed = [100, 100, 280, 900]
        refined, meta = refine_extract_diagram_box(
            image, proposed, [0, 0, 100, 100], None, None, [[10, 10, 90, 35]],
        )
        self.assertEqual(meta["box_refine"], "embedded_snap")
        self.assertEqual(refined, proposed)
    def test_moved_adapter_uses_documents_root(self):
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import invoke
        with patch("_scripts_2.apps.exam.exam_processor.pipeline.ai.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, '{"data": {}}', "")) as run:
            invoke({"operation": "audit", "images": []})
        self.assertEqual(run.call_args.args[0][-1], "_scripts_2.apps.exam.exam_processor.pipeline.ai_worker")
        self.assertTrue((Path(run.call_args.kwargs["cwd"]) / "_scripts_2/apps/exam/exam_processor").is_dir())

    def test_real_adapter_builds_current_module_and_environment(self):
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import invoke
        result={"data":{"result":"no_difference","issues":[]},"provider":"fixture"}
        with patch("_scripts_2.apps.exam.exam_processor.pipeline.ai.subprocess.run",
                   return_value=subprocess.CompletedProcess([],0,json.dumps(result),"private diagnostics")) as run:
            self.assertEqual(invoke({"operation":"audit","images":[]}),result)
        self.assertEqual(run.call_args.args[0][-1],"_scripts_2.apps.exam.exam_processor.pipeline.ai_worker")
        cwd=Path(run.call_args.kwargs["cwd"])
        self.assertTrue((cwd/"_scripts_2/apps/exam/exam_processor").is_dir())
        self.assertIn(str(cwd),run.call_args.kwargs["env"]["PYTHONPATH"])


class InlineBoxTests(unittest.TestCase):
    def test_only_closed_rectangle_bottom_is_excluded(self):
        from _scripts_2.apps.exam.exam_processor.ingestion.pdf_styles import _closed_box_bottom
        bottom={"x0":10,"x1":30,"top":20,"bottom":20}
        top={"x0":10,"x1":30,"top":5,"bottom":5}
        left={"x0":10,"x1":10,"top":5,"bottom":20}
        right={"x0":30,"x1":30,"top":5,"bottom":20}
        self.assertTrue(_closed_box_bottom(SimpleNamespace(lines=[bottom,top,left,right],rects=[]),bottom))
        self.assertFalse(_closed_box_bottom(SimpleNamespace(lines=[bottom,left,right],rects=[]),bottom))
        self.assertFalse(_closed_box_bottom(SimpleNamespace(lines=[bottom,top,right],rects=[]),bottom))

    def test_real_2506_boxes_are_not_underlines(self):
        from _scripts_2.apps.exam.exam_processor.config import KS_ROOT
        from _scripts_2.apps.exam.exam_processor.ingestion.pdf_styles import detect_underlines
        import pdfplumber
        source=KS_ROOT/"1 국어/assets/화작 기출/추출/2506.pdf"
        if not source.exists():
            self.skipTest("Local 2506 PDF unavailable")
        with pdfplumber.open(source) as pdf:
            for page in pdf.pages:
                styles=detect_underlines(page,page.page_number,(0,0,page.width,page.height))
                self.assertFalse(any(s["text"] in ("건의","[A]") for s in styles))
