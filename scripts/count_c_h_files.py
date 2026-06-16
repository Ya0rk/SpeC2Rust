#!/usr/bin/env python3
"""Count .c and .h files under a directory recursively."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def is_under_test_dir(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts[:-1]
    return any(part.lower() == "test" for part in relative_parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count .c and .h files under a directory recursively."
    )
    parser.add_argument("target_dir", help="Path to the project directory")
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print each matched file path",
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

    c_files = sorted(
        path
        for path in target_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() == ".c"
        and not is_under_test_dir(path, target_dir)
    )
    h_files = sorted(
        path
        for path in target_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() == ".h"
        and not is_under_test_dir(path, target_dir)
    )

    print(f"target_dir: {target_dir}")
    print(f"c_files: {len(c_files)}")
    print(f"h_files: {len(h_files)}")
    print(f"total: {len(c_files) + len(h_files)}")

    if args.details:
        print("")
        for path in c_files:
            print(f"{path.relative_to(target_dir)}\t.c")
        for path in h_files:
            print(f"{path.relative_to(target_dir)}\t.h")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
