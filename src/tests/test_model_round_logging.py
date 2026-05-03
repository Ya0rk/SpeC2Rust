import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.model import Model
from utils.round_logger import RoundLogger


class DummyBackend:
    def __init__(self):
        self.last_usage = {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        }

    def get_response(self, messages):
        return ["reply text\\nsecond line"]


class ModelRoundLoggingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f"_tmp_model_round_logging_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def build_model(self):
        model = Model.__new__(Model)
        model.config = None
        model.model_name = "dummy"
        model.llm = DummyBackend()
        model.round_logger = RoundLogger(base_dir=self.root, run_name="run")
        model._current_request_label = ""
        return model

    def test_generate_logs_request_reply_label_and_stack(self):
        model = self.build_model()
        model.set_request_label("代码生成 foo.rs [round 1]")

        reply = model.generate([{"role": "user", "content": "make foo\\nwith bar"}])

        self.assertEqual(reply, ["reply text\\nsecond line"])
        files = list((self.root / "run").glob("*.md"))
        self.assertEqual(len(files), 1)

        text = files[0].read_text(encoding="utf-8")
        self.assertIn("**Objective:** 代码生成 foo.rs [round 1]", text)
        self.assertIn("**Request Tokens:** 3", text)
        self.assertIn("**Reply Tokens:** 2", text)
        self.assertIn("**Total Tokens:** 5", text)
        self.assertIn("make foo\nwith bar", text)
        self.assertIn("reply text\nsecond line", text)
        self.assertIn("generate", text)
        self.assertIn("test_generate_logs_request_reply_label_and_stack", text)


if __name__ == "__main__":
    unittest.main()
