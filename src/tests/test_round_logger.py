import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.round_logger import RoundLogger


class RoundLoggerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f"_tmp_round_logger_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_log_round_writes_human_readable_request_reply_objective_and_stack(self):
        logger = RoundLogger(base_dir=self.root, run_name="run")
        path = logger.log_round(
            request=[{"role": "user", "content": "build a tree\\nwith nodes"}],
            reply=["done\\nnext line"],
            objective="生成树模块",
            model_name="custom_api",
            backend_name="CustomApiGen",
            token_usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
            call_stack=[
                {"file": "src/agent/rust_agent.py", "line": 10, "function": "generate_code"},
                {"file": "src/llm/model.py", "line": 20, "function": "generate"},
            ],
        )

        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.root / "run")
        self.assertEqual(path.suffix, ".md")

        text = path.read_text(encoding="utf-8")
        self.assertRegex(text, r"# LLM Round \d+")
        self.assertIn("**Objective:** 生成树模块", text)
        self.assertIn("**Request Tokens:** 11", text)
        self.assertIn("**Reply Tokens:** 7", text)
        self.assertIn("**Total Tokens:** 18", text)
        self.assertIn("**Call Source:** src/agent/rust_agent.py:10 `generate_code`", text)
        self.assertLess(text.index("## CALL STACK"), text.index("## REQUEST"))
        self.assertIn("## REQUEST", text)
        self.assertIn("### user", text)
        self.assertIn("build a tree\nwith nodes", text)
        self.assertIn("## REPLY", text)
        self.assertIn("done\nnext line", text)
        self.assertIn("## CALL STACK", text)
        self.assertIn("generate_code", text)

    def test_log_round_estimates_tokens_when_backend_usage_is_missing(self):
        logger = RoundLogger(base_dir=self.root, run_name="run")
        path = logger.log_round(
            request=[{"role": "user", "content": "hello world"}],
            reply=["ok"],
            objective="no usage",
        )

        text = path.read_text(encoding="utf-8")
        self.assertRegex(text, r"\*\*Request Tokens:\*\* \d+ \(estimated\)")
        self.assertRegex(text, r"\*\*Reply Tokens:\*\* \d+ \(estimated\)")

    def test_log_round_uses_project_name_in_default_run_directory(self):
        logger = RoundLogger(base_dir=self.root, project_name="head")
        path = logger.log_round(
            request="ping",
            reply="pong",
            objective="project name",
        )

        self.assertTrue(path.exists())
        self.assertTrue(path.parent.name.endswith("-head"))
        self.assertEqual(path.parent.parent, self.root)


if __name__ == "__main__":
    unittest.main()
