"""Rust 源码非代码区域屏蔽。

核心思想：把所有"对括号匹配无意义"的字符（字符串、注释、char 字面量、
lifetime 标记）替换为空格，但保留换行，使行号、列号与原文一一对应。
后续的括号扫描器只看屏蔽后的版本即可避免误判字符串/注释里的括号。

支持的 Rust 词法：
- 行注释 //...
- 块注释 /* ... */（支持嵌套）
- 普通字符串 "..."（含 \\ 转义）
- Byte 字符串 b"..."
- Raw 字符串 r"..."、r#"..."#、r##"..."##、br"..."、br#"..."# 等
- Char 字面量 'x'、'\\n'、'\\u{1F600}' 等
- Byte char 字面量 b'x'
- Lifetime 标记 'a、'static（不屏蔽，但与 char 区分）

不会因为以下情况而崩溃：
- 未闭合的字符串/注释（屏蔽到文件末尾）
- 截断的文件
"""

from __future__ import annotations

from typing import Tuple


def mask_non_code(source: str) -> str:
    """返回与 source 等长的字符串，其中字符串、注释、char 字面量被替换为空格。
    换行符 (\\n) 保留以维持行号一致。"""
    n = len(source)
    out = list(source)
    i = 0

    def blank_range(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = source[i]

        # 行注释 //...
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            if j == -1:
                j = n
            blank_range(i, j)
            i = j
            continue

        # 块注释 /* ... */（嵌套）
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if j + 1 < n and source[j] == "/" and source[j + 1] == "*":
                    depth += 1
                    j += 2
                elif j + 1 < n and source[j] == "*" and source[j + 1] == "/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank_range(i, j)
            i = j
            continue

        # Raw 字符串 r"..."、r#"..."#、br"..."、br#"..."# 等
        # 必须在普通 b"..." 和标识符之前判断
        end = _try_match_raw_string(source, i, n)
        if end is not None:
            blank_range(i, end)
            i = end
            continue

        # Byte 字符串 b"..."
        if c == "b" and i + 1 < n and source[i + 1] == '"':
            end = _scan_quoted_string(source, i + 2, n, '"')
            blank_range(i, end)
            i = end
            continue

        # Byte char b'x'
        if c == "b" and i + 1 < n and source[i + 1] == "'":
            end = _try_scan_char_literal(source, i + 1, n)
            if end is not None:
                blank_range(i, end)
                i = end
                continue
            # 否则按普通标识符处理
            i += 1
            continue

        # 普通字符串 "..."
        if c == '"':
            end = _scan_quoted_string(source, i + 1, n, '"')
            blank_range(i, end)
            i = end
            continue

        # Char 字面量 vs Lifetime
        if c == "'":
            char_end = _try_scan_char_literal(source, i, n)
            if char_end is not None:
                blank_range(i, char_end)
                i = char_end
                continue
            # 不是 char 字面量 → 是 lifetime（'a、'static、'_）
            # lifetime 内不会有括号，原样保留即可，跳过 ' 与后续标识符
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            i = j
            continue

        i += 1

    return "".join(out)


def _scan_quoted_string(source: str, start: int, n: int, quote: str) -> int:
    """从 start 开始扫描，返回字符串结束后的位置（含闭合 quote）。
    若未闭合则返回 n。"""
    j = start
    while j < n:
        ch = source[j]
        if ch == "\\" and j + 1 < n:
            j += 2
            continue
        if ch == quote:
            return j + 1
        j += 1
    return n


def _try_match_raw_string(source: str, i: int, n: int):
    """尝试匹配 r"..."、r#"..."#、br"..."、br#"..."# 等。
    若是 raw 字符串返回结束位置；否则返回 None。"""
    start = i
    # 可选前缀 b
    if source[i] == "b":
        if i + 1 >= n or source[i + 1] != "r":
            return None
        i += 2
    elif source[i] == "r":
        i += 1
    else:
        return None
    # 必须前面没有标识符字符（避免误匹配 foo_r"..."）
    if start > 0:
        prev = source[start - 1]
        if prev.isalnum() or prev == "_":
            return None
    # 计数 #
    hashes = 0
    while i < n and source[i] == "#":
        hashes += 1
        i += 1
    if i >= n or source[i] != '"':
        return None
    i += 1  # 跳过开 "
    terminator = '"' + "#" * hashes
    end = source.find(terminator, i)
    if end == -1:
        return n
    return end + len(terminator)


def _try_scan_char_literal(source: str, i: int, n: int):
    """尝试把 source[i] 处的 ' 当作 char 字面量起点扫描。
    成功返回结束位置；失败（lifetime）返回 None。

    判定策略：从 i+1 开始向后找闭合的 '。如果在闭合前遇到换行/EOF，
    或扫描到的内容明显是标识符（如 'static），则返回 None。"""
    if i >= n or source[i] != "'":
        return None
    j = i + 1
    if j >= n:
        return None
    first = source[j]
    # 转义字符
    if first == "\\":
        j += 1
        # 各种转义都以 ' 收尾
        while j < n and source[j] != "'" and source[j] != "\n":
            j += 1
        if j < n and source[j] == "'":
            return j + 1
        return None
    # 单字符
    if j + 1 < n and source[j + 1] == "'":
        return j + 2
    # 否则视为 lifetime
    return None


def line_col_at(source: str, offset: int) -> Tuple[int, int]:
    """根据偏移量计算 1-based 行号与 1-based 列号。"""
    if offset <= 0:
        return 1, 1
    sub = source[: min(offset, len(source))]
    line = sub.count("\n") + 1
    last_nl = sub.rfind("\n")
    col = offset - last_nl if last_nl >= 0 else offset + 1
    return line, col
