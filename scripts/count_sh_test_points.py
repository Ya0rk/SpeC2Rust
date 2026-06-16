#!/usr/bin/env python3
"""Count test points in shell scripts under a test directory.

Heuristics, in order:
1. Count `# Test N:` markers inside a script.
2. Count `run_test ...` invocations inside a script.
3. Count `assert_test_num N ...` markers inside a script.
4. Fallback: treat the shell script itself as one test point.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


TEST_MARKER_RE = re.compile(r"^\s*#\s*Test\s+\d+\s*:", re.MULTILINE)
RUN_TEST_CALL_RE = re.compile(r"^\s*run_test\s+", re.MULTILINE)
ASSERT_TEST_NUM_RE = re.compile(r"^\s*assert_test_num\s+\d+\b", re.MULTILINE)
RUN_OK_CALL_RE = re.compile(r"^\s*if\s+run_ok_\s+", re.MULTILINE)
TOP_LEVEL_TEST_LABEL_RE = re.compile(
    r'^echo\s+["\'][A-Za-z0-9_.-]+:\s+[^"\']+["\']\s*$',
    re.MULTILINE,
)
ARRAY_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)=\((.*)\)\s*$",
    re.MULTILINE,
)
ARRAY_LOOP_RE = re.compile(
    r'^\s*for\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s+["\']?\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}["\']?\s*;\s*do\s*$'
)


@dataclass(frozen=True)
class FileCount:
    path: Path
    count: int
    rule: str


def parse_array_lengths(content: str) -> dict[str, int]:
    lengths: dict[str, int] = {}

    for match in ARRAY_ASSIGN_RE.finditer(content):
        name = match.group(1)
        raw_items = match.group(2).strip()
        if not raw_items:
            lengths[name] = 0
            continue

        try:
            items = shlex.split(raw_items, posix=True)
        except ValueError:
            continue

        lengths[name] = len(items)

    return lengths


def count_cartesian_loop_points(content: str) -> int:
    array_lengths = parse_array_lengths(content)
    if not array_lengths:
        return 0

    product = 1
    matched_loops = 0

    for line in content.splitlines():
        match = ARRAY_LOOP_RE.match(line)
        if not match:
            continue

        array_name = match.group(1)
        array_len = array_lengths.get(array_name)
        if not array_len:
            continue

        product *= array_len
        matched_loops += 1

    return product if matched_loops >= 2 else 0


def count_points(script_path: Path) -> FileCount:
    content = script_path.read_text(encoding="utf-8", errors="replace")

    test_markers = len(TEST_MARKER_RE.findall(content))
    if test_markers:
        return FileCount(script_path, test_markers, "# Test N:")

    run_test_calls = len(RUN_TEST_CALL_RE.findall(content))
    if run_test_calls:
        return FileCount(script_path, run_test_calls, "run_test")

    assert_test_num_calls = len(ASSERT_TEST_NUM_RE.findall(content))
    if assert_test_num_calls:
        return FileCount(script_path, assert_test_num_calls, "assert_test_num")

    run_ok_calls = len(RUN_OK_CALL_RE.findall(content))
    top_level_labels = len(TOP_LEVEL_TEST_LABEL_RE.findall(content))
    cartesian_loop_points = count_cartesian_loop_points(content)

    inferred_count = max(run_ok_calls, top_level_labels, cartesian_loop_points)
    if inferred_count:
        if inferred_count == cartesian_loop_points:
            return FileCount(script_path, inferred_count, "cartesian array loops")
        if inferred_count == top_level_labels:
            return FileCount(script_path, inferred_count, "top-level test labels")
        return FileCount(script_path, inferred_count, "run_ok_")

    return FileCount(script_path, 1, "single-script fallback")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count test points in .sh scripts under a test directory."
    )
    parser.add_argument("test_dir", help="Path to the test directory")
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print per-script counts in addition to the total",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    test_dir = Path(args.test_dir).expanduser().resolve()

    if not test_dir.exists():
        print(f"error: test directory does not exist: {test_dir}", file=sys.stderr)
        return 1

    if not test_dir.is_dir():
        print(f"error: path is not a directory: {test_dir}", file=sys.stderr)
        return 1

    scripts = sorted(path for path in test_dir.rglob("*.sh") if path.is_file())
    if not scripts:
        print(f"test_dir: {test_dir}")
        print("shell_scripts: 0")
        print("total_test_points: 0")
        return 0

    file_counts = [count_points(script) for script in scripts]
    total = sum(item.count for item in file_counts)

    print(f"test_dir: {test_dir}")
    print(f"shell_scripts: {len(file_counts)}")
    print(f"total_test_points: {total}")

    if args.details:
        print("")
        for item in file_counts:
            relative_path = item.path.relative_to(test_dir)
            print(f"{relative_path}\t{item.count}\t{item.rule}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
