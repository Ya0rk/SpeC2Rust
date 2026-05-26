from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.rtest.repair_prompt import MaterialBudget, build_repair_prompt  # noqa: E402
from agent.rtest.models import TestCaseResult, TestRunSummary  # noqa: E402
from agent.rtest.rust_test_agent import RustTestAgent, _RepairLoopState  # noqa: E402
from agent.rtest.test_runner import TestRunner  # noqa: E402


class TestRunnerLoggingTests(unittest.TestCase):
    def test_write_runtime_log_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            run_dir = test_dir / ".run_case"
            test_dir.mkdir()
            run_dir.mkdir()
            runner = TestRunner(test_dir=str(test_dir), bin_name="demo", timeout_seconds=30)
            result = TestCaseResult(
                name="case.sh",
                script_path=str(test_dir / "case.sh"),
                passed=False,
                exit_code=1,
                stdout="stdout tail",
                stderr="panic: unwrap on None",
                trace="line1\nline2\n",
            )

            path = runner.write_runtime_log(run_dir, result)

            self.assertEqual(path.name, "runtime.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["case_name"], "case.sh")
            self.assertEqual(payload["error"], "panic: unwrap on None")
            self.assertEqual(payload["trace"], ["line1", "line2"])

    def test_read_runtime_evidence_from_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            run_dir = test_dir / ".run_case"
            log_dir = run_dir / ".cgr_logs"
            test_dir.mkdir()
            log_dir.mkdir(parents=True)
            (log_dir / "runtime.json").write_text(
                json.dumps({"case_name": "case.sh", "error": "panic", "frames": [{"file": "src/main.rs", "line": 42}]}),
                encoding="utf-8",
            )
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(test_dir / "case.sh"),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )

            payload = RustTestAgent._read_runtime_evidence(failing_case)

            self.assertEqual(payload["case_name"], "case.sh")
            self.assertEqual(payload["frames"][0]["file"], "src/main.rs")

    def test_read_runtime_evidence_uses_latest_debug_probe_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            run_dir = test_dir / ".run_case"
            log_dir = run_dir / ".cgr_logs"
            test_dir.mkdir()
            log_dir.mkdir(parents=True)
            (log_dir / "runtime.json").write_text(
                json.dumps({"case_name": "case.sh", "error": "panic"}),
                encoding="utf-8",
            )
            (log_dir / "debug_probe_1.json").write_text(
                json.dumps({"backend": "lldb", "frames": [{"file": "src/main.rs", "line": 42}]}),
                encoding="utf-8",
            )
            latest = log_dir / "debug_probe_10.json"
            latest.write_text(
                json.dumps({"backend": "lldb", "frames": [{"file": "src/lib.rs", "line": 99}]}),
                encoding="utf-8",
            )
            (log_dir / "debug_probe_2.dap.json").write_text(
                json.dumps({"backend": "dap", "launch": {"program": "/tmp/app"}}),
                encoding="utf-8",
            )
            os.utime(log_dir / "debug_probe_1.json", (1, 1))
            os.utime(latest, (1, 1))
            os.utime(log_dir / "debug_probe_2.dap.json", (3, 3))
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(test_dir / "case.sh"),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )

            payload = RustTestAgent._read_runtime_evidence(failing_case)

            self.assertEqual(payload["case_name"], "case.sh")
            self.assertEqual(len(payload["debug_probes"]), 2)
            self.assertEqual(payload["debug_probe"]["backend"], "lldb")
            self.assertEqual(payload["debug_probe"]["frames"][0]["file"], "src/lib.rs")

    def test_repair_prompt_includes_runtime_evidence(self) -> None:
        failing_case = TestCaseResult(
            name="case.sh",
            script_path="/tmp/case.sh",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="panic: unwrap on None",
        )
        prompt = build_repair_prompt(
            failing_case=failing_case,
            script_content="echo hi",
            project_structure="",
            rust_overview="",
            material=MaterialBudget(),
            history_summary="",
            source_records_index="",
            attempt=1,
            max_attempts=3,
            last_build_error="",
            flags=[],
            keywords=[],
            expected_outputs=[],
            regression_warning="",
            focused_failure="",
            test_artifact_index="",
            runtime_evidence={"error": "panic: unwrap on None", "frames": [{"file": "src/main.rs", "line": 42}]},
            log_agent_enabled=True,
        )

        self.assertIn("[Runtime evidence]", prompt)
        self.assertIn("src/main.rs", prompt)
        self.assertIn("42", prompt)
        self.assertIn("static_probe_update", prompt)
        self.assertIn('"target": "rust | c | both"', prompt)

    def test_repair_prompt_omits_all_probe_guidance_when_log_agent_is_disabled(self) -> None:
        failing_case = TestCaseResult(
            name="case.sh",
            script_path="/tmp/case.sh",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="failure",
        )
        prompt = build_repair_prompt(
            failing_case=failing_case,
            script_content="exit 1",
            project_structure="",
            rust_overview="",
            material=MaterialBudget(),
            history_summary="",
            source_records_index="",
            attempt=1,
            max_attempts=3,
            last_build_error="",
            flags=[],
            keywords=[],
            expected_outputs=[],
            regression_warning="",
            focused_failure="",
            test_artifact_index="",
            runtime_evidence={"debug_probe": {"target": "rust"}},
            log_agent_enabled=False,
            active_static_probes=[object()],
        )

        self.assertNotIn("LogAgent", prompt)
        self.assertNotIn("debug_probe", prompt)
        self.assertNotIn("static_probe_update", prompt)
        self.assertNotIn("[Runtime evidence]", prompt)

    def test_repair_prompt_marks_all_test_shell_scripts_read_only(self) -> None:
        failing_case = TestCaseResult(
            name="case.sh",
            script_path="/tmp/case.sh",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="failure",
        )
        prompt = build_repair_prompt(
            failing_case=failing_case,
            script_content="exit 1",
            project_structure="",
            rust_overview="",
            material=MaterialBudget(),
            history_summary="",
            source_records_index="",
            attempt=1,
            max_attempts=3,
            last_build_error="",
            flags=[],
            keywords=[],
            expected_outputs=[],
            regression_warning="",
            focused_failure="",
            test_artifact_index="",
        )

        self.assertIn("Never edit any test shell script or fixture", prompt)
        self.assertIn("Every test shell script and fixture is read-only", prompt)
        self.assertNotIn("Prefer editing the current test script", prompt)
        self.assertNotIn("you may edit only the current failing script", prompt)

    def test_filter_disallowed_edits_rejects_test_shell_script_edits(self) -> None:
        agent = RustTestAgent(max_repair_iterations=1)

        edits = agent._filter_disallowed_edits(
            [
                {"path": "test/case.sh", "mode": "replace_range", "content": "exit 0"},
                {"path": "case.sh", "mode": "replace_range", "content": "exit 0"},
                {"path": "src/main.rs", "mode": "replace_range", "content": "fn main() {}"},
            ]
        )

        self.assertEqual([edit["path"] for edit in edits], ["src/main.rs"])

    def test_repair_round_reports_rejected_read_only_test_script_edit(self) -> None:
        class FakeLlm:
            def set_request_label(self, _label: str) -> None:
                return None

            def generate(self, _messages):
                return json.dumps(
                    {
                        "summary": "attempted script edit",
                        "edits": [
                            {
                                "path": "test/case.sh",
                                "mode": "replace_range",
                                "start_line": 1,
                                "end_line": 1,
                                "content": "exit 0\n",
                            }
                        ],
                        "complete": False,
                    }
                )

        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            script = test_dir / "case.sh"
            script.write_text("exit 1\n", encoding="utf-8")
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(script),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="failure",
            )
            agent = RustTestAgent(max_repair_iterations=1)
            agent.llm = FakeLlm()
            state = _RepairLoopState(
                history_summary="",
                last_build_error="",
                regression_warning="",
                last_failure_signature=failing_case.failure_signature(),
                last_edits_fingerprint="",
                last_debug_probe_fingerprint="",
                stall_count=0,
                dup_edits_count=0,
                debug_probe_count=0,
            )

            outcome = agent._repair_one_round(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                runner=TestRunner(test_dir=str(test_dir), bin_name="demo"),
                project_structure="",
                source_index=None,
                source_index_display="",
                rust_overview="",
                failing_case=failing_case,
                script_content="exit 1\n",
                flags=[],
                keywords=[],
                expected_outputs=[],
                baseline_pass_names=set(),
                material=MaterialBudget(),
                state=state,
                attempt=1,
                snapshot=None,
            )

            self.assertEqual(outcome, "continue")
            self.assertIn("read-only inputs", state.history_summary)

    def test_test_runner_only_persists_runtime_log_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            script = test_dir / "case.sh"
            script.write_text("exit 1\n", encoding="utf-8")
            binary = Path(tmp) / "demo-rust"
            binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            os.chmod(binary, 0o755)

            disabled = TestRunner(str(test_dir), "demo", enable_logging=False)
            disabled.stage(str(binary), None)
            disabled.run_single(script)
            self.assertFalse((test_dir / ".run_case" / ".cgr_logs" / "runtime.json").exists())
            disabled.cleanup()

            enabled = TestRunner(str(test_dir), "demo", enable_logging=True)
            enabled.stage(str(binary), None)
            enabled.run_single(script)
            runtime_log = test_dir / ".run_case" / ".cgr_logs" / "runtime.json"
            self.assertTrue(runtime_log.exists())
            enabled.cleanup()
            self.assertTrue(runtime_log.exists())

    def test_execute_debug_probe_writes_probe_bundle(self) -> None:
        class FakeResult:
            ok = True
            returncode = 0
            command = ["lldb"]
            script_path = "/tmp/probe/lldb.cmd"
            stdout = "__CGR_BACKTRACE_BEGIN__\nframe #0: app`main at src/main.rs:42\n__CGR_BACKTRACE_END__"
            stderr = ""
            frames = [{"file": "src/main.rs", "line": 42}]
            locals = {"x": "5"}
            watch_values = {"0": "5"}

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            RustTestAgent, "_locate_release_binary", return_value="/tmp/app"
        ), patch("agent.rtest.runtime_probe.LldbBackend.run", return_value=FakeResult()) as run_mock:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(test_dir / "case.sh"),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )
            agent = RustTestAgent(max_repair_iterations=1)

            ok = agent._execute_debug_probe(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                failing_case=failing_case,
                probe_spec={
                    "backend": "lldb",
                    "breakpoints": [{"file": "src/main.rs", "line": 42}],
                    "watch_expressions": ["x"],
                },
                attempt=2,
            )

            self.assertTrue(ok)
            probe_path = test_dir / ".run_case" / ".cgr_logs" / "debug_probe_2.json"
            self.assertTrue(probe_path.exists())
            payload = json.loads(probe_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["frames"][0]["file"], "src/main.rs")
            self.assertEqual(payload["watch_values"]["0"], "5")
            self.assertEqual(Path(run_mock.call_args.kwargs["cwd"]), test_dir / ".run_case")

    def test_execute_debug_probe_can_capture_rust_and_c_targets(self) -> None:
        class FakeResult:
            def __init__(self, file: str, line: int) -> None:
                self.ok = True
                self.returncode = 0
                self.command = ["lldb"]
                self.script_path = "/tmp/probe/lldb.cmd"
                self.stdout = ""
                self.stderr = ""
                self.frames = [{"file": file, "line": line}]
                self.locals = {}
                self.watch_values = {}

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            RustTestAgent, "_locate_release_binary", return_value="/tmp/rust-app"
        ), patch(
            "agent.rtest.runtime_probe.LldbBackend.run",
            side_effect=[FakeResult("src/main.rs", 42), FakeResult("src/main.c", 18)],
        ) as run_mock:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(test_dir / "case.sh"),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="failure",
            )
            agent = RustTestAgent(max_repair_iterations=1)
            agent.runtime_probe_service.configure_c_target(str(Path(tmp)), "/tmp/c-app")

            ok = agent._execute_debug_probe(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                failing_case=failing_case,
                probe_spec={
                    "target": "both",
                    "targets": {
                        "rust": {"breakpoints": [{"file": "src/main.rs", "line": 42}]},
                        "c": {"breakpoints": [{"file": "src/main.c", "line": 18}]},
                    },
                },
                attempt=3,
            )

            self.assertTrue(ok)
            payload = json.loads(
                (test_dir / ".run_case" / ".cgr_logs" / "debug_probe_3.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["targets"]["rust"]["frames"][0]["file"], "src/main.rs")
            self.assertEqual(payload["targets"]["c"]["frames"][0]["file"], "src/main.c")
            self.assertEqual(run_mock.call_count, 2)

    def test_repair_one_round_reads_runtime_evidence_without_name_error(self) -> None:
        class FakeLlm:
            def set_request_label(self, _label: str) -> None:
                return None

            def generate(self, _messages):
                return '{"complete": true, "updated_summary": "saw runtime evidence"}'

        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            run_dir = test_dir / ".run_case"
            log_dir = run_dir / ".cgr_logs"
            log_dir.mkdir(parents=True)
            script = test_dir / "case.sh"
            script.write_text("echo hi\n", encoding="utf-8")
            (log_dir / "runtime.json").write_text(
                json.dumps({"case_name": "case.sh", "error": "panic"}),
                encoding="utf-8",
            )
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(script),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )
            agent = RustTestAgent(max_repair_iterations=1, enable_log_agent=True)
            agent.llm = FakeLlm()
            state = _RepairLoopState(
                history_summary="",
                last_build_error="",
                regression_warning="",
                last_failure_signature=failing_case.failure_signature(),
                last_edits_fingerprint="",
                last_debug_probe_fingerprint="",
                stall_count=0,
                dup_edits_count=0,
                debug_probe_count=0,
            )

            outcome = agent._repair_one_round(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                runner=TestRunner(test_dir=str(test_dir), bin_name="demo"),
                project_structure="",
                source_index=None,
                source_index_display="",
                rust_overview="",
                failing_case=failing_case,
                script_content="echo hi\n",
                flags=[],
                keywords=[],
                expected_outputs=[],
                baseline_pass_names=set(),
                material=MaterialBudget(),
                state=state,
                attempt=1,
                snapshot=None,
            )

            self.assertEqual(outcome, "abort")
            self.assertEqual(state.history_summary, "saw runtime evidence")

    def test_repair_one_round_absorbs_material_before_debug_probe(self) -> None:
        class FakeLlm:
            def set_request_label(self, _label: str) -> None:
                return None

            def generate(self, _messages):
                return json.dumps(
                    {
                        "summary": "need C source before probing",
                        "cgr_read": [{"kind": "function", "query": "main"}],
                        "debug_probe": {
                            "backend": "lldb",
                            "breakpoints": [{"file": "src/main.rs", "line": 1}],
                        },
                        "edits": [],
                        "complete": False,
                    }
                )

        class FakeSourceIndex:
            def fulfill_request(self, _req):
                return {
                    "name": "main",
                    "file": "c4.c",
                    "source": "int main(int argc, char **argv) { return 0; }",
                }

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            RustTestAgent, "_execute_debug_probe", return_value=True
        ) as probe_mock:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            script = test_dir / "case.sh"
            script.write_text("echo hi\n", encoding="utf-8")
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(script),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )
            agent = RustTestAgent(max_repair_iterations=1, enable_log_agent=True)
            agent.llm = FakeLlm()
            state = _RepairLoopState(
                history_summary="",
                last_build_error="",
                regression_warning="",
                last_failure_signature=failing_case.failure_signature(),
                last_edits_fingerprint="",
                last_debug_probe_fingerprint="",
                stall_count=0,
                dup_edits_count=0,
                debug_probe_count=0,
            )
            material = MaterialBudget()

            outcome = agent._repair_one_round(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                runner=TestRunner(test_dir=str(test_dir), bin_name="demo"),
                project_structure="",
                source_index=FakeSourceIndex(),
                source_index_display="",
                rust_overview="",
                failing_case=failing_case,
                script_content="echo hi\n",
                flags=[],
                keywords=[],
                expected_outputs=[],
                baseline_pass_names=set(),
                material=material,
                state=state,
                attempt=1,
                snapshot=None,
            )

            self.assertEqual(outcome, "continue")
            self.assertEqual(material.c_records()[0]["name"], "main")
            self.assertEqual(state.debug_probe_count, 0)
            probe_mock.assert_not_called()

    def test_repair_one_round_prefers_edits_over_debug_probe(self) -> None:
        class FakeLlm:
            def set_request_label(self, _label: str) -> None:
                return None

            def generate(self, _messages):
                return json.dumps(
                    {
                        "summary": "apply the concrete fix",
                        "edits": [
                            {
                                "path": "src/main.rs",
                                "mode": "insert_after",
                                "start_line": 1,
                                "end_line": 1,
                                "content": "fn helper() {}\n",
                            }
                        ],
                        "debug_probe": {
                            "backend": "lldb",
                            "breakpoints": [],
                            "watch_expressions": [],
                            "collect_stack": False,
                            "collect_locals": False,
                        },
                        "complete": False,
                    }
                )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            RustTestAgent, "_execute_debug_probe", return_value=True
        ) as probe_mock:
            test_dir = Path(tmp) / "test"
            project_dir = Path(tmp) / "project"
            test_dir.mkdir()
            project_dir.mkdir()
            script = test_dir / "case.sh"
            script.write_text("echo hi\n", encoding="utf-8")
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(script),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )
            agent = RustTestAgent(max_repair_iterations=1)
            agent.llm = FakeLlm()
            state = _RepairLoopState(
                history_summary="",
                last_build_error="",
                regression_warning="",
                last_failure_signature=failing_case.failure_signature(),
                last_edits_fingerprint="",
                last_debug_probe_fingerprint="",
                stall_count=0,
                dup_edits_count=0,
                debug_probe_count=0,
            )

            with patch.object(agent.adapter, "apply_structured_edits", return_value=(True, [])) as edits_mock, patch.object(
                agent.adapter, "read_file_slice", return_value=""
            ), patch.object(agent, "_build_and_verify", return_value="passed") as verify_mock:
                outcome = agent._repair_one_round(
                    rust_project_path=str(project_dir),
                    bin_name="demo",
                    runner=TestRunner(test_dir=str(test_dir), bin_name="demo"),
                    project_structure="",
                    source_index=None,
                    source_index_display="",
                    rust_overview="",
                    failing_case=failing_case,
                    script_content="echo hi\n",
                    flags=[],
                    keywords=[],
                    expected_outputs=[],
                    baseline_pass_names=set(),
                    material=MaterialBudget(),
                    state=state,
                    attempt=1,
                    snapshot=None,
                )

            self.assertEqual(outcome, "passed")
            self.assertEqual(state.debug_probe_count, 0)
            probe_mock.assert_not_called()
            edits_mock.assert_called_once()
            verify_mock.assert_called_once()

    def test_repair_one_round_skips_debug_probe_without_breakpoints(self) -> None:
        class FakeLlm:
            def set_request_label(self, _label: str) -> None:
                return None

            def generate(self, _messages):
                return json.dumps(
                    {
                        "summary": "bad no-op probe",
                        "edits": [],
                        "debug_probe": {
                            "backend": "lldb",
                            "breakpoints": [],
                            "watch_expressions": [],
                            "collect_stack": False,
                            "collect_locals": False,
                        },
                        "complete": False,
                    }
                )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            RustTestAgent, "_execute_debug_probe", return_value=True
        ) as probe_mock:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            script = test_dir / "case.sh"
            script.write_text("echo hi\n", encoding="utf-8")
            failing_case = TestCaseResult(
                name="case.sh",
                script_path=str(script),
                passed=False,
                exit_code=1,
                stdout="",
                stderr="panic",
            )
            agent = RustTestAgent(max_repair_iterations=1, enable_log_agent=True)
            agent.llm = FakeLlm()
            state = _RepairLoopState(
                history_summary="",
                last_build_error="",
                regression_warning="",
                last_failure_signature=failing_case.failure_signature(),
                last_edits_fingerprint="",
                last_debug_probe_fingerprint="",
                stall_count=0,
                dup_edits_count=0,
                debug_probe_count=0,
            )

            outcome = agent._repair_one_round(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                runner=TestRunner(test_dir=str(test_dir), bin_name="demo"),
                project_structure="",
                source_index=None,
                source_index_display="",
                rust_overview="",
                failing_case=failing_case,
                script_content="echo hi\n",
                flags=[],
                keywords=[],
                expected_outputs=[],
                baseline_pass_names=set(),
                material=MaterialBudget(),
                state=state,
                attempt=1,
                snapshot=None,
            )

            self.assertEqual(outcome, "continue")
            self.assertEqual(state.debug_probe_count, 0)
            self.assertIn("no breakpoints", state.history_summary)
            probe_mock.assert_not_called()

    def test_repair_one_round_runs_persistent_static_probe_update(self) -> None:
        class FakeLlm:
            def set_request_label(self, _label: str) -> None:
                return None

            def generate(self, _messages):
                return json.dumps(
                    {
                        "summary": "compare state in Rust and C",
                        "edits": [],
                        "static_probe_update": {
                            "add": [
                                {"id": "r1", "target": "rust", "file": "src/main.rs", "line": 3, "expressions": ["state"]},
                                {"id": "c1", "target": "c", "file": "main.c", "line": 3, "expressions": ["state"]},
                            ],
                            "program_args": ["input"],
                        },
                        "complete": False,
                    }
                )

        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp) / "test"
            test_dir.mkdir()
            script = test_dir / "case.sh"
            script.write_text("exit 1\n", encoding="utf-8")
            failing_case = TestCaseResult("case.sh", str(script), False, 1, "", "fail")
            agent = RustTestAgent(max_repair_iterations=1, enable_log_agent=True)
            agent.llm = FakeLlm()
            state = _RepairLoopState(
                history_summary="",
                last_build_error="",
                regression_warning="",
                last_failure_signature=failing_case.failure_signature(),
                last_edits_fingerprint="",
                last_debug_probe_fingerprint="",
                stall_count=0,
                dup_edits_count=0,
                debug_probe_count=0,
            )
            with patch.object(
                agent.runtime_probe_service, "execute_static_probes", return_value=True
            ) as execute_mock:
                outcome = agent._repair_one_round(
                    rust_project_path=str(Path(tmp)),
                    bin_name="demo",
                    runner=TestRunner(str(test_dir), "demo"),
                    project_structure="",
                    source_index=None,
                    source_index_display="",
                    rust_overview="",
                    failing_case=failing_case,
                    script_content="exit 1\n",
                    flags=[],
                    keywords=[],
                    expected_outputs=[],
                    baseline_pass_names=set(),
                    material=MaterialBudget(),
                    state=state,
                    attempt=1,
                    snapshot=None,
                )

            self.assertEqual(outcome, "continue")
            self.assertEqual(sorted(state.static_probes), ["c1", "r1"])
            self.assertEqual(state.static_program_args, ["input"])
            execute_mock.assert_called_once()

    def test_repair_suite_until_stable_runs_multiple_cycles(self) -> None:
        hello_fail = TestCaseResult(
            name="c4-hello.sh",
            script_path="/tmp/c4-hello.sh",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="hello fail",
        )
        self_host_fail = TestCaseResult(
            name="c4-self-host.sh",
            script_path="/tmp/c4-self-host.sh",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="self-host fail",
        )
        initial_summary = TestRunSummary(
            total=2,
            passed=0,
            failed=2,
            results=[hello_fail, self_host_fail],
        )
        cycle1_summary = TestRunSummary(
            total=2,
            passed=1,
            failed=1,
            results=[
                TestCaseResult(
                    name="c4-hello.sh",
                    script_path="/tmp/c4-hello.sh",
                    passed=True,
                    exit_code=0,
                    stdout="",
                    stderr="",
                ),
                self_host_fail,
            ],
        )
        cycle2_summary = TestRunSummary(
            total=2,
            passed=2,
            failed=0,
            results=[
                TestCaseResult(
                    name="c4-hello.sh",
                    script_path="/tmp/c4-hello.sh",
                    passed=True,
                    exit_code=0,
                    stdout="",
                    stderr="",
                ),
                TestCaseResult(
                    name="c4-self-host.sh",
                    script_path="/tmp/c4-self-host.sh",
                    passed=True,
                    exit_code=0,
                    stdout="",
                    stderr="",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            RustTestAgent, "_repair_failing_case", side_effect=[True, False, True]
        ) as repair_mock, patch.object(
            RustTestAgent, "_print_summary", return_value=None
        ), patch.object(
            RustTestAgent, "_locate_release_binary", return_value="/tmp/bin"
        ), patch.object(
            TestRunner, "run_all", side_effect=[cycle1_summary, cycle2_summary]
        ) as run_all_mock:
            agent = RustTestAgent(max_repair_iterations=1, max_suite_repair_cycles=2)
            runner = TestRunner(test_dir=str(Path(tmp) / "test"), bin_name="demo")

            result = agent._repair_suite_until_stable(
                rust_project_path=str(Path(tmp)),
                bin_name="demo",
                runner=runner,
                project_structure="",
                source_index=object(),
                summary=initial_summary,
                scripts=[Path("/tmp/c4-hello.sh"), Path("/tmp/c4-self-host.sh")],
                test_dst=str(Path(tmp) / "test"),
                initial_binary_path="/tmp/bin",
            )

            self.assertTrue(result.all_passed)
            self.assertEqual(run_all_mock.call_count, 2)
            self.assertEqual(repair_mock.call_count, 3)


if __name__ == "__main__":
    unittest.main()
