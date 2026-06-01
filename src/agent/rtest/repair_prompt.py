"""修复 prompt 构建 + token 预算管理。

修复 **#18**：原实现对 ``provided_c_records`` / ``provided_rust_files``
只追加从不淘汰，大项目多轮后 prompt 会爆掉 max_tokens。本模块维护两个 LRU
容器，在总字符数超过 ``PROMPT_MATERIAL_BUDGET_CHARS`` 时按插入顺序淘汰。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

from .constants import (
    BUILD_ERROR_TAIL_CHARS,
    EXPECTED_OUTPUT_DISPLAY_CHARS,
    EXPECTED_OUTPUT_DISPLAY_COUNT,
    PROMPT_MATERIAL_BUDGET_CHARS,
    PROMPT_TRACE_TAIL_CHARS,
    REGRESSION_WARNING_TAIL_CHARS,
)
from .models import TestCaseResult


def _with_line_numbers(content: str, start_line: int = 1) -> str:
    lines = (content or "").splitlines()
    if not lines:
        return ""
    start = max(1, int(start_line or 1))
    end = start + len(lines) - 1
    width = max(4, len(str(end)))
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(lines, start=start))


class MaterialBudget:
    """带 LRU 淘汰的"已注入材料"容器。

    两类材料共享一个字符预算：
    - C 源码记录（一条记录对应 rec['source']）
    - Rust 文件（path -> content）

    插入顺序即淘汰顺序。访问（``touch``）时会把 key 挪到最新。
    """

    def __init__(self, budget_chars: int = PROMPT_MATERIAL_BUDGET_CHARS):
        self.budget_chars = budget_chars
        self._c_records: "OrderedDict[str, Dict]" = OrderedDict()  # key -> rec
        self._rust_files: "OrderedDict[str, Dict]" = OrderedDict()
        self._test_artifacts: "OrderedDict[str, Dict]" = OrderedDict()
        self._lru: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
        self._eviction_events: List[Dict[str, object]] = []

    def _touch(self, kind: str, key: str) -> None:
        marker = (kind, key)
        self._lru[marker] = None
        self._lru.move_to_end(marker)

    # ---------------- C records ----------------

    @staticmethod
    def _c_key(rec: Dict) -> str:
        name = str(rec.get("name") or "")
        file_path = str(rec.get("file") or "")
        return f"{file_path}::{name}"

    def add_c_record(self, rec: Dict) -> bool:
        """添加 C 源码记录，返回是否新增成功。"""
        if not rec:
            return False
        key = self._c_key(rec)
        if key in self._c_records:
            self._c_records.move_to_end(key)
            self._touch("c", key)
            return False
        self._c_records[key] = rec
        self._touch("c", key)
        self._evict_if_needed(protected=("c", key))
        return key in self._c_records

    def has_c_record(self, rec: Dict) -> bool:
        return self._c_key(rec) in self._c_records

    def c_records(self) -> List[Dict]:
        return list(self._c_records.values())

    # ---------------- Rust files ----------------

    @staticmethod
    def _range_key(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        normalized = (path or "").replace("\\", "/")
        if isinstance(start_line, int) and isinstance(end_line, int):
            return f"{normalized}:{start_line}-{end_line}"
        return normalized

    @staticmethod
    def _normalize_path(path: str) -> str:
        return (path or "").replace("\\", "/")

    @staticmethod
    def _normalize_range(
        start_line: Optional[int],
        end_line: Optional[int],
    ) -> Optional[Tuple[int, int]]:
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            return None
        start = max(1, start_line)
        end = max(1, end_line)
        if end < start:
            start, end = end, start
        return start, end

    def _rust_line_intervals(self, path: str) -> List[Tuple[int, int]]:
        normalized = self._normalize_path(path)
        intervals: List[Tuple[int, int]] = []
        for entry in self._rust_files.values():
            if self._normalize_path(str(entry.get("path") or "")) != normalized:
                continue
            rng = self._normalize_range(
                entry.get("start_line"),
                entry.get("end_line"),
            )
            if rng:
                intervals.append(rng)
        if not intervals:
            return []
        intervals.sort()
        merged: List[Tuple[int, int]] = []
        for start, end in intervals:
            if not merged or start > merged[-1][1] + 1:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        return merged

    def uncovered_rust_ranges(
        self,
        path: str,
        start_line: int,
        end_line: int,
    ) -> List[Tuple[int, int]]:
        """Return sub-ranges not already covered by provided Rust snippets."""
        normalized = self._normalize_path(path)
        requested = self._normalize_range(start_line, end_line)
        if not normalized or not requested:
            return []
        if self._range_key(normalized) in self._rust_files:
            return []

        req_start, req_end = requested
        cursor = req_start
        missing: List[Tuple[int, int]] = []
        for have_start, have_end in self._rust_line_intervals(normalized):
            if have_end < cursor:
                continue
            if have_start > req_end:
                break
            if have_start > cursor:
                missing.append((cursor, min(req_end, have_start - 1)))
            cursor = max(cursor, have_end + 1)
            if cursor > req_end:
                break
        if cursor <= req_end:
            missing.append((cursor, req_end))
        return missing

    def add_rust_file(
        self,
        path: str,
        content: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        mode: str = "whole_file",
    ) -> bool:
        if not path or not content:
            return False
        key = self._range_key(path, start_line, end_line) if mode == "line_range" else self._range_key(path)
        if key in self._rust_files and self._rust_files[key].get("content") == content:
            self._rust_files.move_to_end(key)
            self._touch("rust", key)
            return False
        self._rust_files[key] = {
            "path": path.replace("\\", "/"),
            "display_path": key,
            "content": content,
            "mode": mode,
            "start_line": start_line,
            "end_line": end_line,
        }
        self._rust_files.move_to_end(key)
        self._touch("rust", key)
        self._evict_if_needed(protected=("rust", key))
        return key in self._rust_files

    def has_rust_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> bool:
        normalized = self._normalize_path(path)
        if self._range_key(normalized) in self._rust_files:
            return True
        requested = self._normalize_range(start_line, end_line)
        if requested:
            start, end = requested
            if self._range_key(normalized, start, end) in self._rust_files:
                return True
            return not self.uncovered_rust_ranges(normalized, start, end)
        return False

    def rust_files(self) -> Dict[str, str]:
        return {
            str(entry.get("display_path") or key): str(entry.get("content") or "")
            for key, entry in self._rust_files.items()
        }

    def rust_file_entries(self) -> List[Dict]:
        return list(self._rust_files.values())

    # ---------------- Test artifacts ----------------

    def add_test_artifact(
        self,
        path: str,
        content: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        mode: str = "whole_file",
    ) -> bool:
        if not path or not content:
            return False
        key = self._range_key(path, start_line, end_line) if mode == "line_range" else self._range_key(path)
        if key in self._test_artifacts and self._test_artifacts[key].get("content") == content:
            self._test_artifacts.move_to_end(key)
            self._touch("test", key)
            return False
        self._test_artifacts[key] = {
            "path": path.replace("\\", "/"),
            "display_path": key,
            "content": content,
            "mode": mode,
            "start_line": start_line,
            "end_line": end_line,
        }
        self._test_artifacts.move_to_end(key)
        self._touch("test", key)
        self._evict_if_needed(protected=("test", key))
        return key in self._test_artifacts

    def has_test_artifact(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> bool:
        normalized = (path or "").replace("\\", "/")
        if self._range_key(normalized) in self._test_artifacts:
            return True
        if isinstance(start_line, int) and isinstance(end_line, int):
            return self._range_key(normalized, start_line, end_line) in self._test_artifacts
        return False

    def test_artifacts(self) -> Dict[str, str]:
        return {
            str(entry.get("display_path") or key): str(entry.get("content") or "")
            for key, entry in self._test_artifacts.items()
        }

    def test_artifact_entries(self) -> List[Dict]:
        return list(self._test_artifacts.values())

    # ---------------- 预算维护 ----------------

    def total_chars(self) -> int:
        total = 0
        for rec in self._c_records.values():
            total += len(str(rec.get("source", "")))
        for entry in self._rust_files.values():
            total += len(str(entry.get("content", "")))
        for entry in self._test_artifacts.values():
            total += len(str(entry.get("content", "")))
        return total

    def _evict_if_needed(self, protected: Optional[Tuple[str, str]] = None) -> None:
        while self.total_chars() > self.budget_chars:
            victim = self._pop_oldest_unprotected(protected)
            if not victim:
                self._record_over_budget_without_victim(protected)
                break

    def _pop_oldest_unprotected(self, protected: Optional[Tuple[str, str]]) -> bool:
        for marker in list(self._lru.keys()):
            kind, key = marker
            if protected and marker == protected:
                continue
            if kind == "c" and key in self._c_records:
                rec = self._c_records.pop(key)
                self._lru.pop(marker, None)
                size = len(str(rec.get("source", "")))
                self._record_eviction("C 源码", key, size, "prompt budget exceeded")
                return True
            if kind == "rust" and key in self._rust_files:
                entry = self._rust_files.pop(key)
                self._lru.pop(marker, None)
                size = len(str(entry.get("content", "")))
                self._record_eviction("Rust 文件", key, size, "prompt budget exceeded")
                return True
            if kind == "test" and key in self._test_artifacts:
                entry = self._test_artifacts.pop(key)
                self._lru.pop(marker, None)
                size = len(str(entry.get("content", "")))
                self._record_eviction("测试产物", key, size, "prompt budget exceeded")
                return True
            self._lru.pop(marker, None)
        return False

    def _record_eviction(self, label: str, key: str, size: int, reason: str) -> None:
        print(f"    [rtest] prompt 预算超限，淘汰 {label}：{key}")
        self._eviction_events.append({
            "kind": label,
            "key": key,
            "chars": size,
            "reason": reason,
        })
        self._eviction_events = self._eviction_events[-12:]

    def _record_over_budget_without_victim(self, protected: Optional[Tuple[str, str]]) -> None:
        if not protected:
            return
        kind, key = protected
        self._eviction_events.append({
            "kind": kind,
            "key": key,
            "chars": self.total_chars(),
            "reason": "single protected material exceeds the soft budget and is kept for this round",
        })
        self._eviction_events = self._eviction_events[-12:]

    def budget_pressure_summary(self) -> str:
        lines = [
            f"- material_budget_chars: {self.budget_chars}",
            f"- current_material_chars: {self.total_chars()}",
        ]
        if not self._eviction_events:
            return "\n".join(lines)
        lines.append("- evicted_or_over_budget_materials:")
        for event in self._eviction_events[-8:]:
            lines.append(
                f"  - {event.get('kind')}: {event.get('key')} "
                f"({event.get('chars')} chars) reason={event.get('reason')}"
            )
        lines.append(
            "- If required whole files were evicted or too large, request smaller line_range snippets instead of repeating the same whole_file request."
        )
        lines.append(
            "- If old history is no longer useful, return `history_control: {\"drop_history\": true}` with a concise updated_summary."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------- prompt


def build_repair_prompt(
    *,
    failing_case: TestCaseResult,
    script_content: str,
    project_structure: str,
    rust_overview: str,
    material: MaterialBudget,
    history_summary: str,
    source_records_index: str,
    attempt: int,
    max_attempts: int,
    last_build_error: str = "",
    flags: Optional[Iterable[str]] = None,
    keywords: Optional[Iterable[str]] = None,
    expected_outputs: Optional[Iterable[str]] = None,
    regression_warning: str = "",
    focused_failure: str = "",
    test_artifact_index: str = "",
    runtime_evidence: Optional[Dict[str, object]] = None,
    log_agent_enabled: bool = False,
    active_static_probes: Optional[Iterable[object]] = None,
) -> str:
    c_blocks: List[str] = []
    for rec in material.c_records():
        c_blocks.append(
            f"### `{rec.get('name')}` [{rec.get('file')} {rec.get('span')}]"
            f"\n```c\n{rec.get('source','')}\n```"
        )
    rust_blocks: List[str] = []
    for entry in material.rust_file_entries():
        path = str(entry.get("display_path") or entry.get("path") or "")
        content = str(entry.get("content") or "")
        start_line = entry.get("start_line") if entry.get("mode") == "line_range" else 1
        rust_blocks.append(
            f"### {path}\n"
            "The left side of the code block, `NNNN |`, is the real file line number; edit start_line/end_line must use these line numbers; "
            "do not write the line-number prefix into content.\n"
            f"```text\n{_with_line_numbers(content, int(start_line or 1))}\n```"
        )
    artifact_blocks: List[str] = []
    for entry in material.test_artifact_entries():
        path = str(entry.get("display_path") or entry.get("path") or "")
        content = str(entry.get("content") or "")
        artifact_blocks.append(f"### {path}\n```text\n{content}\n```")

    build_error_block = ""
    if last_build_error:
        build_error_block = (
            "\nAfter the previous edit, `cargo build --release` still failed; please prioritize fixing the following compilation errors:\n"
            f"```text\n{last_build_error[-BUILD_ERROR_TAIL_CHARS:]}\n```\n"
        )

    regression_block = ""
    if regression_warning:
        regression_block = (
            "\n[Warning] The previous fix made this case pass, but broke other cases that had already passed; "
            "it has been automatically rolled back. Treat the regressed cases as hard constraints for the same repair target; "
            "do not switch to repairing the old case, and do not submit a patch that merely flips which case passes. "
            "Your next edit must explain and preserve the shared invariant that lets the current failing case and the regressed cases pass together. "
            "For stream-oriented or byte-oriented programs, be especially careful to separate flushable output bytes from unresolved lookahead/state bytes; "
            "flushing output early must not force ambiguous state to be finalized early.\n"
            f"```text\n{regression_warning[-REGRESSION_WARNING_TAIL_CHARS:]}\n```\n"
        )

    flags_list = list(flags or [])
    keywords_list = list(keywords or [])
    flags_line = ", ".join(flags_list) if flags_list else "(unrecognized)"
    keywords_line = ", ".join(keywords_list) if keywords_list else "(unrecognized)"

    expected_block = ""
    expected_outputs_list = list(expected_outputs or [])
    if expected_outputs_list:
        chunks: List[str] = []
        for idx, body in enumerate(expected_outputs_list[:EXPECTED_OUTPUT_DISPLAY_COUNT], start=1):
            chunks.append(
                f"#{idx} (exists in the script as expected output; do not copy this literal directly into Rust source)\n"
                f"```text\n{body[:EXPECTED_OUTPUT_DISPLAY_CHARS]}\n```"
            )
        expected_block = "\nDetected expected-output snippets in the script (for understanding the tested behavior only; do not "
        expected_block += "hardcode these strings as return values in Rust):\n"
        expected_block += "\n".join(chunks) + "\n"

    trace_block = ""
    if failing_case.trace:
        trace_block = (
            "\nbash -x rerun trace (lines starting with `+ ` are executed commands; the last non-`+` line is usually the real failure point):\n"
            f"```text\n{failing_case.trace[-PROMPT_TRACE_TAIL_CHARS:]}\n```\n"
        )

    runtime_block = (
        _build_runtime_evidence_block(runtime_evidence) if log_agent_enabled else ""
    )
    instrumentation_context = _build_instrumentation_context(
        log_agent_enabled, active_static_probes
    )
    instrumentation_json = _build_instrumentation_json_schema(log_agent_enabled)
    instrumentation_requirement = _build_instrumentation_requirement(log_agent_enabled)

    c_block_text = "\n".join(c_blocks) if c_blocks else "(none)"
    rust_block_text = "\n".join(rust_blocks) if rust_blocks else "(none)"
    artifact_block_text = "\n".join(artifact_blocks) if artifact_blocks else "(none)"
    focused_failure_block = focused_failure or "(failed to extract a structured failure block; refer to the most recent run result and trace)"
    artifact_index_block = test_artifact_index or "(no readable test artifacts found)"
    budget_block = material.budget_pressure_summary()

    return f"""You are fixing a Rust project translated from a C project so that it passes sh functional tests.
The current case failed (repair round {attempt}/{max_attempts}).

Test script runtime conventions (understand first; all test scripts are human-preprocessed read-only inputs):
- Never edit any test shell script or fixture. The test suite is a fixed validation baseline maintained outside this repair loop.
- Run the original C project's sh script directly by default, without rewriting it with the LLM first.
- Commands in the original script that share the project name (for example, the tested program names like `head` / `which`) are mapped by the runner to the Rust executable;
  `$RUST_BIN` and `<bin_name>-rust` also point to the same Rust executable.
- If a C reference executable exists, it is exposed only through `$C_BIN`, `$C_WRAPPER_BIN`, or `<bin_name>-c`;
  do not mistake the project-named command in the script for the C reference.
- The test runner does not inject the wrapper directory into PATH; if the test mutates PATH, that is the behavior under test.
  Do not fix it by hardcoding the system PATH, ignoring the PATH environment variable, or bypassing the user-set PATH.
- The original script is usually a fixed-expected-output / fixture test; a failure usually means the Rust implementation disagrees with the original C tool behavior.
  Fix the Rust implementation around the C behavior. Only when the script explicitly uses `$C_BIN` / `<bin_name>-c` is it a Rust-vs-C comparison.
- But if the diff only comes from differences in the absolute paths / argv[0] / binary names of `$C_BIN` and `$RUST_BIN`,
  that indicates the fixed test baseline may require human preprocessing outside this agent. Do not edit the script or hardcode a path in Rust;
  explain the evidence in `summary` and request further readable artifacts if needed. Do not set `complete=true` just to stop a failing case.

Inferred tested features (from script name / content; may be CLI flags, subcommands, or key strings, and are not tied to any specific project style):
- Flag candidates: {flags_line}
- Keyword candidates: {keywords_line}
(Please look for the code paths in the C source that handle these parameters / subcommands / input features first, and then fix the corresponding Rust implementation,
 rather than guessing what Rust should return.)

Current failing subcase / minimized diff (look here first):
```text
{focused_failure_block}
```

Test script: {failing_case.name}
```bash
{script_content}
```

Most recent execution result:
- exit_code: {failing_case.exit_code}
- stdout (tail):
```
{failing_case.stdout}
```
- stderr (tail):
```
{failing_case.stderr}
```
    {trace_block}{runtime_block}{instrumentation_context}{expected_block}{build_error_block}{regression_block}
Project structure design document (output from the spec agent, used as a modification guide):
```
{project_structure or '(not provided; conservatively modify only files related to the failure)'}
```

Rust project overview:
```
{rust_overview}
```

C source index (request as needed; mimic ContextualRustAgent semantics by function name or file name):
{source_records_index}

Readable test-run artifacts (via test_artifact_read requests; paths are relative to the current test run directory.
If stdout only shows a diff summary, prioritize the corresponding `.raw` / `.out` / `.log` files. Large artifacts may also be requested with `mode="line_range"`.
If the failure is a timeout, first request/read `timeout_context.txt` and then the focused `timeout_trace.txt` or generated files under `tmp/`.
For generated-code projects, inspect generated source/binary artifacts such as `*.x.c`, `a.out`, compiler logs, or files under `tmp/` before repeatedly editing the Rust generator):
```text
{artifact_index_block}
```

Prompt material budget status:
```text
{budget_block}
```

Provided C source code (the first round already injected the relevant functions based on the tested features; inspect this first):
{c_block_text}

Provided Rust files (the first round already injected the most relevant Rust files with real line numbers):
{rust_block_text}

Provided test-run artifacts:
{artifact_block_text}

History summary:
{history_summary or '(none)'}

Return JSON only, with no explanation. Use the following structure:
{{
  "summary": "This round's analysis (must clearly explain how the tested feature is implemented/handled in C, and which part of Rust is missing or wrong)",
  "cgr_read": [
    {{"kind": "function", "query": "C function name"}},
    {{"kind": "file", "query": "C file name or relative path", "mode": "line_range", "start_line": 120, "end_line": 220}}
  ],
  "rust_read_requests": [
    {{"path": "src/<your_module>.rs", "mode": "line_range", "start_line": 120, "end_line": 220}},
    {{"path": "src/<small_module>.rs", "mode": "whole_file"}}
  ],
  "test_artifact_read": [
    {{"path": "results/test6_fail.log", "mode": "line_range", "start_line": 1, "end_line": 120}},
    {{"path": "results/test6_c.out"}},
    {{"path": "results/test6_rust.out"}}
  ],
  "edits": [
    {{
      "path": "src/<your_module>.rs",
      "mode": "replace_range",
      "start_line": 10,
      "end_line": 20,
      "content": "Replacement valid Rust snippet"
    }}
  ],
  "history_control": {{"drop_history": false}},
{instrumentation_json}
  "complete": false,
  "updated_summary": "Updated brief memory"
}}

Requirements:
1. Only local edits are allowed: replace_range / delete_range / insert_before / insert_after.
2. Line numbers must be based on the actual line numbers of Rust files that have already been read. If the target Rust file has not been read yet,
   read it with rust_read_requests first; this round may return no edits.
   Provided Rust files are shown as `NNNN | code`; `NNNN` is the real line number to use in edits.
3. If you need more C source context, use cgr_read (kind is "function" or "file",
   query by name or relative path). For large C/Rust files, prefer mode="line_range" with start_line/end_line.
   Whole-file requests are allowed when the whole file is genuinely necessary and fits the budget.
4. Only edit the translated Rust project (`*.rs` / `Cargo.toml`, etc.). Every test shell script and fixture is read-only,
   including the currently failing script. Do not edit the original C project or the target directory.
5. **No fake implementations**: do not use placeholder styles such as `unimplemented!()` / `todo!()` / `panic!("not implemented")` /
   `panic!("stub")`; also do not paste the expected output literal found in the script directly into Rust source
   as the return value. Both approaches will be automatically rejected. Fixes must be based on the real C source logic.
6. If your change makes this case pass but causes other previously passing cases to fail (a regression), the whole change will be rolled back,
   so prefer minimal changes / only fix the code path related to this case's features.
7. If the current materials are not enough to edit safely, you may request materials only; you will see the response in the next round.
   For large Rust/C/test files that were evicted or are too large to fit, prefer `mode="line_range"` with `start_line` / `end_line`.
   For timeout failures, do not keep guessing from the word "timeout"; request/read timeout artifacts or generated-code artifacts to identify the concrete subcase.
   If you use `test_artifact_read`, leave `edits` empty in the same round; artifact reads must be reviewed before editing.
8. If the target Rust file is already in "provided Rust files" and the key C functions have also been provided,
   this round must provide edits or request new focused evidence; do not request the same provided file again, do not set complete=true to stop,
   and do not just repeat the problem.
   If the full file was evicted because of prompt budget pressure, request a focused line_range for the exact area you need.
9. If you have determined that a Rust file is missing the corresponding C logic (for example main.rs only prints a placeholder output),
   you must fix that file directly with replace_range / insert_before / insert_after.
10. If cargo reports a private field / private method, do not continue accessing private members from outside the module;
    instead add the necessary public method inside the module that owns the struct, or switch to an existing public API.
11. When adding methods to an existing impl, insert them before that impl's closing brace; do not insert them into a later
    `impl Default`, trait impl, or other unrelated impl block.
12. If the current failing subcase shows the C/Rust outputs differ only by the binary absolute path, and the C source confirms the program prints argv[0],
    do not hardcode the C_BIN path in Rust and do not edit the test script. Report that the preprocessed read-only test baseline needs human review.
13. You may set `history_control.drop_history=true` when old history is crowding out useful source context. If you do, put only the new concise memory in `updated_summary`.
14. If the same failure persists after multiple edits to the same file region, stop editing that region and request new evidence first
    (C source, focused Rust line ranges, timeout artifacts, generated files, or instrumentation). A slightly different rewrite of the same region is not progress.
15. For Rust code that generates C/shell/source text, inspect the generated artifact before editing the generator. Prefer small logic changes
    to argument construction, shell option selection, escaping helpers, or branch conditions. Do not replace a large generated C template as one
    giant escaped Rust string unless no smaller fix exists; if a template edit is unavoidable, use focused line ranges and keep the JSON content
    short enough to remain parseable.
16. If a generated source file is empty or fails with "undefined reference to `main`", treat the immediate defect as "the generator emitted
    no compilable program". Do not assume the only valid fix is to fully clone the original C project's entire generated runtime. For script
    wrapper tests, a minimal generated program that writes the embedded script to a temporary file and execs the configured shell with preserved
    argv is an acceptable repair step, as long as it is based on the generator inputs and not hardcoded to the expected output.
17. Do not request broad C file chunks as a substitute for a repair plan. C file `line_range` requests should normally be at most 250 lines
    and should target a named function or a narrow area. If generated artifacts are listed, read those artifacts before asking for more C source.
{instrumentation_requirement}
"""


def _build_runtime_evidence_block(runtime_evidence: Optional[Dict[str, object]]) -> str:
    if not runtime_evidence:
        return ""
    return (
        "\n[Runtime evidence]\n"
        f"```json\n{json.dumps(runtime_evidence, ensure_ascii=False, indent=2)}\n```\n"
    )


def _build_instrumentation_context(
    enabled: bool, active_static_probes: Optional[Iterable[object]]
) -> str:
    if not enabled:
        return ""
    active = []
    for probe in active_static_probes or []:
        active.append(
            {
                "id": getattr(probe, "probe_id", ""),
                "target": getattr(probe, "target", ""),
                "file": getattr(probe, "file", ""),
                "line": getattr(probe, "line", 0),
                "expressions": list(getattr(probe, "expressions", []) or []),
                "label": getattr(probe, "label", ""),
            }
        )
    return (
        "\nLogAgent: enabled. Dynamic probes and temporary static logging probes are available. "
        "Static probes are applied only to temporary build copies, never to the source project.\n"
        f"Active static probes:\n```json\n{json.dumps(active, ensure_ascii=False, indent=2)}\n```\n"
    )


def _build_instrumentation_json_schema(enabled: bool) -> str:
    if not enabled:
        return ""
    return """  "debug_probe": {
    "target": "rust | c | both",
    "backend": "lldb",
    "targets": {
      "rust": {"breakpoints": [{"file": "src/<your_module>.rs", "line": 42}], "watch_expressions": ["state.len()"]},
      "c": {"breakpoints": [{"file": "src/source.c", "line": 42}], "watch_expressions": ["state"]}
    },
    "program_args": ["--help"],
    "collect_stack": true,
    "collect_locals": true
  },
  "static_probe_update": {
    "add": [
      {"id": "rust_before_branch", "target": "rust", "file": "src/<your_module>.rs", "line": 42, "expressions": ["state.len()"], "label": "before branch"},
      {"id": "c_before_branch", "target": "c", "file": "src/source.c", "line": 42, "expressions": ["state_len"], "label": "before branch"}
    ],
    "remove": ["obsolete_probe_id"],
    "clear": false,
    "program_args": ["--help"]
  },"""


def _build_instrumentation_requirement(enabled: bool) -> str:
    if not enabled:
        return ""
    return """13. Use `debug_probe` only when source/test materials cannot explain the failure. It supports `target` = `rust`, `c`, or `both`;
    when targeting both, provide target-specific breakpoint/watch data under `targets.rust` and `targets.c`.
14. Use `static_probe_update` when values must be observed across runs or in both C and Rust. Probe IDs persist for this failing case:
    `add` creates or replaces points, `remove` deletes named points, and `clear` removes all points. Static probes run on temporary project
    copies only. Use valid expressions for the target language; invalid instrumentation may produce a build error in the evidence.
15. Instrumentation is an evidence-gathering round: do not include `debug_probe` or `static_probe_update` together with non-empty `edits`
    or new material requests. After evidence appears, analyze it before requesting additional probes.
16. If the prompt budget is crowded, use `history_control.drop_history=true` and request only the new source lines or test lines needed next."""
