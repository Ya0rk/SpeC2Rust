"""把 C 项目自带的 sh 测试脚本翻译为 rtest 框架可直接运行的版本。

原始 C 测试脚本通常依赖 ``./<bin>`` / ``make`` / 当前工作目录，无法直接拿来跑
翻译后的 Rust 实现。rtest 的运行约定是：

- ``$RUST_BIN`` / ``$C_BIN`` 环境变量分别指向 Rust / C 可执行文件
- 测试 shell 内提供 ``<project>-rust`` / ``<project>`` 函数，但不改写 PATH
- 脚本运行 cwd 是临时目录，已经把测试目录里的 fixture 拷过来
- 测试目的是验证 Rust 与 C 行为一致：优先用 ``diff <($C_BIN ...) <($RUST_BIN ...)``

本模块用 LLM 把每个 sh 脚本翻译为这种约定，并做基本语法校验（``bash -n``）。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, List, Optional, Tuple

_BASH_CODE_RE = re.compile(r"```(?:bash|sh|shell)?\s*\n(.*?)```", re.DOTALL)


def translate_shell_tests(
    src_dir: Path,
    dst_dir: Path,
    *,
    project_name: str,
    c_binary_available: bool = True,
    llm: Any,
    adapter: Any,
    verbose: bool = False,
) -> List[Path]:
    """逐个翻译 ``src_dir`` 下所有 ``.sh`` 脚本，写入 ``dst_dir``。

    返回成功翻译并落盘的目标脚本路径列表。同时把原脚本作为
    ``<name>.orig.sh`` 留在 ``dst_dir`` 旁边便于人工审计。
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    for entry in sorted(src_dir.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".sh"):
            continue
        try:
            original = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            print(f"  [rtest][translator] 读取 {entry.name} 失败：{exc}")
            continue

        translated, raw = _translate_one(
            original=original,
            script_name=entry.name,
            project_name=project_name,
            c_binary_available=c_binary_available,
            llm=llm,
            adapter=adapter,
            verbose=verbose,
        )
        if not translated:
            print(f"  [rtest][translator] {entry.name} 翻译失败，跳过该脚本")
            continue

        target = dst_dir / entry.name
        target.write_text(translated, encoding="utf-8", newline="\n")
        try:
            if os.name != "nt":
                os.chmod(target, 0o755)
        except OSError:
            pass

        # 备份原脚本 + LLM 原始响应，方便人工核对
        try:
            (dst_dir / f"{entry.name}.orig").write_text(
                original, encoding="utf-8", newline="\n"
            )
            if verbose and raw:
                (dst_dir / f"{entry.name}.llm_raw.txt").write_text(
                    raw, encoding="utf-8", newline="\n"
                )
        except OSError:
            pass

        out.append(target)
        print(f"  [rtest][translator] 已翻译：{entry.name}")
    return out


# ---------------------------------------------------------------------------


def _translate_one(
    *,
    original: str,
    script_name: str,
    project_name: str,
    c_binary_available: bool,
    llm: Any,
    adapter: Any,
    verbose: bool,
) -> Tuple[Optional[str], Optional[str]]:
    """调用 LLM 翻译单个脚本，返回 (翻译后内容, 原始响应)。"""
    prompt = _build_prompt(original, script_name, project_name, c_binary_available)
    if hasattr(llm, "set_request_label"):
        try:
            llm.set_request_label(f"翻译测试脚本 {script_name}")
        except Exception:  # noqa: BLE001
            pass
    reply = llm.generate(
        [
            {
                "role": "system",
                "content": (
                    "You are a shell test script translation assistant. Given a native sh test from a C project, "
                    "rewrite it into an equivalent test that can run directly under the rtest framework without changing the test intent."
                ),
            },
            {"role": "user", "content": prompt},
        ]
    )
    raw = reply[0] if isinstance(reply, list) else reply
    if verbose:
        snippet = (raw or "")[:2000].replace("\n", "\n      ")
        print(f"  [rtest][translator] LLM raw（前 2k 字符）:\n      {snippet}")

    body = _extract_bash_block(raw or "")
    if not body:
        return None, raw
    body = body.strip()
    if not body:
        return None, raw

    # 兜底：补上 shebang 与 set -euo pipefail（如果用户没加）
    if not body.startswith("#!"):
        body = "#!/usr/bin/env bash\n" + body
    if "set -e" not in body and "set -euo" not in body:
        # 插到 shebang 后第一行
        lines = body.splitlines()
        lines.insert(1, "set -euo pipefail")
        body = "\n".join(lines)
    body = body.rstrip() + "\n"

    if not _syntax_check(body):
        # 失败时把出错脚本写入 .invalid，便于复盘
        return None, raw

    if not _looks_safe(body):
        print(
            f"  [rtest][translator] {script_name} 翻译结果含禁用调用（make/cc/gcc），已拒绝"
        )
        return None, raw

    return body, raw


def _build_prompt(
    original: str,
    script_name: str,
    project_name: str,
    c_binary_available: bool,
) -> str:
    if c_binary_available:
        c_reference_rules = f"""2. The `$C_BIN` environment variable points to the absolute path of the C reference executable.
3. The test shell provides two functions, `{project_name}-rust` (Rust) and `{project_name}` (C);
   these are only convenience wrappers and do not inject the wrapper directory into PATH."""
        comparison_rules = f"""7. Test purpose: verify behavioral consistency between the Rust implementation and the C reference. **Prefer**:
   - `diff -u <("$C_BIN" args...) <("$RUST_BIN" args...)`
   - 或 `"$RUST_BIN" args... > out.txt` 后用 `grep -q` / `diff` 对比已知期望。
   If the program under test reads PATH itself (for example, which / env / shell-like tools), make sure C and
   Rust run under the same PATH and do not change the tested PATH for convenience of the test framework.
   For usage/help/version outputs that print argv[0], do not compare the raw absolute paths from `$C_BIN --help`
   and `$RUST_BIN --help` directly; use the same-named `./{project_name}` wrapper above
   or normalize argv[0] symmetrically for both C and Rust outputs. """
        c_bin_output_rule = '- Do not escape `$RUST_BIN` or `$C_BIN`; use `"$RUST_BIN"` / `"$C_BIN"` directly.'
    else:
        c_reference_rules = f"""2. No C reference executable is available; runtime will not provide `$C_BIN`, nor the `{project_name}` C wrapper.
3. Do not reference `$C_BIN`, the `{project_name}` C wrapper, or other C comparison commands in the translated result."""
        comparison_rules = """7. Test purpose: verify that the Rust implementation satisfies the explicit behavioral requirements from the original script.
   Because no C reference executable is available, C comparison sections must be removed or rewritten, and `diff "$C_BIN"...` must not be generated.
   Prefer keeping fixtures, here-docs, fixed expected files, grep/case assertions, and known exit-code checks from the original script;
   if a section only compares C/Rust output and has no explicit expectation, that C comparison section may be removed,
   but checks for Rust behavior in the same section must not be removed. """
        c_bin_output_rule = '- Do not escape `$RUST_BIN`; use `"$RUST_BIN"` directly; `$C_BIN` is forbidden in this case.'
    return f"""Translate the following native sh test script from a C project into a version that can be executed directly by `bash` under the rtest framework.

rtest runtime conventions (must be followed strictly):
1. The `$RUST_BIN` environment variable points to the absolute path of the Rust executable (your target under test).
{c_reference_rules}
4. When the script starts, the cwd is the test temporary directory, and all non-`.sh` files from the original test directory have already been copied in as fixtures.
5. You **must not** use local compilation commands such as `make` / `cc` / `gcc`.
6. In general, do not write `cd "$(dirname "$0")"` or similar because the cwd is already correct; but if the original test depends on
   cwd / argv[0] / PATH semantics, you may symlink/copy `$RUST_BIN`
   to `./{project_name}` inside a temporary subdirectory and then run `(cd "$rust_dir" && ./{project_name} args...)`
   to preserve Rust-side argv[0] semantics.
{comparison_rules}
8. If the original script contains calls like `./test_<name>` to C unit-test executables, **delete** those steps directly
   (rtest only verifies Rust behavior; C unit tests are not relevant here).
9. Keep fixture writes in here-doc form (such as `cat > foo.txt <<EOF ... EOF`) because they are necessary for the test.
10. Print an obvious success marker at the end of the output (for example, `echo "all <name> tests passed"`).

Output requirements:
- Output only the script body that can be run directly by `bash`; do not provide explanations or markdown wrappers.
- Must start with `#!/usr/bin/env bash`, and the second line must be `set -euo pipefail`.
{c_bin_output_rule}

Original script (file name: {script_name}):
```bash
{original}
```
"""


def _extract_bash_block(text: str) -> str:
    if not text:
        return ""
    m = _BASH_CODE_RE.search(text)
    if m:
        return m.group(1)
    return text


def _syntax_check(script: str) -> bool:
    """用 ``bash -n`` 检查脚本语法是否合法。"""
    try:
        proc = subprocess.run(
            ["bash", "-n", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except FileNotFoundError:
        # 找不到 bash 时无法预检，让运行时再报错
        return True
    except subprocess.TimeoutExpired:
        return False
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="ignore").strip()
        print(f"  [rtest][translator] bash -n 失败：{err[:400]}")
        return False
    return True


_FORBIDDEN_PATTERNS = (
    re.compile(r"\bmake\b"),
    re.compile(r"\b(?:cc|gcc|clang)\b"),
    re.compile(r"(?<![\w./])\./test_[A-Za-z0-9_]+"),
)


def _looks_safe(script: str) -> bool:
    body = _strip_heredocs(script)
    for pat in _FORBIDDEN_PATTERNS:
        if pat.search(body):
            return False
    return True


_HEREDOC_RE = re.compile(
    r"<<-?\\?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\b[^\n]*\n(?P<body>.*?)\n(?P=tag)\b",
    re.DOTALL,
)


def _strip_heredocs(text: str) -> str:
    return _HEREDOC_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
