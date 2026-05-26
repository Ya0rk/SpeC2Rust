"""Suite-level repair orchestration for rtest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

from .models import TestRunSummary
from .source_loader import CSourceIndex
from .test_runner import TestRunner


@dataclass
class SuiteRepairContext:
    rust_project_path: str
    bin_name: str
    runner: TestRunner
    project_structure: str
    source_index: CSourceIndex
    summary: TestRunSummary
    scripts: List[Path]
    initial_binary_path: str
    max_suite_repair_cycles: int


class SuiteRepairCoordinator:
    """Drive repeated test-suite repair cycles until stable or exhausted."""

    def __init__(self, agent: object, context: SuiteRepairContext):
        self.agent = agent
        self.context = context

    def run(self) -> TestRunSummary:
        rust_bin_name = f"{self.context.bin_name}-rust"
        current_summary = self.context.summary
        for suite_cycle in range(1, self.context.max_suite_repair_cycles + 1):
            failing_cases = [case for case in current_summary.results if not case.passed]
            if not failing_cases:
                return current_summary

            if suite_cycle > 1:
                print(
                    f"[rtest] ===== 继续套件修复轮次 {suite_cycle}/{self.context.max_suite_repair_cycles} ====="
                )

            baseline_pass_names: Set[str] = {
                case.name for case in current_summary.results if case.passed
            }
            failing_cases.sort(key=_script_size)
            fixed_any = False
            for case in failing_cases:
                fixed = self.agent._repair_failing_case(  # noqa: SLF001
                    rust_project_path=self.context.rust_project_path,
                    bin_name=self.context.bin_name,
                    runner=self.context.runner,
                    project_structure=self.context.project_structure,
                    source_index=self.context.source_index,
                    failing_case=case,
                    baseline_pass_names=baseline_pass_names,
                )
                if fixed:
                    fixed_any = True
                    baseline_pass_names.add(case.name)

            final_binary = self.agent._locate_release_binary(  # noqa: SLF001
                self.context.rust_project_path, rust_bin_name
            )
            if final_binary and final_binary != self.context.initial_binary_path:
                self.context.runner.restage_rust_binary(final_binary)

            current_summary = self.context.runner.run_all(self.context.scripts)
            self.agent._print_summary(  # noqa: SLF001
                current_summary, label=f"第 {suite_cycle} 轮套件修复后的测试结果"
            )
            if current_summary.all_passed:
                return current_summary
            if not fixed_any:
                print("[rtest] 本轮没有任何失败用例被修复，停止继续套件修复")
                break

        return current_summary


def _script_size(case) -> int:
    try:
        return Path(case.script_path).stat().st_size
    except OSError:
        return 1 << 30
