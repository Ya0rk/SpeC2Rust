from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.rtest.log_agent import LogAgent, RuntimeEvidenceBundle
from agent.rtest.models import TestCaseResult


class LogAgentTests(unittest.TestCase):
    def test_compress_runtime_bundle_prefers_structured_fields(self) -> None:
        bundle = RuntimeEvidenceBundle(
            case_name="case01",
            error="panic: index out of bounds",
            stderr="thread 'main' panicked\nstack backtrace:\n0: foo::run\n1: main\n",
            frames=[
                {"function": "foo::run", "file": "src/foo.rs", "line": 42},
                {"function": "main", "file": "src/main.rs", "line": 12},
            ],
            locals={"idx": 9, "len": 4},
            trace_lines=["enter foo::run", "exit foo::run"],
        )

        summary = LogAgent.compress(bundle, max_chars=1200)

        self.assertEqual(summary["error"], "panic: index out of bounds")
        self.assertEqual(summary["frames"][0]["file"], "src/foo.rs")
        self.assertEqual(summary["locals"]["idx"], 9)

    def test_write_case_bundle_creates_runtime_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / ".cgr_logs"
            path = LogAgent.write_case_bundle(
                log_dir,
                {
                    "case_name": "case.sh",
                    "exit_code": 1,
                    "error": "panic: unwrap on None",
                },
            )

            self.assertEqual(path.name, "runtime.json")
            self.assertTrue(path.exists())
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["error"].startswith("panic"))

    def test_bundle_from_result_uses_result_tails(self) -> None:
        result = TestCaseResult(
            name="case.sh",
            script_path="/tmp/case.sh",
            passed=False,
            exit_code=1,
            stdout="stdout tail",
            stderr="stderr tail",
            trace="line1\nline2\n",
        )

        bundle = LogAgent.bundle_from_result(result)

        self.assertEqual(bundle.case_name, "case.sh")
        self.assertEqual(bundle.exit_code, 1)
        self.assertEqual(bundle.error, "stderr tail")
        self.assertEqual(bundle.trace_lines, ["line1", "line2"])

    def test_parse_instrumentation_request(self) -> None:
        request = LogAgent.parse_instrumentation_request(
            {
                "backend": "lldb",
                "target": "c",
                "breakpoints": [{"file": "src/main.rs", "line": "42"}],
                "watch_expressions": ["x"],
                "program_args": ["--help"],
            }
        )

        self.assertEqual(request.breakpoints[0].file, "src/main.rs")
        self.assertEqual(request.breakpoints[0].line, 42)
        self.assertEqual(request.watch_expressions, ["x"])
        self.assertEqual(request.program_args, ["--help"])
        self.assertEqual(request.target, "c")

    def test_parse_static_probe_update_supports_add_remove_and_clear(self) -> None:
        update = LogAgent.parse_static_probe_update(
            {
                "add": [
                    {
                        "id": "c_branch",
                        "target": "c",
                        "file": "src/main.c",
                        "line": 17,
                        "expressions": ["state"],
                    }
                ],
                "remove": ["old_probe"],
                "clear": False,
                "program_args": ["--help"],
            }
        )

        self.assertEqual(update.add[0].probe_id, "c_branch")
        self.assertEqual(update.add[0].target, "c")
        self.assertEqual(update.remove, ["old_probe"])
        self.assertEqual(update.program_args, ["--help"])

    def test_combine_probe_summary_keeps_latest_probe_only(self) -> None:
        summary = LogAgent.combine_probe_summary(
            {
                "case_name": "case.sh",
                "debug_probes": [{"probe_round": 1}],
                "debug_probe": {"probe_round": 2},
            },
            {"probe_round": 3},
        )

        self.assertNotIn("debug_probes", summary)
        self.assertEqual(summary["debug_probe"]["probe_round"], 3)


if __name__ == "__main__":
    unittest.main()
