import threading
import unittest

from _scripts_2.apps.exam.exam_processor.pipeline.ai_activity import append, begin


class AiActivityTests(unittest.TestCase):
    def test_begin_and_append_persist_on_job(self):
        from _scripts_2.apps.exam.exam_processor.domain.job_runtime import JobRuntimeState

        job_id = "a" * 32
        runtime_state = JobRuntimeState(job_id=job_id)

        class FakeRuntimeRepo:
            def __init__(self, state):
                self.state = state

            def load(self, job_dir, jid):
                return self.state

            def save(self, job_dir, state):
                self.state = state

        class FakeStore:
            lock = threading.RLock()

            def __init__(self, state):
                self.runtime = FakeRuntimeRepo(state)

            def job_dir(self, jid):
                return "/fake/dir"

        fake = FakeStore(runtime_state)
        begin(fake, job_id, "extract", source="single")
        self.assertEqual(fake.runtime.state.ai_log, [])
        self.assertEqual(fake.runtime.state.ai_progress["operation"], "extract")
        append(fake, job_id, "API 호출", phase="api")
        self.assertEqual(len(fake.runtime.state.ai_log), 1)
        self.assertEqual(fake.runtime.state.ai_progress["message"], "API 호출")
        self.assertEqual(fake.runtime.state.ai_log[0]["phase"], "api")
