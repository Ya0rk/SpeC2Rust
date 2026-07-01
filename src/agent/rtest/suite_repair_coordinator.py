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
        attempted_names: Set[str] = set()
        for suite_cycle in range(1, self.context.max_suite_repair_cycles + 1):
            if current_summary.all_passed:
                return current_summary

            if suite_cycle > 1:
                print(
                    f"[rtest] ===== 继续套件修复轮次 {suite_cycle}/{self.context.max_suite_repair_cycles} ====="
                )

            fixed_any = False
            while True:
                failing_cases = [
                    case
                    for case in current_summary.results
                    if not case.passed and case.name not in attempted_names
                ]
                if not failing_cases:
                    break

                failing_cases.sort(key=_script_size)
                case = failing_cases[0]
                attempted_names.add(case.name)
                baseline_pass_names: Set[str] = {
                    result.name for result in current_summary.results if result.passed
                }
                passed_before = current_summary.passed
                fixed = self.agent._repair_failing_case(  # noqa: SLF001
                    rust_project_path=self.context.rust_project_path,
                    bin_name=self.context.bin_name,
                    runner=self.context.runner,
                    project_structure=self.context.project_structure,
                    source_index=self.context.source_index,
                    failing_case=case,
                    baseline_pass_names=baseline_pass_names,
                )

                final_binary = self.agent._locate_release_binary(  # noqa: SLF001
                    self.context.rust_project_path, rust_bin_name
                )
                if final_binary:
                    self.context.runner.restage_rust_binary(final_binary)

                current_summary = self.context.runner.run_all(self.context.scripts)
                self.agent._print_summary(  # noqa: SLF001
                    current_summary,
                    label=f"第 {suite_cycle} 轮修复 {case.name} 后测试结果",
                )
                case_now_passed = any(
                    result.name == case.name and result.passed
                    for result in current_summary.results
                )
                if fixed and case_now_passed and current_summary.passed > passed_before:
                    fixed_any = True
                if current_summary.all_passed:
                    return current_summary

            if not fixed_any:
                remaining_unattempted = [
                    case.name
                    for case in current_summary.results
                    if not case.passed and case.name not in attempted_names
                ]
                if remaining_unattempted and suite_cycle < self.context.max_suite_repair_cycles:
                    print(
                        "[rtest] 本轮没有任何失败用例被修复，"
                        "继续下一轮套件修复剩余未尝试用例"
                    )
                    continue
                print("[rtest] 已达每个失败用例最多一次修复尝试（单用例最多 20 轮），仍有用例未通过")
                break

        return current_summary


def _script_size(case) -> int:
    try:
        return Path(case.script_path).stat().st_size
    except OSError:
        return 1 << 30
