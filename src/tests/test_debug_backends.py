from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.rtest.debug_backends import (  # noqa: E402
    BreakpointSpec,
    DapBackend,
    InstrumentationRequest,
    LldbBackend,
    StaticProbeSpec,
)
from agent.rtest.runtime_probe import _apply_static_probes, _render_static_statement  # noqa: E402


class DebugBackendTests(unittest.TestCase):
    def test_lldb_script_contains_break_and_inspection_commands(self) -> None:
        request = InstrumentationRequest(
            breakpoints=[BreakpointSpec(file="src/main.rs", line=35)],
            collect_stack=True,
            collect_locals=True,
            watch_expressions=["x", "vec.len()"],
            program_args=["--help"],
        )

        script = LldbBackend(debugger_executable="lldb").build_script("/tmp/app", request)

        self.assertIn("settings set auto-confirm true", script)
        self.assertIn("breakpoint set --file src/main.rs --line 35", script)
        self.assertIn("frame variable", script)
        self.assertIn("bt", script)
        self.assertIn("expression -- x", script)
        self.assertIn("settings set target.run-args --help", script)
        self.assertIn("process kill", script)
        self.assertTrue(script.rstrip().endswith("quit"))

    def test_parse_lldb_output_collects_frames_locals_and_watches(self) -> None:
        output = """
__CGR_LOCALS_BEGIN__
(int) x = 5
(std::vector<int>) vec = size=3 {1, 2, 3}
__CGR_LOCALS_END__
__CGR_BACKTRACE_BEGIN__
frame #0: 0x0000000000000000 app`main at src/main.rs:35
frame #1: 0x0000000000000001 libc.so.6`__libc_start_main + 231
__CGR_BACKTRACE_END__
__CGR_WATCH_0_BEGIN__
(int) $0 = 5
__CGR_WATCH_0_END__
""".strip()

        parsed = LldbBackend(debugger_executable="lldb").parse_output(output)

        self.assertEqual(parsed["locals"]["x"], "5")
        self.assertEqual(parsed["frames"][0]["file"], "src/main.rs")
        self.assertEqual(parsed["watch_values"]["0"], "5")

    def test_run_uses_backend_and_parses_output(self) -> None:
        request = InstrumentationRequest(
            breakpoints=[BreakpointSpec(file="src/main.rs", line=35)],
            collect_stack=True,
            collect_locals=True,
            program_args=["--arg", "value"],
        )

        class Proc:
            returncode = 0
            stdout = b"""
__CGR_LOCALS_BEGIN__
(int) x = 5
__CGR_LOCALS_END__
__CGR_BACKTRACE_BEGIN__
frame #0: 0x0000000000000000 app`main at src/main.rs:35
__CGR_BACKTRACE_END__
"""
            stderr = b""

        with tempfile.TemporaryDirectory() as tmp, patch("agent.rtest.debug_backends.shutil.which", return_value="/usr/bin/lldb"), patch(
            "agent.rtest.debug_backends.subprocess.run", return_value=Proc()
        ) as run_mock:
            backend = LldbBackend(timeout_seconds=5)
            result = backend.run("/tmp/app", request, log_dir=Path(tmp))

        self.assertTrue(result.ok)
        self.assertEqual(result.locals["x"], "5")
        self.assertEqual(result.frames[0]["file"], "src/main.rs")
        self.assertTrue(run_mock.called)
        self.assertEqual(run_mock.call_args.args[0][:3], ["/usr/bin/lldb", "--batch", "-s"])
        self.assertNotIn("/tmp/app", run_mock.call_args.args[0])

    def test_dap_launch_payload_reuses_same_instrumentation_request(self) -> None:
        request = InstrumentationRequest(
            breakpoints=[BreakpointSpec(file="src/main.rs", line=35)],
            collect_stack=True,
            collect_locals=True,
            program_args=["--arg", "value"],
        )

        payload = DapBackend().build_launch_payload("/tmp/app", request, cwd="/tmp")

        self.assertEqual(payload["program"], "/tmp/app")
        self.assertEqual(payload["breakpoints"][0]["line"], 35)
        self.assertTrue(payload["collectLocals"])
        self.assertEqual(payload["cwd"], "/tmp")
        self.assertEqual(payload["args"], ["--arg", "value"])

    def test_static_probe_statements_are_target_specific(self) -> None:
        rust_statement = _render_static_statement(
            StaticProbeSpec("rust_state", "rust", "src/main.rs", 10, ["items.len()"], "state")
        )
        c_statement = _render_static_statement(
            StaticProbeSpec("c_state", "c", "src/main.c", 10, ["count"], "state")
        )

        self.assertIn("eprintln!", rust_statement)
        self.assertIn("items.len()", rust_statement)
        self.assertIn("fprintf(stderr", c_statement)
        self.assertIn("(long long)(count)", c_statement)

    def test_multiple_static_probes_preserve_original_line_locations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            file = src / "main.rs"
            file.write_text("fn main() {\nlet a = 1;\nlet b = 2;\n}\n", encoding="utf-8")

            _apply_static_probes(
                root,
                [
                    StaticProbeSpec("at_a", "rust", "src/main.rs", 2, ["a"]),
                    StaticProbeSpec("at_b", "rust", "src/main.rs", 3, ["b"]),
                ],
            )

            text = file.read_text(encoding="utf-8")
            self.assertLess(text.index("[CGR_STATIC:at_a]"), text.index("let a = 1;"))
            self.assertLess(text.index("[CGR_STATIC:at_b]"), text.index("let b = 2;"))


if __name__ == "__main__":
    unittest.main()
