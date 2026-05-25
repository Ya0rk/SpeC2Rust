"""修复 prompt 构建 + token 预算管理。

修复 **#18**：原实现对 ``provided_c_records`` / ``provided_rust_files``
只追加从不淘汰，大项目多轮后 prompt 会爆掉 max_tokens。本模块维护两个 LRU
容器，在总字符数超过 ``PROMPT_MATERIAL_BUDGET_CHARS`` 时按插入顺序淘汰。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Iterable, List, Optional

from .constants import (
    BUILD_ERROR_TAIL_CHARS,
    EXPECTED_OUTPUT_DISPLAY_CHARS,
    EXPECTED_OUTPUT_DISPLAY_COUNT,
    PROMPT_MATERIAL_BUDGET_CHARS,
    PROMPT_TRACE_TAIL_CHARS,
    REGRESSION_WARNING_TAIL_CHARS,
)
from .models import TestCaseResult


def _with_line_numbers(content: str) -> str:
    lines = (content or "").splitlines()
    if not lines:
        return ""
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(lines, start=1))


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
        self._rust_files: "OrderedDict[str, str]" = OrderedDict()
        self._test_artifacts: "OrderedDict[str, str]" = OrderedDict()

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
            return False
        self._c_records[key] = rec
        self._evict_if_needed()
        return True

    def has_c_record(self, rec: Dict) -> bool:
        return self._c_key(rec) in self._c_records

    def c_records(self) -> List[Dict]:
        return list(self._c_records.values())

    # ---------------- Rust files ----------------

    def add_rust_file(self, path: str, content: str) -> bool:
        if not path or not content:
            return False
        if path in self._rust_files and self._rust_files[path] == content:
            self._rust_files.move_to_end(path)
            return False
        self._rust_files[path] = content
        self._rust_files.move_to_end(path)
        self._evict_if_needed()
        return True

    def has_rust_file(self, path: str) -> bool:
        return path in self._rust_files

    def rust_files(self) -> Dict[str, str]:
        return dict(self._rust_files)

    # ---------------- Test artifacts ----------------

    def add_test_artifact(self, path: str, content: str) -> bool:
        if not path or not content:
            return False
        if path in self._test_artifacts and self._test_artifacts[path] == content:
            self._test_artifacts.move_to_end(path)
            return False
        self._test_artifacts[path] = content
        self._test_artifacts.move_to_end(path)
        self._evict_if_needed()
        return True

    def has_test_artifact(self, path: str) -> bool:
        return path in self._test_artifacts

    def test_artifacts(self) -> Dict[str, str]:
        return dict(self._test_artifacts)

    # ---------------- 预算维护 ----------------

    def total_chars(self) -> int:
        total = 0
        for rec in self._c_records.values():
            total += len(str(rec.get("source", "")))
        for content in self._rust_files.values():
            total += len(content)
        for content in self._test_artifacts.values():
            total += len(content)
        return total

    def _evict_if_needed(self) -> None:
        while self.total_chars() > self.budget_chars:
            if self._c_records and (
                not self._rust_files
                or len(self._c_records) >= len(self._rust_files)
            ):
                key, _ = self._c_records.popitem(last=False)
                print(f"    [rtest] prompt 预算超限，淘汰 C 源码：{key}")
            elif self._rust_files:
                path, _ = self._rust_files.popitem(last=False)
                print(f"    [rtest] prompt 预算超限，淘汰 Rust 文件：{path}")
            elif self._test_artifacts:
                path, _ = self._test_artifacts.popitem(last=False)
                print(f"    [rtest] prompt 预算超限，淘汰测试产物：{path}")
            else:
                break


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
) -> str:
    c_blocks: List[str] = []
    for rec in material.c_records():
        c_blocks.append(
            f"### `{rec.get('name')}` [{rec.get('file')} {rec.get('span')}]"
            f"\n```c\n{rec.get('source','')}\n```"
        )
    rust_blocks: List[str] = []
    for path, content in material.rust_files().items():
        rust_blocks.append(
            f"### {path}\n"
            "The left side of the code block, `NNNN |`, is the real file line number; edit start_line/end_line must use these line numbers; "
            "do not write the line-number prefix into content.\n"
            f"```text\n{_with_line_numbers(content)}\n```"
        )
    artifact_blocks: List[str] = []
    for path, content in material.test_artifacts().items():
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
            "it has been automatically rolled back. Please try a more focused repair approach:\n"
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

    c_block_text = "\n".join(c_blocks) if c_blocks else "(none)"
    rust_block_text = "\n".join(rust_blocks) if rust_blocks else "(none)"
    artifact_block_text = "\n".join(artifact_blocks) if artifact_blocks else "(none)"
    focused_failure_block = focused_failure or "(failed to extract a structured failure block; refer to the most recent run result and trace)"
    artifact_index_block = test_artifact_index or "(no readable test artifacts found)"

    return f"""You are fixing a Rust project translated from a C project so that it passes sh functional tests.
The current case failed (repair round {attempt}/{max_attempts}).

Test script runtime conventions (understand first; only change the failing script if you confirm it is a test-migration error):
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
  that is usually a test-migration or runtime issue. Prefer editing the current test script `test/{failing_case.name}`,
  make C/Rust run under the same wrapper/symlink name, or normalize symmetrically in the script's normalize_output;
  do not keep modifying Rust business logic repeatedly.

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
{trace_block}{expected_block}{build_error_block}{regression_block}
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
If stdout only shows a diff summary, prioritize the corresponding `.raw` / `.out` / `.log` files):
```text
{artifact_index_block}
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
    {{"kind": "file", "query": "C file name or relative path"}}
  ],
  "rust_read_requests": [
    {{"path": "src/<your_module>.rs"}}
  ],
  "test_artifact_read": [
    {{"path": "results/test6_fail.log"}},
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
  "complete": false,
  "updated_summary": "Updated brief memory"
}}

Requirements:
1. Only local edits are allowed: replace_range / delete_range / insert_before / insert_after.
2. Line numbers must be based on the actual line numbers of Rust files that have already been read. If the target Rust file has not been read yet,
   read it with rust_read_requests first; this round may return no edits.
   Provided Rust files are shown as `NNNN | code`; `NNNN` is the real line number to use in edits.
3. If you need more C source context, use cgr_read (kind is "function" or "file",
   query by name or relative path), and I will provide the C source in the next round.
4. By default, only edit the translated Rust project (`*.rs` / `Cargo.toml`, etc.). The only exception:
   if the current failure is a test-migration error (for example argv[0] / `$C_BIN` / `$RUST_BIN` path differences, or the test script
   uses a non-equivalent runtime cwd/PATH), you may edit only the current failing script `test/{failing_case.name}`.
   Do not edit other test scripts, fixtures, the original C project, or the target directory.
5. **No fake implementations**: do not use placeholder styles such as `unimplemented!()` / `todo!()` / `panic!("not implemented")` /
   `panic!("stub")`; also do not paste the expected output literal found in the script directly into Rust source
   as the return value. Both approaches will be automatically rejected. Fixes must be based on the real C source logic.
6. If your change makes this case pass but causes other previously passing cases to fail (a regression), the whole change will be rolled back,
   so prefer minimal changes / only fix the code path related to this case's features.
7. If the current materials are not enough to edit safely, you may request materials only; you will see the response in the next round.
8. If the target Rust file is already in "provided Rust files" and the key C functions have also been provided,
   this round must provide edits or explicitly set complete=true; do not request the same provided file again, and do not just repeat the problem.
9. If you have determined that a Rust file is missing the corresponding C logic (for example main.rs only prints a placeholder output),
   you must fix that file directly with replace_range / insert_before / insert_after.
10. If cargo reports a private field / private method, do not continue accessing private members from outside the module;
    instead add the necessary public method inside the module that owns the struct, or switch to an existing public API.
11. When adding methods to an existing impl, insert them before that impl's closing brace; do not insert them into a later
    `impl Default`, trait impl, or other unrelated impl block.
12. If the current failing subcase shows the C/Rust outputs differ only by the binary absolute path, and the C source confirms the program prints argv[0],
    do not hardcode the C_BIN path in Rust; instead fix the current test script so the comparison is fair for argv[0].
"""
