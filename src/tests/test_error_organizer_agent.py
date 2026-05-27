import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.error_organizer_agent import ErrorOrganizerAgent


class ErrorOrganizerAgentTests(unittest.TestCase):
    def test_organize_errors_attaches_surrounding_source_context(self):
        root = Path(__file__).parent / f"_tmp_error_organizer_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            src_dir = root / "src"
            src_dir.mkdir()
            target = src_dir / "demo.rs"
            target.write_text(
                "\n".join(f"line {i}" for i in range(1, 41)) + "\n",
                encoding="utf-8",
            )

            organizer = ErrorOrganizerAgent(batch_size=10)
            batches = organizer.organize_errors(
                """
error[E0425]: cannot find value `foo` in this scope
  --> src/demo.rs:20:5
   |
20 | foo();
   | ^^^ not found
""",
                str(root),
            )

            self.assertEqual(len(batches), 1)
            batch = batches[0]
            self.assertIn("candidate_contexts", batch)
            self.assertEqual(len(batch["candidate_contexts"]), 1)
            context = batch["candidate_contexts"][0]
            self.assertEqual(context["file_path"], "src/demo.rs")
            self.assertEqual(context["start_line"], 5)
            self.assertEqual(context["end_line"], 35)
            self.assertIn("0005 | line 5", context["content"])
            self.assertIn("0020 | line 20", context["content"])
            self.assertIn("0035 | line 35", context["content"])
            self.assertIn("附加 1 段源码上下文", batch["summary"])
            self.assertIn("File: src/demo.rs", batch["context_text"])
        finally:
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
