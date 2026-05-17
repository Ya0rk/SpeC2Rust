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
            "下面代码块左侧 `NNNN |` 是真实文件行号，edit 的 start_line/end_line 必须使用这些行号；"
            "不要把行号前缀写进 content。\n"
            f"```text\n{_with_line_numbers(content)}\n```"
        )
    artifact_blocks: List[str] = []
    for path, content in material.test_artifacts().items():
        artifact_blocks.append(f"### {path}\n```text\n{content}\n```")

    build_error_block = ""
    if last_build_error:
        build_error_block = (
            "\n上一次编辑后 cargo build --release 仍然失败，请优先修复以下编译错误：\n"
            f"```text\n{last_build_error[-BUILD_ERROR_TAIL_CHARS:]}\n```\n"
        )

    regression_block = ""
    if regression_warning:
        regression_block = (
            "\n[警告] 上一次的修复虽然让本用例通过了，但破坏了原本通过的其它用例，"
            "已被自动回滚。请换一个更聚焦的修复思路：\n"
            f"```text\n{regression_warning[-REGRESSION_WARNING_TAIL_CHARS:]}\n```\n"
        )

    flags_list = list(flags or [])
    keywords_list = list(keywords or [])
    flags_line = ", ".join(flags_list) if flags_list else "（未识别）"
    keywords_line = ", ".join(keywords_list) if keywords_list else "（未识别）"

    expected_block = ""
    expected_outputs_list = list(expected_outputs or [])
    if expected_outputs_list:
        chunks: List[str] = []
        for idx, body in enumerate(expected_outputs_list[:EXPECTED_OUTPUT_DISPLAY_COUNT], start=1):
            chunks.append(
                f"#{idx} (脚本中作为期望输出存在，禁止把这段字面量直接复制进 Rust 源码)\n"
                f"```text\n{body[:EXPECTED_OUTPUT_DISPLAY_CHARS]}\n```"
            )
        expected_block = "\n脚本中检测到的期望输出片段（只用于你理解被测行为，不允许"
        expected_block += "在 Rust 里硬编码这些字符串作为返回值）：\n"
        expected_block += "\n".join(chunks) + "\n"

    trace_block = ""
    if failing_case.trace:
        trace_block = (
            "\nbash -x 复跑 trace（行首 `+ ` 是被执行的命令，最后一条非 `+` 行通常就是真正"
            "失败的地方）：\n"
            f"```text\n{failing_case.trace[-PROMPT_TRACE_TAIL_CHARS:]}\n```\n"
        )

    c_block_text = "\n".join(c_blocks) if c_blocks else "（无）"
    rust_block_text = "\n".join(rust_blocks) if rust_blocks else "（无）"
    artifact_block_text = "\n".join(artifact_blocks) if artifact_blocks else "（无）"
    focused_failure_block = focused_failure or "（未能提取结构化失败块，请参考最近一次执行结果和 trace）"
    artifact_index_block = test_artifact_index or "（未发现可读取的测试产物）"

    return f"""你正在修复一个由 C 项目翻译而来的 Rust 项目，让它通过 sh 功能测试。
当前用例失败（第 {attempt}/{max_attempts} 轮修复）。

测试脚本运行环境约定（先理解；只有确认是测试迁移错误时才允许改当前失败脚本）：
- 默认直接运行原始 C 项目的 sh 脚本，不预先用 LLM 重写。
- 原始脚本里的项目同名命令（例如 `head` / `which` 这类被测程序名）会由 runner 映射到 Rust 可执行文件；
  `$RUST_BIN` 和 `<bin_name>-rust` 也指向同一个 Rust 可执行文件。
- 如果存在 C 参考可执行文件，它只通过 `$C_BIN`、`$C_WRAPPER_BIN` 或 `<bin_name>-c` 暴露；
  不要把脚本里的项目同名命令误判为 C 参考。
- test runner 不会把 wrapper 目录注入 PATH；如果测试修改 PATH，这是被测行为本身，
  不要通过硬编码系统 PATH、忽略 PATH 环境变量、或绕过用户设置的 PATH 来修复
- 原始脚本通常是固定期望/fixture 测试；脚本失败通常意味着 Rust 实现与原 C 工具行为不一致，
  请围绕 C 实现修 Rust。只有脚本明确使用 `$C_BIN` / `<bin_name>-c` 时，才是 Rust vs C 对照。
- 但如果 diff 只来自 `$C_BIN` 与 `$RUST_BIN` 的绝对路径 / argv[0] / 二进制文件名不同，
  这通常是测试迁移或运行方式问题。优先编辑当前测试脚本 `test/{failing_case.name}`，
  让 C/Rust 在同名 wrapper/symlink 下运行，或在脚本 normalize_output 中做对称归一化；
  不要反复修改 Rust 业务代码。

被测特征推断（来自脚本名 / 内容；可能是 CLI flag、子命令、关键字符串，不针对任何特定项目风格）：
- flag 候选：{flags_line}
- 关键字候选：{keywords_line}
（请优先去 C 源码里找处理这些参数 / 子命令 / 输入特征的代码路径，再修对应的 Rust 实现，
 而不是猜 Rust 该返回什么）

当前失败子用例 / 精简差异（优先看这里）：
```text
{focused_failure_block}
```

测试脚本：{failing_case.name}
```bash
{script_content}
```

最近一次执行结果：
- exit_code: {failing_case.exit_code}
- stdout (尾部):
```
{failing_case.stdout}
```
- stderr (尾部):
```
{failing_case.stderr}
```
{trace_block}{expected_block}{build_error_block}{regression_block}
项目结构设计文档（spec agent 产出，作为修改指引）：
```
{project_structure or '（未提供，请保守地只改与失败相关的文件）'}
```

Rust 项目概览：
```
{rust_overview}
```

C 源码索引（按需请求；模仿 ContextualRustAgent 的语义按函数名或文件名请求）：
{source_records_index}

可读取的测试运行产物（通过 test_artifact_read 请求；路径相对当前测试运行目录。
如果 stdout 里只显示了 diff 摘要，优先读取对应的 `.raw` / `.out` / `.log` 文件）：
```text
{artifact_index_block}
```

已经提供给你的 C 源码（首轮已根据被测特征自动注入了相关函数，请优先看这部分）：
{c_block_text}

已经提供给你的 Rust 文件（首轮已根据被测特征自动注入了最相关的 Rust 文件，带真实行号）：
{rust_block_text}

已经提供给你的测试运行产物：
{artifact_block_text}

历史摘要：
{history_summary or '（无）'}

只返回 JSON，不要任何解释，结构如下：
{{
  "summary": "本轮分析（必须明确说明：被测特征在 C 里是怎么实现 / 处理的，当前 Rust 哪一段缺失或错误）",
  "cgr_read": [
    {{"kind": "function", "query": "C 函数名"}},
    {{"kind": "file", "query": "C 文件名或相对路径"}}
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
      "content": "替换后的合法 Rust 片段"
    }}
  ],
  "complete": false,
  "updated_summary": "更新后的简短记忆"
}}

要求：
1. 只允许局部编辑：replace_range / delete_range / insert_before / insert_after。
2. 行号必须基于已经读到的 Rust 文件实际行号。如果还没读到目标 Rust 文件，
   请先用 rust_read_requests 把它读出来，本轮可以不返回 edits。
   已提供的 Rust 文件使用 `NNNN | code` 展示；`NNNN` 就是 edit 要填写的真实行号。
3. 如果需要 C 源码加深理解，使用 cgr_read（kind 为 "function" 或 "file"，
   query 为名字或相对路径），下一轮我会把 C 源码贴给你。
4. 默认只允许编辑翻译出来的 Rust 工程（`*.rs` / `Cargo.toml` 等）。唯一例外：
   如果当前失败是测试迁移错误（例如 argv[0] / `$C_BIN` / `$RUST_BIN` 路径差异、测试脚本
   使用了不等价的运行 cwd/PATH），允许只编辑当前失败脚本 `test/{failing_case.name}`。
   禁止编辑其它测试脚本、fixture、原始 C 项目或 target 目录。
5. **严禁假实现**：禁止用 `unimplemented!()` / `todo!()` / `panic!("not implemented")` /
   `panic!("stub")` 等占位写法；也禁止把脚本里检测到的期望输出字面量原样塞进 Rust 源码
   作为返回值。这两种写法会被程序自动驳回。修复必须基于 C 源码的真实逻辑。
6. 如果你的修改让本用例通过但导致其它原本通过的用例失败（回归），整次修改会被回滚，
   请优先做最小改动 / 只修与本用例特征相关的代码路径。
7. 如果当前材料不足以安全编辑，可以只请求材料；下一轮你会看到响应。
8. 如果目标 Rust 文件已经出现在“已经提供给你的 Rust 文件”中，并且 C 侧关键函数也已经提供，
   本轮必须给出 edits 或明确 complete=true；不要重复请求同一个已提供文件，也不要只复述问题。
9. 如果你已经判断某个 Rust 文件缺失 C 中的对应逻辑（例如 main.rs 只有占位输出），
   必须直接用 replace_range / insert_before / insert_after 修复该文件。
10. 如果 cargo 报 private field / private method，禁止继续在外部模块访问 private 成员；
    应在拥有该结构体的模块内部增加必要的 public 方法，或改用已经存在的 public API。
11. 给已有 impl 增加方法时，必须插入到该 impl 的 closing brace 之前；不要插入到后面的
    `impl Default`、trait impl 或其它无关 impl 块里。
12. 如果当前失败子用例显示 C/Rust 输出只差二进制绝对路径，且 C 源码确认程序打印 argv[0]，
    不要在 Rust 里硬编码 C_BIN 路径；应修当前测试脚本，使比较方式对 argv[0] 公平。
"""
