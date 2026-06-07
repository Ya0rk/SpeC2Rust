#!/usr/bin/env python3
"""Extract and sum input/output token counts from a token log text file."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s*$")
NUMBER_PREFIX_RE = re.compile(r"^\s*([\d,]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract input or output token counts from a token log text file."
    )
    parser.add_argument("txt_path", help="Path to the txt file to parse.")
    parser.add_argument(
        "token_type",
        help="Which token type to extract: input or output.",
    )
    return parser.parse_args()


def normalize_token_type(raw_value: str) -> str:
    value = raw_value.strip().lower()
    alias_map = {
        "input": "input",
        "in": "input",
        "i": "input",
        "输入": "input",
        "output": "output",
        "out": "output",
        "o": "output",
        "输出": "output",
    }
    if value not in alias_map:
        raise ValueError(
            "token_type must be one of: input, in, i, 输入, output, out, o, 输出"
        )
    return alias_map[value]


def extract_number(line: str) -> int:
    match = NUMBER_PREFIX_RE.match(line)
    if not match:
        raise ValueError(f"no leading number found in line: {line!r}")
    return int(match.group(1).replace(",", ""))


def parse_records(lines: list[str]) -> list[dict[str, int | str]]:
    records: list[dict[str, int | str]] = []
    total_lines = len(lines)
    index = 0

    while index < total_lines:
        current_line = lines[index].strip()
        if not TIMESTAMP_RE.match(current_line):
            index += 1
            continue

        timestamp = current_line
        input_index = index + 1
        if input_index >= total_lines:
            raise ValueError(f"timestamp {timestamp} is missing an input token line")

        input_line = lines[input_index].strip()
        input_tokens = extract_number(input_line)

        output_tokens: int | None = None
        search_index = input_index + 1
        while search_index < total_lines:
            candidate = lines[search_index].strip()
            if TIMESTAMP_RE.match(candidate):
                break
            if NUMBER_PREFIX_RE.match(candidate):
                output_tokens = extract_number(candidate)
                break
            search_index += 1

        if output_tokens is None:
            raise ValueError(f"timestamp {timestamp} is missing an output token line")

        records.append(
            {
                "timestamp": timestamp,
                "input": input_tokens,
                "output": output_tokens,
            }
        )

        index = search_index if search_index > index else index + 1

    return records


def main() -> int:
    args = parse_args()
    txt_path = Path(args.txt_path)

    try:
        token_type = normalize_token_type(args.token_type)
        lines = txt_path.read_text(encoding="utf-8").splitlines()
        records = parse_records(lines)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("No token records found.")
        return 1

    total = 0
    for record in records:
        value = int(record[token_type])
        total += value
        print(f"{record['timestamp']}\t{value}")

    print(f"total_{token_type}_tokens\t{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
