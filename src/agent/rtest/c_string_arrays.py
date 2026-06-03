"""Helpers for copying C string arrays into Rust constants."""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple


def c_string_array_lines_to_rust_static(
    lines: Iterable[str],
    *,
    constant_name: str,
    visibility: str = "",
    array_name: str = "",
) -> str:
    source_lines = list(lines)
    if array_name:
        source_lines = _select_c_array_block(source_lines, array_name) or source_lines
    values = extract_c_string_values(source_lines)
    if not values:
        return ""
    vis = visibility.strip()
    prefix = f"{vis} " if vis else ""
    out: List[str] = [f"{prefix}static {constant_name}: &[&str] = &["]
    out.extend(f"    {rust_string_literal(value)}," for value in values)
    out.append("];")
    return "\n".join(out) + "\n"


def _select_c_array_block(lines: List[str], array_name: str) -> List[str]:
    start = -1
    name_pattern = array_name.strip()
    if not name_pattern:
        return []
    for idx, line in enumerate(lines):
        if name_pattern not in line:
            continue
        start = idx
        break
    if start < 0:
        return []
    block: List[str] = []
    in_initializer = False
    depth = 0
    for line in lines[start:]:
        if "{" in line:
            in_initializer = True
        if in_initializer:
            block.append(line)
            depth += line.count("{")
            depth -= line.count("}")
            if depth <= 0 and "}" in line:
                break
    return block


def extract_c_string_values(lines: Iterable[str]) -> List[str]:
    values: List[str] = []
    for line in lines:
        value = _extract_joined_c_strings(line)
        if value is not None:
            values.append(value)
    return values


def _extract_joined_c_strings(line: str) -> Optional[str]:
    values: List[str] = []
    i = 0
    while i < len(line):
        if line[i] != '"':
            i += 1
            continue
        token, end = _read_c_string_token(line, i + 1)
        if token is None:
            break
        values.append(token)
        i = end + 1
    if not values:
        return None
    return "".join(values)


def _read_c_string_token(line: str, start: int) -> Tuple[Optional[str], int]:
    chars: List[str] = []
    i = start
    while i < len(line):
        ch = line[i]
        if ch == '"':
            return "".join(chars), i
        if ch != "\\":
            chars.append(ch)
            i += 1
            continue
        decoded, i = _decode_c_escape(line, i + 1)
        if decoded is not None:
            chars.append(decoded)
    return None, i


def _decode_c_escape(text: str, i: int) -> Tuple[Optional[str], int]:
    if i >= len(text):
        return "\\", i
    ch = text[i]
    simple = {
        "a": "\x07",
        "b": "\x08",
        "f": "\x0c",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\x0b",
        "\\": "\\",
        "'": "'",
        '"': '"',
        "?": "?",
    }
    if ch in simple:
        return simple[ch], i + 1
    if ch in "\r\n":
        # C line continuation. The current helper operates per line, so just
        # drop the continuation marker instead of inserting text.
        return "", i + 1
    if ch == "x":
        j = i + 1
        while j < len(text) and text[j] in "0123456789abcdefABCDEF":
            j += 1
        if j == i + 1:
            return "x", j
        try:
            return chr(int(text[i + 1:j], 16)), j
        except ValueError:
            return "", j
    if ch in "01234567":
        j = i
        while j < len(text) and j < i + 3 and text[j] in "01234567":
            j += 1
        try:
            return chr(int(text[i:j], 8)), j
        except ValueError:
            return "", j
    return ch, i + 1


def rust_string_literal(value: str) -> str:
    parts: List[str] = ['"']
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            parts.append("\\\\")
        elif ch == '"':
            parts.append('\\"')
        elif ch == "\n":
            parts.append("\\n")
        elif ch == "\r":
            parts.append("\\r")
        elif ch == "\t":
            parts.append("\\t")
        elif ch == "\0":
            parts.append("\\0")
        elif 0x20 <= code <= 0x7E:
            parts.append(ch)
        elif code <= 0x7F:
            parts.append(f"\\x{code:02x}")
        else:
            parts.append(f"\\u{{{code:x}}}")
    parts.append('"')
    return "".join(parts)
