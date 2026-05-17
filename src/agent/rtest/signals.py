"""从测试脚本里提取被测信号：

- CLI flag 候选（例如 ``-E``、``--help``）
- 关键字（脚本名、脚本内引号字符串中的标识符）
- 期望输出（heredoc 写入到 exp/expected 文件的内容）
- 假实现 / 硬编码作弊检测

修复了原实现的两个问题：

- **#7**：原 ``bash_internal`` 黑名单只有 ``--/-x/-o``，漏掉 ``-e/-u/-c`` 等
  常见 ``set -X`` / ``shopt -X`` 选项，导致它们被当作被测 CLI flag 污染推断。
- **#28**：原实现直接在整段脚本文本上匹配 flag，heredoc 里的 ``--foo`` 也会被
  收下。现在先把 heredoc 内容剔除再扫。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Set

from .constants import EXPECTED_OUTPUT_MAX_BODY_CHARS, FAKE_IMPL_HARDCODED_MIN_CHARS


# --------- 正则 ---------

_FLAG_RE = re.compile(r"(?<![\w-])(-[A-Za-z]|--[a-z][a-z0-9_-]+)\b")

# heredoc： `<<TAG ... TAG` / `<<-TAG ... TAG` / `<<\TAG ... TAG`
_HEREDOC_RE = re.compile(
    r"<<-?\\?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\b[^\n]*\n(?P<body>.*?)\n(?P=tag)\b",
    re.DOTALL,
)

# 匹配 `set -abc` / `shopt -s foo` 这样的行，用来剔除 shell 选项被误认为 CLI flag
_SHELL_OPTION_LINE_RE = re.compile(
    r"^\s*(?:set|shopt|trap|bash|sh)\b[^\n]*$",
    re.MULTILINE,
)

# 由于 _strip_shell_option_lines 已经把 `set -X` / `shopt -X` / `trap ...` 等整行剥掉，
# 短选项黑名单只保留真正不可能是被测程序 flag 的字面量：
# - ``--`` 是参数分隔符，永远不是程序功能 flag；
# 像 ``-v`` / ``-h`` / ``-e`` 这种虽然是 bash 内置，但同时也是极常见的程序短选项，
# 不能一刀切，否则会漏掉真正的被测 flag。
_BASH_INTERNAL_SHORT = {"--"}

# bash 本身的长选项，被测程序基本不会复用：
_BASH_INTERNAL_LONG = {
    "--posix", "--norc", "--noprofile", "--rcfile", "--init-file",
    "--login", "--noediting", "--dump-po-strings", "--dump-strings",
    "--debugger",
}


# --------- 正文预处理 ---------


def _strip_heredocs(script_text: str) -> str:
    """把脚本里所有 heredoc 块替换为空行，保留行号。"""
    if not script_text:
        return script_text

    def _blank_body(match: re.Match) -> str:
        full = match.group(0)
        # 用换行数相同的空字符串代替，避免后续行号错位
        return "\n" * full.count("\n")

    return _HEREDOC_RE.sub(_blank_body, script_text)


def _strip_shell_option_lines(script_text: str) -> str:
    """把 ``set -e`` / ``shopt -s foo`` 这类行整行清空，但保留换行符。"""
    if not script_text:
        return script_text
    return _SHELL_OPTION_LINE_RE.sub("", script_text)


def _script_body_for_flag_scan(script_text: str) -> str:
    """返回用于 flag 扫描的正文：剔除 heredoc 与 shell 选项行。"""
    body = _strip_heredocs(script_text)
    body = _strip_shell_option_lines(body)
    return body


# --------- 公共 API ---------


def extract_test_flags(script_name: str, script_text: str) -> List[str]:
    """推断脚本实际测试的 CLI flag 集合。

    - 脚本正文里出现的 ``-x`` / ``--foo-bar`` 收下；
    - 从脚本名后缀派生的候选（如 ``foo-E.sh`` -> ``-E``）必须在正文里
      真实出现过才采信；
    - 剔除 bash 内置短选项与长选项（``--posix`` 等）。
    """
    body = _script_body_for_flag_scan(script_text)

    body_flags: Set[str] = set()
    if body:
        for m in _FLAG_RE.finditer(body):
            body_flags.add(m.group(1))

    candidates_from_name: Set[str] = set()
    stem = Path(script_name).stem
    if "-" in stem:
        suffix = stem.split("-", 1)[1].strip()
        if suffix:
            if 1 <= len(suffix) <= 3 and not suffix.isdigit():
                candidates_from_name.add(f"-{suffix}")
            long_form = suffix.replace("_", "-").lower()
            if len(long_form) >= 2:
                candidates_from_name.add(f"--{long_form}")

    validated_from_name = candidates_from_name & body_flags
    flags = body_flags | validated_from_name

    return sorted(
        f
        for f in flags
        if f not in _BASH_INTERNAL_SHORT and f not in _BASH_INTERNAL_LONG
    )


def extract_test_keywords(script_name: str, script_text: str) -> List[str]:
    """提取脚本名 + 正文引号中的标识符（长度 >= 3）。"""
    keys: Set[str] = set()
    stem = Path(script_name).stem
    for token in re.split(r"[-_.\s]+", stem):
        if len(token) >= 3 and not token.isdigit():
            keys.add(token.lower())
    if script_text:
        for m in re.finditer(
            r"['\"]([A-Za-z_][A-Za-z0-9_-]{2,})['\"]", script_text
        ):
            keys.add(m.group(1).lower())
    return sorted(keys)


def extract_expected_outputs(script_text: str) -> List[str]:
    """提取 heredoc 写入 exp / expected 文件的期望输出内容，用于反作弊。"""
    outs: List[str] = []
    if not script_text:
        return outs
    for m in _HEREDOC_RE.finditer(script_text):
        body = m.group("body")
        if 0 < len(body) < EXPECTED_OUTPUT_MAX_BODY_CHARS:
            outs.append(body)
    return outs


# --------- 假实现检测 ---------

_FAKE_IMPL_RE = re.compile(
    r"\bunimplemented!\s*\(|\btodo!\s*\(|"
    r'panic!\s*\(\s*"[^"]*(?:not\s+implemented|todo|stub|fixme|placeholder)',
    re.IGNORECASE,
)


def violates_no_fake_impl(content: str, expected_outputs: Iterable[str]) -> str:
    """检查一段 Rust 代码是否存在假实现或硬编码期望输出。

    返回原因字符串（空串表示通过）。
    """
    if not content:
        return ""
    if _FAKE_IMPL_RE.search(content):
        return "包含 unimplemented!/todo!/panic 占位标记"
    for exp in expected_outputs:
        block = exp.strip()
        if len(block) >= FAKE_IMPL_HARDCODED_MIN_CHARS and block in content:
            return "包含与测试期望输出完全一致的字面量（疑似硬编码作弊）"
    return ""
