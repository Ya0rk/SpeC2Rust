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
                    "你是 shell 测试脚本翻译助手。给你一段 C 项目原生 sh 测试，"
                    "请把它改写为可被 rtest 框架直接运行的等价测试，不能改变测试意图。"
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
        c_reference_rules = f"""2. 环境变量 `$C_BIN` 指向 C 参考可执行文件的绝对路径。
3. 测试 shell 内提供 `{project_name}-rust`（Rust）和 `{project_name}`（C）两个函数；
   这只是调用便利，不会把 wrapper 目录注入 PATH。"""
        comparison_rules = f"""7. 测试目的：验证 Rust 实现与 C 参考的行为一致。**优先**用：
   - `diff -u <("$C_BIN" args...) <("$RUST_BIN" args...)`
   - 或 `"$RUST_BIN" args... > out.txt` 后用 `grep -q` / `diff` 对比已知期望。
   如果被测程序本身会读取 PATH（例如 which / env / shell 类工具），必须确保 C 和
   Rust 在相同 PATH 下运行，禁止因为测试框架便利命令改变被测 PATH。
   对 usage/help/version 这类会打印 argv[0] 的输出，禁止直接比较 `$C_BIN --help`
   和 `$RUST_BIN --help` 的原始绝对路径；应使用上面的同名 `./{project_name}` wrapper
   运行，或对 C/Rust 输出做完全对称的 argv[0] 归一化。"""
        c_bin_output_rule = "- 禁止使用 `\\$RUST_BIN`、`\\$C_BIN` 这种转义，直接用 `\"$RUST_BIN\"` / `\"$C_BIN\"`。"
    else:
        c_reference_rules = f"""2. 当前没有可用的 C 参考可执行文件；运行时不会提供 `$C_BIN`，也不会提供 `{project_name}` C wrapper。
3. 禁止在翻译结果里引用 `$C_BIN`、`{project_name}` C wrapper 或其它 C 对照命令。"""
        comparison_rules = """7. 测试目的：验证 Rust 实现是否满足原脚本中的显式行为要求。
   因为没有 C 参考可执行文件，必须删除或改写 C 对照段，不能生成 `diff "$C_BIN"...`。
   优先保留原脚本里的 fixture、here-doc、固定期望文件、grep/case 断言、已知状态码检查；
   如果原脚本某一段只是在比较 C/Rust 输出且没有显式期望，可删除那一段 C 对照，
   但不能删除同一段对 Rust 行为的检查。"""
        c_bin_output_rule = "- 禁止使用 `\\$RUST_BIN` 这种转义，直接用 `\"$RUST_BIN\"`；本次禁止使用 `$C_BIN`。"
    return f"""把下面这段 C 项目原生 sh 测试脚本，翻译为可被 rtest 框架直接 `bash` 执行的版本。

rtest 运行约定（必须严格遵守）：
1. 环境变量 `$RUST_BIN` 指向 Rust 可执行文件的绝对路径（你的待测对象）。
{c_reference_rules}
4. 脚本启动时 cwd 是测试临时目录，已自动把原测试目录里所有非 `.sh` 文件作为 fixture 拷进来。
5. 你**不能**使用 `make` / `cc` / `gcc` 这类本地编译命令。
6. 一般不要写 `cd "$(dirname "$0")"` 之类——cwd 已经正确；但如果原测试依赖
   cwd / argv[0] / PATH 语义，可以在临时子目录里 symlink/copy `$RUST_BIN`
   为 `./{project_name}`，再用 `(cd "$rust_dir" && ./{project_name} args...)`
   保留 Rust 侧 argv[0] 语义。
{comparison_rules}
8. 如果原脚本里有 `./test_<name>` 这种 C 单元测试可执行的调用，**直接删除**该步骤
   （rtest 只验证 Rust 行为，C 单元测试与我们无关）。
9. 保留 here-doc 形式的 fixture 写入（如 `cat > foo.txt <<EOF ... EOF`），它们对测试是必要的。
10. 输出结尾打印一行明显的 success 标志（如 `echo "all <name> tests passed"`）。

输出要求：
- 只输出可直接 `bash` 执行的脚本正文，不要任何解释、不要 markdown 包裹。
- 必须以 `#!/usr/bin/env bash` 开头，第二行 `set -euo pipefail`。
{c_bin_output_rule}

原脚本（文件名：{script_name}）：
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
