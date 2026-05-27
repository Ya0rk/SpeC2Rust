import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

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

    def test_model_initializes_round_logger_with_project_name(self):
        config = type("ConfigLike", (), {
            "round_log_dir": self.root,
            "round_log_project_name": "head",
            "model_name": "dummy",
        })()

        with patch.object(Model, "_get_model", return_value=DummyBackend()):
            model = Model(config)

        self.assertTrue(model.round_logger.run_dir.name.endswith("-head"))
        self.assertEqual(model.round_logger.run_dir.parent, self.root)


if __name__ == "__main__":
    unittest.main()
