#!/usr/bin/env python3
"""Count C function definitions under a directory, excluding test directories."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "typedef",
}


@dataclass(frozen=True)
class FileCount:
    path: Path
    count: int


def is_under_test_dir(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts[:-1]
    return any(part.lower() == "test" for part in relative_parts)


def strip_comments_and_literals(text: str) -> str:
    result: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if ch == "/" and nxt == "/":
            result.append("  ")
            i += 2
            while i < n and text[i] != "\n":
                result.append(" ")
                i += 1
            continue

        if ch == "/" and nxt == "*":
            result.append("  ")
            i += 2
            while i < n - 1:
                if text[i] == "*" and text[i + 1] == "/":
                    result.append("  ")
                    i += 2
                    break
                result.append("\n" if text[i] == "\n" else " ")
                i += 1
            continue

        if ch in {'"', "'"}:
            quote = ch
            result.append(" ")
            i += 1
            while i < n:
                curr = text[i]
                if curr == "\\":
                    result.append(" ")
                    if i + 1 < n:
                        result.append("\n" if text[i + 1] == "\n" else " ")
                    i += 2
                    continue
                result.append("\n" if curr == "\n" else " ")
                i += 1
                if curr == quote:
                    break
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def find_signature_open(normalized: str) -> int | None:
    if not normalized.endswith(")"):
        return None

    depth = 0
    for idx in range(len(normalized) - 1, -1, -1):
        ch = normalized[idx]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                return idx
    return None


def is_function_definition(candidate: str) -> bool:
    normalized = " ".join(candidate.split())
    if not normalized or normalized.startswith("#"):
        return False

    open_idx = find_signature_open(normalized)
    if open_idx is None:
        return False

    prefix = normalized[:open_idx].rstrip()
    if not prefix or "=" in prefix:
        return False

    name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", prefix)
    if not name_match:
        return False

    name = name_match.group(1)
    if name in CONTROL_KEYWORDS:
        return False

    if prefix.endswith(("struct", "union", "enum")):
        return False

    return True


def count_functions_in_file(path: Path) -> int:
    content = strip_comments_and_literals(
        path.read_text(encoding="utf-8", errors="replace")
    )

    count = 0
    brace_depth = 0
    top_level_buffer: list[str] = []

    for ch in content:
        if brace_depth == 0:
            if ch == ";":
                top_level_buffer.clear()
                continue

            if ch == "{":
                candidate = "".join(top_level_buffer).strip()
                if is_function_definition(candidate):
                    count += 1
                top_level_buffer.clear()
                brace_depth = 1
                continue

            if ch == "}":
                top_level_buffer.clear()
                continue

            top_level_buffer.append(ch)
            continue

        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1

    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count C function definitions under a directory recursively."
    )
    parser.add_argument("target_dir", help="Path to the project directory")
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print per-file function counts",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_dir = Path(args.target_dir).expanduser().resolve()

    if not target_dir.exists():
        print(f"error: directory does not exist: {target_dir}", file=sys.stderr)
        return 1

    if not target_dir.is_dir():
        print(f"error: path is not a directory: {target_dir}", file=sys.stderr)
        return 1

    source_files = sorted(
        path
        for path in target_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".c", ".h"}
        and not is_under_test_dir(path, target_dir)
    )

    file_counts = [FileCount(path, count_functions_in_file(path)) for path in source_files]
    total_functions = sum(item.count for item in file_counts)

    print(f"target_dir: {target_dir}")
    print(f"source_files: {len(file_counts)}")
    print(f"total_functions: {total_functions}")

    if args.details:
        print("")
        for item in file_counts:
            print(f"{item.path.relative_to(target_dir)}\t{item.count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
