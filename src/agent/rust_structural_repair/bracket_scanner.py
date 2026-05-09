"""栈式括号扫描器。

输入：Rust 源码（任意状态，可能不平衡）
输出：未闭合的开括号列表 + 多余的闭括号列表 + 平衡度

特别注意：必须在 mask_non_code 之后的字符串上扫描，避免字符串/注释里
的括号被误计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .tokenizer import mask_non_code, line_col_at


_MATCH = {")": "(", "]": "[", "}": "{"}


@dataclass
class UnclosedOpen:
    """未闭合的开括号。"""

    char: str          # '{' / '(' / '['
    offset: int        # 在原文中的字符偏移
    line: int          # 1-based
    col: int           # 1-based
    indent: str        # 该行的前导空白（用于决定补全闭合括号时的缩进）


@dataclass
class OrphanClose:
    """没有对应开括号的闭括号。"""

    char: str
    offset: int
    line: int
    col: int


@dataclass
class MismatchClose:
    """闭括号类型与栈顶不匹配。"""

    expected: str      # 栈顶期望的闭括号，例如 '}'
    actual: str        # 实际遇到的闭括号，例如 ')'
    offset: int
    line: int
    col: int


@dataclass
class ScanResult:
    unclosed_opens: List[UnclosedOpen] = field(default_factory=list)
    orphan_closes: List[OrphanClose] = field(default_factory=list)
    mismatches: List[MismatchClose] = field(default_factory=list)

    @property
    def is_balanced(self) -> bool:
        return not self.unclosed_opens and not self.orphan_closes and not self.mismatches


def scan_brackets(source: str) -> ScanResult:
    """扫描源码，返回所有未闭合开/孤立闭/类型不匹配信息。"""
    masked = mask_non_code(source)
    n = len(masked)
    stack: List[Tuple[str, int]] = []  # (open_char, offset)
    result = ScanResult()

    for i in range(n):
        ch = masked[i]
        if ch in "({[":
            stack.append((ch, i))
        elif ch in ")]}":
            expected_open = _MATCH[ch]
            if not stack:
                line, col = line_col_at(source, i)
                result.orphan_closes.append(OrphanClose(char=ch, offset=i, line=line, col=col))
            elif stack[-1][0] != expected_open:
                # 类型不匹配：弹出栈顶但记录错误
                top_char, _top_offset = stack.pop()
                line, col = line_col_at(source, i)
                result.mismatches.append(
                    MismatchClose(
                        expected=_close_for(top_char),
                        actual=ch,
                        offset=i,
                        line=line,
                        col=col,
                    )
                )
            else:
                stack.pop()

    # 剩余栈即未闭合
    for open_char, offset in stack:
        line, col = line_col_at(source, offset)
        indent = _indent_of_line(source, offset)
        result.unclosed_opens.append(
            UnclosedOpen(char=open_char, offset=offset, line=line, col=col, indent=indent)
        )

    return result


def _close_for(open_char: str) -> str:
    return {"(": ")", "[": "]", "{": "}"}.get(open_char, open_char)


def _indent_of_line(source: str, offset: int) -> str:
    """返回 offset 所在行的前导空白。"""
    line_start = source.rfind("\n", 0, offset) + 1
    j = line_start
    while j < len(source) and source[j] in (" ", "\t"):
        j += 1
    return source[line_start:j]


def bracket_imbalance(source: str) -> int:
    """快速计算 abs(open - close)。供调用方做轻量判断使用。"""
    masked = mask_non_code(source)
    opens = masked.count("{") + masked.count("(") + masked.count("[")
    closes = masked.count("}") + masked.count(")") + masked.count("]")
    return abs(opens - closes)
