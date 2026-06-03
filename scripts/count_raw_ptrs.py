#!/usr/bin/env python3
"""
统计 Rust 项目中的裸指针类型出现次数和裸指针解引用次数。

口径说明:
    - raw_ptr_type_occurrences:
        统计源码里 `*const` / `*mut` 类型语法的出现次数。
        这包含函数签名、字段类型、类型别名、cast 等位置，不仅仅是变量声明。
    - raw_ptr_dereferences:
        统计表达式中操作数可推断为裸指针的一元 `*` 解引用，例如
        `*ptr`、`(*ptr).field`、`**ptr`。

实现说明:
    - 使用轻量 Rust 词法扫描，跳过注释、嵌套块注释、普通字符串、raw string、
      字符字面量和字节/C 字符串，避免文本误匹配。
    - 不是完整 AST/类型解析器，裸指针解引用依赖局部类型标注、raw pointer
      cast 和常见 raw pointer 构造 API 做保守推断；相对于纯正则更适合论文实验统计。

用法:
    python count_raw_ptrs.py <project_folder_path>

输出:
    - 终端：每个 .rs 文件的统计 + 总计
    - JSON 文件：保存到目标项目文件夹下的 raw_ptr_stats.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


EXPR_START_KEYWORDS = {
    "as",
    "async",
    "await",
    "box",
    "break",
    "const",
    "continue",
    "crate",
    "false",
    "for",
    "if",
    "loop",
    "match",
    "move",
    "return",
    "self",
    "Self",
    "static",
    "super",
    "true",
    "unsafe",
    "while",
    "yield",
}

EXPR_END_KEYWORDS = {
    "crate",
    "false",
    "self",
    "Self",
    "super",
    "true",
}

EXPR_START_PUNCT = {"(", "[", "{", "*", "&", "&&", "!", "-", "..", "..=", "|", "||"}
EXPR_END_PUNCT = {")", "]", "}", "?"}

MULTI_CHAR_PUNCT = (
    "<<=",
    ">>=",
    "..=",
    "::",
    "->",
    "=>",
    "&&",
    "||",
    "==",
    "!=",
    "<=",
    ">=",
    "<<",
    ">>",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "..",
)

PUNCT_CHARS = set("()[]{};,.:!?+-*/%&|^~=<>#$@")
RAW_POINTER_QUALIFIERS = {"const", "mut"}
RAW_POINTER_RETURNING_METHODS = {
    "add",
    "byte_add",
    "byte_offset",
    "cast",
    "offset",
    "sub",
    "wrapping_add",
    "wrapping_byte_add",
    "wrapping_byte_offset",
    "wrapping_offset",
    "wrapping_sub",
}
RAW_POINTER_CONSTRUCTORS = {
    "addr_of",
    "addr_of_mut",
    "as_mut_ptr",
    "as_ptr",
    "into_raw",
    "null",
    "null_mut",
}
OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    column: int


@dataclass(frozen=True)
class Finding:
    line: int
    column: int
    snippet: str


@dataclass(frozen=True)
class AnalysisResult:
    raw_pointer_type_occurrences: list[Finding]
    raw_pointer_dereferences: list[Finding]


def _is_ident_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_ident_continue(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, ch in enumerate(text):
        if ch == "\n":
            starts.append(index + 1)
    return starts


def _position_for_index(starts: list[int], index: int) -> tuple[int, int]:
    line_index = bisect.bisect_right(starts, index) - 1
    return line_index + 1, index - starts[line_index] + 1


def _consume_line_comment(text: str, index: int) -> int:
    while index < len(text) and text[index] != "\n":
        index += 1
    return index


def _consume_block_comment(text: str, index: int) -> int:
    depth = 1
    index += 2
    while index < len(text) and depth > 0:
        if text.startswith("/*", index):
            depth += 1
            index += 2
            continue
        if text.startswith("*/", index):
            depth -= 1
            index += 2
            continue
        index += 1
    return index


def _consume_quoted_literal(text: str, index: int, prefix_len: int = 0) -> int:
    index += prefix_len + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return index


def _consume_raw_string(text: str, index: int) -> int | None:
    start = index
    if text.startswith("br", index) or text.startswith("cr", index):
        index += 2
    elif text[index] == "r":
        index += 1
    else:
        return None

    hash_count = 0
    while index < len(text) and text[index] == "#":
        hash_count += 1
        index += 1

    if index >= len(text) or text[index] != '"':
        return None

    index += 1
    terminator = '"' + ("#" * hash_count)
    end = text.find(terminator, index)
    if end == -1:
        return len(text)
    return end + len(terminator)


def _consume_char_literal(text: str, index: int) -> int | None:
    current = index + 1
    if current >= len(text) or text[current] in {"\n", "\r", "'"}:
        return None

    if text[current] == "\\":
        current += 1
        if current >= len(text):
            return len(text)
        if text[current] == "u" and current + 1 < len(text) and text[current + 1] == "{":
            current += 2
            while current < len(text) and text[current] != "}":
                current += 1
            if current < len(text):
                current += 1
        else:
            current += 1
    else:
        current += 1

    if current < len(text) and text[current] == "'":
        return current + 1
    return None


def tokenize_rust(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    text_len = len(text)
    starts = _line_starts(text)

    def add_token(kind: str, value: str, start_index: int) -> None:
        line, column = _position_for_index(starts, start_index)
        tokens.append(Token(kind, value, line, column))

    while index < text_len:
        ch = text[index]

        if ch.isspace():
            index += 1
            continue

        if text.startswith("//", index):
            index = _consume_line_comment(text, index + 2)
            continue
        if text.startswith("/*", index):
            index = _consume_block_comment(text, index)
            continue

        raw_string_end = None
        if ch in {"r", "b", "c"}:
            raw_string_end = _consume_raw_string(text, index)
        if raw_string_end is not None:
            index = raw_string_end
            continue

        if ch == '"' or (ch in {"b", "c"} and index + 1 < text_len and text[index + 1] == '"'):
            prefix_len = 0 if ch == '"' else 1
            index = _consume_quoted_literal(text, index, prefix_len=prefix_len)
            continue

        if ch == "'":
            char_end = _consume_char_literal(text, index)
            if char_end is not None:
                index = char_end
                continue
            if index + 1 < text_len and _is_ident_start(text[index + 1]):
                end = index + 2
                while end < text_len and _is_ident_continue(text[end]):
                    end += 1
                add_token("lifetime", text[index:end], index)
                index = end
                continue

        if ch == "r" and index + 2 < text_len and text[index + 1] == "#" and _is_ident_start(text[index + 2]):
            end = index + 3
            while end < text_len and _is_ident_continue(text[end]):
                end += 1
            add_token("ident", text[index + 2:end], index)
            index = end
            continue

        if _is_ident_start(ch):
            end = index + 1
            while end < text_len and _is_ident_continue(text[end]):
                end += 1
            value = text[index:end]
            kind = "keyword" if value in EXPR_START_KEYWORDS or value in {"mut", "let", "fn", "pub", "impl", "struct", "enum", "trait", "type", "where", "use"} else "ident"
            add_token(kind, value, index)
            index = end
            continue

        if ch.isdigit():
            end = index + 1
            while end < text_len and (text[end].isalnum() or text[end] == "_"):
                end += 1
            add_token("number", text[index:end], index)
            index = end
            continue

        matched = False
        for punct in MULTI_CHAR_PUNCT:
            if text.startswith(punct, index):
                add_token("punct", punct, index)
                index += len(punct)
                matched = True
                break
        if matched:
            continue

        if ch in PUNCT_CHARS:
            add_token("punct", ch, index)
            index += 1
            continue

        index += 1

    return tokens


def _can_start_expression(token: Token) -> bool:
    if token.kind in {"ident", "number"}:
        return True
    if token.kind == "keyword":
        return token.value in EXPR_START_KEYWORDS
    if token.kind == "punct":
        return token.value in EXPR_START_PUNCT
    return False


def _can_end_expression(token: Token) -> bool:
    if token.kind in {"ident", "number"}:
        return True
    if token.kind == "keyword":
        return token.value in EXPR_END_KEYWORDS
    if token.kind == "punct":
        return token.value in EXPR_END_PUNCT
    return False


def _is_identifier_like(token: Token) -> bool:
    return token.kind in {"ident", "keyword"} and token.value not in {
        "as",
        "const",
        "fn",
        "let",
        "mut",
        "pub",
        "type",
    }


def _raw_pointer_type_depth_at(
    tokens: list[Token],
    index: int,
    type_aliases: dict[str, int] | None = None,
) -> int:
    if index >= len(tokens):
        return 0

    aliases = type_aliases or {}
    token = tokens[index]
    if token.kind == "ident" and token.value in aliases:
        return aliases[token.value]

    depth = 0
    cursor = index
    while cursor + 1 < len(tokens):
        if tokens[cursor].value != "*":
            break
        qualifier = tokens[cursor + 1]
        if qualifier.kind != "keyword" or qualifier.value not in RAW_POINTER_QUALIFIERS:
            break
        depth += 1
        cursor += 2
    return depth


def _find_matching_token(tokens: list[Token], open_index: int) -> int | None:
    open_value = tokens[open_index].value
    close_value = OPEN_TO_CLOSE.get(open_value)
    if close_value is None:
        return None

    depth = 1
    for index in range(open_index + 1, len(tokens)):
        value = tokens[index].value
        if value == open_value:
            depth += 1
        elif value == close_value:
            depth -= 1
            if depth == 0:
                return index
    return None


def _find_statement_end(tokens: list[Token], start_index: int) -> int:
    stack: list[str] = []
    for index in range(start_index, len(tokens)):
        value = tokens[index].value
        if value in OPEN_TO_CLOSE:
            stack.append(OPEN_TO_CLOSE[value])
            continue
        if stack and value == stack[-1]:
            stack.pop()
            continue
        if not stack and value in {";", "}"}:
            return index
    return len(tokens)


def _top_level_token_indexes(tokens: list[Token], start: int, end: int, value: str) -> list[int]:
    indexes: list[int] = []
    stack: list[str] = []
    for index in range(start, end):
        token_value = tokens[index].value
        if token_value in OPEN_TO_CLOSE:
            stack.append(OPEN_TO_CLOSE[token_value])
            continue
        if stack and token_value == stack[-1]:
            stack.pop()
            continue
        if not stack and token_value == value:
            indexes.append(index)
    return indexes


def _has_raw_pointer_constructor_call(tokens: list[Token], start: int, end: int) -> bool:
    for index in range(start, end):
        token = tokens[index]
        if token.kind != "ident" or token.value not in RAW_POINTER_CONSTRUCTORS:
            continue

        next_token = tokens[index + 1] if index + 1 < end else None
        following = tokens[index + 2] if index + 2 < end else None
        if next_token is not None and next_token.value == "(":
            return True
        if next_token is not None and next_token.value == "!":
            return True
        if next_token is not None and next_token.value == "::":
            continue
        if next_token is not None and next_token.value == "<" and following is not None:
            return True
    return False


def _expression_raw_pointer_depth(
    tokens: list[Token],
    start: int,
    end: int,
    raw_pointer_bindings: dict[str, int],
    type_aliases: dict[str, int],
    raw_returning_functions: dict[str, int],
) -> int:
    while start < end and tokens[start].value == "(":
        matching = _find_matching_token(tokens, start)
        if matching is None or matching >= end:
            break
        if matching == end - 1:
            start += 1
            end -= 1
            continue
        break

    if start >= end:
        return 0

    if (
        start + 2 < end
        and tokens[start].value == "&"
        and tokens[start + 1].value == "raw"
        and tokens[start + 2].value in RAW_POINTER_QUALIFIERS
    ):
        return 1

    leading_dereferences = 0
    while start < end and tokens[start].value == "*":
        leading_dereferences += 1
        start += 1
    if leading_dereferences:
        depth = _expression_raw_pointer_depth(
            tokens,
            start,
            end,
            raw_pointer_bindings,
            type_aliases,
            raw_returning_functions,
        )
        return max(depth - leading_dereferences, 0)

    for as_index in reversed(_top_level_token_indexes(tokens, start, end, "as")):
        depth = _raw_pointer_type_depth_at(tokens, as_index + 1, type_aliases)
        if depth:
            return depth

    token = tokens[start]
    if _is_identifier_like(token):
        if start + 1 < end and tokens[start + 1].value == "(":
            return raw_returning_functions.get(token.value, 0)
        if token.value in raw_pointer_bindings:
            return raw_pointer_bindings[token.value]
        if start + 2 < end and tokens[start + 1].value == ".":
            method = tokens[start + 2]
            if method.kind == "ident" and method.value in RAW_POINTER_RETURNING_METHODS:
                return raw_pointer_bindings.get(token.value, 0)

    if _has_raw_pointer_constructor_call(tokens, start, end):
        return 1

    return 0


def _infer_raw_pointer_context(
    tokens: list[Token],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    type_aliases: dict[str, int] = {}
    raw_returning_functions: dict[str, int] = {}
    raw_pointer_bindings: dict[str, int] = {}

    for index, token in enumerate(tokens):
        if token.value == "type" and index + 3 < len(tokens):
            name_token = tokens[index + 1]
            equals_token = tokens[index + 2]
            if name_token.kind == "ident" and equals_token.value == "=":
                depth = _raw_pointer_type_depth_at(tokens, index + 3, type_aliases)
                if depth:
                    type_aliases[name_token.value] = depth

        if token.value == "fn" and index + 1 < len(tokens):
            name_token = tokens[index + 1]
            if name_token.kind == "ident":
                signature_end = _find_statement_end(tokens, index)
                for arrow_index in _top_level_token_indexes(tokens, index, signature_end, "->"):
                    depth = _raw_pointer_type_depth_at(tokens, arrow_index + 1, type_aliases)
                    if depth:
                        raw_returning_functions[name_token.value] = depth
                        break

    for index, token in enumerate(tokens):
        if token.value == ":" and index > 0:
            name_token = tokens[index - 1]
            if _is_identifier_like(name_token):
                depth = _raw_pointer_type_depth_at(tokens, index + 1, type_aliases)
                if depth:
                    raw_pointer_bindings[name_token.value] = depth

        if token.value == "let":
            cursor = index + 1
            if cursor < len(tokens) and tokens[cursor].value == "mut":
                cursor += 1
            if cursor >= len(tokens) or not _is_identifier_like(tokens[cursor]):
                continue

            name = tokens[cursor].value
            statement_end = _find_statement_end(tokens, index)
            equals_indexes = _top_level_token_indexes(tokens, cursor + 1, statement_end, "=")
            if not equals_indexes:
                continue

            depth = _expression_raw_pointer_depth(
                tokens,
                equals_indexes[0] + 1,
                statement_end,
                raw_pointer_bindings,
                type_aliases,
                raw_returning_functions,
            )
            if depth:
                raw_pointer_bindings[name] = depth

        if _is_identifier_like(token) and index + 1 < len(tokens) and tokens[index + 1].value == "=":
            statement_end = _find_statement_end(tokens, index)
            depth = _expression_raw_pointer_depth(
                tokens,
                index + 2,
                statement_end,
                raw_pointer_bindings,
                type_aliases,
                raw_returning_functions,
            )
            if depth:
                raw_pointer_bindings[token.value] = depth

    return raw_pointer_bindings, type_aliases, raw_returning_functions


def find_raw_pointer_type_occurrences(tokens: list[Token]) -> list[Token]:
    occurrences: list[Token] = []
    for index in range(len(tokens) - 1):
        if (
            tokens[index].value == "*"
            and tokens[index + 1].kind == "keyword"
            and tokens[index + 1].value in RAW_POINTER_QUALIFIERS
        ):
            occurrences.append(tokens[index])
    return occurrences


def count_raw_pointer_type_occurrences(tokens: list[Token]) -> int:
    return len(find_raw_pointer_type_occurrences(tokens))


OPERAND_END_TOKENS = {
    ";",
    ",",
    ")",
    "]",
    "}",
    "=>",
    "=",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "||",
    "&&",
    "==",
    "!=",
    "<=",
    ">=",
    "<",
    ">",
    "+",
    "-",
    "/",
    "%",
    "&",
    "|",
    "^",
    "..",
    "..=",
}


def _find_operand_end(tokens: list[Token], start_index: int) -> int:
    stack: list[str] = []
    index = start_index
    while index < len(tokens):
        value = tokens[index].value
        if value in OPEN_TO_CLOSE:
            stack.append(OPEN_TO_CLOSE[value])
            index += 1
            continue
        if stack:
            if value == stack[-1]:
                stack.pop()
            index += 1
            continue
        if index > start_index and value in OPERAND_END_TOKENS:
            break
        index += 1
    return index


def _is_raw_pointer_dereference(
    tokens: list[Token],
    star_index: int,
    raw_pointer_bindings: dict[str, int],
    type_aliases: dict[str, int],
    raw_returning_functions: dict[str, int],
) -> bool:
    cursor = star_index
    required_depth = 0
    while cursor < len(tokens) and tokens[cursor].value == "*":
        required_depth += 1
        cursor += 1

    if cursor >= len(tokens):
        return False

    operand_end = _find_operand_end(tokens, cursor)
    operand_depth = _expression_raw_pointer_depth(
        tokens,
        cursor,
        operand_end,
        raw_pointer_bindings,
        type_aliases,
        raw_returning_functions,
    )
    return operand_depth >= required_depth


def find_raw_pointer_dereferences(tokens: list[Token]) -> list[Token]:
    raw_pointer_bindings, type_aliases, raw_returning_functions = _infer_raw_pointer_context(tokens)
    occurrences: list[Token] = []
    for index, token in enumerate(tokens):
        if token.value != "*":
            continue

        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if next_token is None:
            continue
        if next_token.kind == "keyword" and next_token.value in {"const", "mut"}:
            continue
        if not _can_start_expression(next_token):
            continue

        prev_token = tokens[index - 1] if index > 0 else None
        if prev_token is None:
            if _is_raw_pointer_dereference(
                tokens,
                index,
                raw_pointer_bindings,
                type_aliases,
                raw_returning_functions,
            ):
                occurrences.append(token)
            continue
        if prev_token.kind == "punct" and prev_token.value == "::":
            continue
        if _can_end_expression(prev_token):
            continue

        if _is_raw_pointer_dereference(
            tokens,
            index,
            raw_pointer_bindings,
            type_aliases,
            raw_returning_functions,
        ):
            occurrences.append(token)

    return occurrences


def count_raw_pointer_dereferences(tokens: list[Token]) -> int:
    return len(find_raw_pointer_dereferences(tokens))


def _finding_from_token(token: Token, lines: list[str]) -> Finding:
    snippet = lines[token.line - 1].strip() if 0 < token.line <= len(lines) else ""
    return Finding(line=token.line, column=token.column, snippet=snippet)


def _finding_to_json(finding: Finding) -> dict[str, int | str]:
    return {
        "line": finding.line,
        "column": finding.column,
        "snippet": finding.snippet,
    }


def analyze_code_detailed(text: str) -> AnalysisResult:
    tokens = tokenize_rust(text)
    lines = text.splitlines()
    type_occurrences = [
        _finding_from_token(token, lines)
        for token in find_raw_pointer_type_occurrences(tokens)
    ]
    dereferences = [
        _finding_from_token(token, lines)
        for token in find_raw_pointer_dereferences(tokens)
    ]
    return AnalysisResult(
        raw_pointer_type_occurrences=type_occurrences,
        raw_pointer_dereferences=dereferences,
    )


def analyze_code(text: str) -> tuple[int, int]:
    result = analyze_code_detailed(text)
    return (
        len(result.raw_pointer_type_occurrences),
        len(result.raw_pointer_dereferences),
    )


def analyze_file(filepath: str) -> tuple[int, int]:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
        return analyze_code(handle.read())


def analyze_file_detailed(filepath: str) -> AnalysisResult:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
        return analyze_code_detailed(handle.read())


def analyze_project(project_path: str) -> tuple[dict[str, dict[str, object]], int, int]:
    results: dict[str, dict[str, object]] = {}
    total_type_occurrences = 0
    total_dereferences = 0

    rust_files: list[str] = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [directory for directory in dirs if directory != "target"]
        for filename in files:
            if filename.endswith(".rs"):
                rust_files.append(os.path.join(root, filename))

    if not rust_files:
        print("⚠ 未找到任何 .rs 文件")
        return results, 0, 0

    for filepath in sorted(rust_files):
        analysis = analyze_file_detailed(filepath)
        type_occurrences = len(analysis.raw_pointer_type_occurrences)
        dereferences = len(analysis.raw_pointer_dereferences)
        relative_path = Path(os.path.relpath(filepath, project_path)).as_posix()
        results[relative_path] = {
            "raw_ptr_type_occurrences": type_occurrences,
            "raw_ptr_declarations": type_occurrences,
            "raw_ptr_dereferences": dereferences,
            "raw_ptr_type_occurrence_lines": [
                finding.line for finding in analysis.raw_pointer_type_occurrences
            ],
            "raw_ptr_dereference_lines": [
                finding.line for finding in analysis.raw_pointer_dereferences
            ],
            "raw_ptr_type_occurrence_details": [
                _finding_to_json(finding)
                for finding in analysis.raw_pointer_type_occurrences
            ],
            "raw_ptr_dereference_details": [
                _finding_to_json(finding)
                for finding in analysis.raw_pointer_dereferences
            ],
        }
        total_type_occurrences += type_occurrences
        total_dereferences += dereferences

    return results, total_type_occurrences, total_dereferences


def _format_locations(details: object) -> str:
    if not isinstance(details, list):
        return ""
    locations: list[str] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        line = item.get("line")
        column = item.get("column")
        if isinstance(line, int) and isinstance(column, int):
            locations.append(f"{line}:{column}")
    return ", ".join(locations)


def _run_self_tests() -> None:
    cases = [
        (
            "skip strings and comments",
            r'''
fn demo(ptr: *mut i32) {
    let q = ptr as *const i32;
    let _s = "*mut i32 and *ptr in a normal string";
    let _r = r#"*const u8 and *raw in a raw string"#;
    // let bad: *mut i32 = *commented;
    /* let bad2: *const i32 = *commented2; */
    unsafe { let _ = *q; }
}
''',
            2,
            1,
        ),
        (
            "skip multiplication and reference dereferences",
            r'''
fn demo(optind: &mut usize, x: i32) {
    let _product = x * 2;
    *optind += 1;
}
''',
            0,
            0,
        ),
        (
            "raw aliases and pointer depth",
            r'''
type Raw = *mut *const i32;
unsafe fn demo(ptr: Raw) {
    let _ = **ptr;
}
''',
            2,
            2,
        ),
    ]

    for name, code, expected_types, expected_dereferences in cases:
        type_count, dereference_count = analyze_code(code)
        if (type_count, dereference_count) != (expected_types, expected_dereferences):
            raise AssertionError(
                f"{name}: expected {(expected_types, expected_dereferences)}, "
                f"got {(type_count, dereference_count)}"
            )
    print("✅ self-test 通过")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="统计 Rust 项目中裸指针类型出现次数和裸指针解引用次数"
    )
    parser.add_argument("project_path", type=str, nargs="?", help="Rust 项目文件夹路径")
    parser.add_argument("--self-test", action="store_true", help="运行内置回归测试")
    args = parser.parse_args()

    if args.self_test:
        _run_self_tests()
        return

    if not args.project_path:
        parser.error("缺少 project_path；或使用 --self-test 运行内置测试")

    project_root = os.path.abspath(args.project_path)
    if not os.path.isdir(project_root):
        print(f"❌ 路径不存在: {project_root}")
        sys.exit(1)

    print(f"📂 分析项目: {project_root}")
    print("─" * 72)
    print(f"{'文件':<50} {'类型出现':>8} {'解引用':>8}")
    print("─" * 72)

    results, total_types, total_dereferences = analyze_project(project_root)

    for filename, stats in sorted(results.items()):
        print(f"{filename:<50} {stats['raw_ptr_type_occurrences']:>8} {stats['raw_ptr_dereferences']:>8}")

    files_with_findings = {
        filename: stats
        for filename, stats in sorted(results.items())
        if stats["raw_ptr_type_occurrences"] or stats["raw_ptr_dereferences"]
    }
    if files_with_findings:
        print("\n检测明细（行:列）:")
        for filename, stats in files_with_findings.items():
            print(f"- {filename}")
            type_locations = _format_locations(stats.get("raw_ptr_type_occurrence_details"))
            dereference_locations = _format_locations(stats.get("raw_ptr_dereference_details"))
            if type_locations:
                print(f"  类型出现: {type_locations}")
            if dereference_locations:
                print(f"  解引用: {dereference_locations}")

    print("─" * 72)
    print(f"{'总计':<50} {total_types:>8} {total_dereferences:>8}")

    output_json = {
        "project": project_root,
        "counter_version": "rust_lexer_v3_locations",
        "metric_notes": {
            "raw_ptr_type_occurrences": "Occurrences of *const/*mut raw pointer type syntax.",
            "raw_ptr_declarations": "Backward-compatible alias of raw_ptr_type_occurrences.",
            "raw_ptr_dereferences": "Unary * dereference expressions whose operand can be conservatively inferred as a raw pointer.",
            "raw_ptr_*_lines": "1-based source line numbers for each detected occurrence.",
            "raw_ptr_*_details": "1-based line/column and stripped source snippet for each detected occurrence.",
        },
        "total_raw_ptr_type_occurrences": total_types,
        "total_raw_ptr_declarations": total_types,
        "total_raw_ptr_dereferences": total_dereferences,
        "files": results,
    }
    json_path = Path(project_root) / "raw_ptr_stats.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(output_json, handle, indent=2, ensure_ascii=False)

    print(f"\n✅ JSON 结果已保存至: {json_path}")


if __name__ == "__main__":
    main()
