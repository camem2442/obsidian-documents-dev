import re
import shutil
import tempfile
import unittest
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from PIL import Image

from _scripts_2.apps.exam.exam_processor.ai import process
from _scripts_2.apps.exam.exam_processor.store import Store
from _scripts_2.apps.exam.exam_processor.work_queue import WorkQueue
from _scripts_2.apps.exam.exam_processor.vault import inside_vault, pdf_files
from _scripts_2.apps.exam.exam_processor.tests.test_core import NOTE


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "source.md"
        self.path.write_text(NOTE)
        self.store = Store(self.root/"data", self.root/"exports")
        self.job = self.store.import_file(self.path, "2026학년도 6월 모의평가", "화법과 작문")

    def tearDown(self):
        self.temp.cleanup()

    def fake_audit(self, store, job_id, item_id, expected, operation):
        return process(store, job_id, item_id, expected, "audit", caller=lambda _: {
            "data":{"result":"no_difference","issues":[]},"provider":"fixture","configured_model":"fixture"})

    def approve(self):
        for i in self.store.get(self.job["id"])["items"]:
            self.fake_audit(self.store,self.job["id"],i["id"],i["revision"],"audit")
            self.store.review(self.job["id"],i["id"],i["revision"],"approve")

    def test_named_set_and_separate_questions_move_together(self):
        for index, item in enumerate(self.job["items"][:2], 1):
            path=f"assets/{item['id']}/{item['revision']}/fig{index}.png"
            target=self.store.job_dir(self.job["id"])/path
            target.parent.mkdir(parents=True,exist_ok=True)
            Image.new("RGB",(20,20),"white").save(target)
            item["assets"]=[{"id":f"fig{index}","path":path,"section":"body"}]
            item["body"]+=f"\n\n<!-- MATERIAL:fig{index} START -->\n![[{path}]]\n\n자료 전사\n<!-- MATERIAL:fig{index} END -->"
        self.store.save(self.job)
        self.approve()
        result = self.store.export(self.job["id"],source_code="2606")
        batch=Path(result["last_export"])
        folder=batch/"2606 1"
        combined=(folder/"2606_1.md").read_text()
        question=(folder/"260638.md").read_text()
        self.assertIn("원본 지문입니다.",combined)
        self.assertIn("둘째 문제",combined)
        self.assertNotIn("원본 지문입니다.",question)
        self.assertNotIn("둘째 문제",question)
        self.assertIn("./2606_1.md#공통%20지문",question)
        self.assertIn('q_number: "38"',question)
        (folder/"260638.md").write_text(question+"\n사용자 필기")
        moved=self.root/"수동 분류"/folder.name
        moved.parent.mkdir()
        shutil.move(folder,moved)
        self.assertTrue((moved/"2606_1.md").is_file())
        for note in moved.glob("*.md"):
            for reference in re.findall(r"!\[\[([^\]]+)\]\]",note.read_text()):
                self.assertTrue((moved/reference).is_file(),reference)
        self.assertTrue((moved/"260638.md").read_text().endswith("사용자 필기"))
        newer=self.store.export(self.job["id"],source_code="2606")
        self.assertNotEqual(newer["last_export"],str(batch))
        self.assertTrue((moved/"260638.md").read_text().endswith("사용자 필기"))

    def test_partial_set_is_not_published(self):
        for i in self.job["items"][:2]:
            self.fake_audit(self.store,self.job["id"],i["id"],i["revision"],"audit")
            self.store.review(self.job["id"],i["id"],i["revision"],"approve")
        with self.assertRaises(ValueError):
            self.store.export(self.job["id"],source_code="2606")
        self.assertFalse(list((self.root/"exports").rglob("*.md")))

    def test_vault_paths_and_symlinks(self):
        ks=self.root/"KS"
        ks.mkdir()
        (ks/"exam.pdf").write_bytes(b"fixture")
        (ks/"escape").symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError): inside_vault("../outside",ks)
        with self.assertRaises(ValueError): inside_vault(ks/"escape/source.md",ks)
        self.assertEqual(len(pdf_files(ks,self.store,ks)["files"]),1)

    def test_failed_queue_retries_only_failure(self):
        calls=[]
        fail=[True]
        target=self.job["items"][1]["id"]
        def processor(store,j,i,r,op):
            calls.append(i)
            if i==target and fail[0]:
                fail[0]=False
                raise ValueError("fixture failure")
            return self.fake_audit(store,j,i,r,op)
        queue=WorkQueue(self.store,processor)
        queue.prepare(self.job["id"],"audit")
        queue.run(self.job["id"])
        self.assertEqual(len(calls),3)
        queue.prepare(self.job["id"],"audit","retry")
        queue.run(self.job["id"])
        self.assertEqual(calls.count(target),2)
        self.assertEqual(len(calls),4)
        self.assertTrue(all(i["review"]=="pending" for i in self.store.get(self.job["id"])["items"]))

    def test_pause_restart_and_protect_edit(self):
        queue=WorkQueue(self.store)
        def processor(store,j,i,r,op):
            self.fake_audit(store,j,i,r,op)
            queue.pause(j)
        queue.processor=processor
        queue.prepare(self.job["id"],"audit")
        queue.run(self.job["id"])
        job=self.store.get(self.job["id"])
        self.assertEqual(job["queue"]["status"],"paused")
        item=job["items"][1]
        self.store.update(job["id"],item["id"],item["revision"],{"body":"사용자 수정본"})
        resumed=WorkQueue(Store(self.store.root,self.store.output),self.fake_audit)
        resumed.prepare(job["id"],"audit","resume")
        resumed.run(job["id"])
        fresh=self.store.get(job["id"])
        self.assertEqual(fresh["items"][1]["body"],"사용자 수정본")
        self.assertEqual(fresh["queue"]["entries"][1]["status"],"skipped")

    def test_extract_queue_handles_passage_revision_changes(self):
        for item in self.job["items"]:
            item["regions"]=[{"image":"fixture.png","bbox":[0,0,20,20],"width":20,"height":20}]
        self.store.save(self.job)
        calls=[]
        def processor(store,j,i,r,op):
            calls.append(i)
            return store.update(j,i,r,{"body":store.item(store.get(j),i)["body"]+" 전사"})
        queue=WorkQueue(self.store,processor)
        queue.prepare(self.job["id"],"extract")
        queue.run(self.job["id"])
        self.assertEqual(len(calls),3)
        self.assertTrue(all(e["status"]=="completed" for e in self.store.get(self.job["id"])["queue"]["entries"]))
        with self.assertRaises(ValueError):
            queue.prepare(self.job["id"],"extract")

    def test_http_vault_export(self):
        self.approve()
        ks=self.root/"KS"
        ks.mkdir()
        (ks/"input.pdf").write_bytes(b"listing fixture")
        import sys
        process_handle=subprocess.Popen([
            sys.executable,
            "-m",
            "_scripts_2.apps.exam.exam_processor",
            "serve",
            "--port",
            "0",
        ],
            env={**os.environ,"EXAM_PROCESSOR_DATA":str(self.store.root),"EXAM_PROCESSOR_KS":str(ks)},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=process_handle.stdout.readline()
            base=re.search(r"http://127.0.0.1:\d+",line)[0]
            def request(route,data=None):
                req=urllib.request.Request(base+route,data=json.dumps(data).encode() if data is not None else None,
                    headers={"Content-Type":"application/json","X-Exam-Token":token} if data is not None else {})
                with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
            token=request("/api/config")["token"]
            files=request("/api/vault/files?folder="+urllib.parse.quote(str(ks)))
            self.assertEqual(len(files["files"]),1)
            result=request(f"/api/jobs/{self.job['id']}/export",{"destination":str(ks/"미분류"),"source_code":"2606"})
            folder=Path(result["last_export"])/"2606 1"
            self.assertTrue((folder/"2606_1.md").exists())
            self.assertTrue((folder/"260638.md").exists())
            with self.assertRaises(urllib.error.HTTPError):
                request(f"/api/jobs/{self.job['id']}/export",{"destination":str(self.root/"outside"),"source_code":"2606"})
        finally:
            process_handle.terminate()
            process_handle.communicate(timeout=10)
