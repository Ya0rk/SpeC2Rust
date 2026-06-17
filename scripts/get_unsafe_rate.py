from pathlib import Path
import argparse
import bisect
import json
import re
import sys


IDENTIFIER_CHAR_RE = re.compile(r"[A-Za-z0-9_]")
UNSAFE_RE = re.compile(r"\bunsafe\b")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_path")
    return parser.parse_args()


def is_identifier_char(char):
    return bool(char) and IDENTIFIER_CHAR_RE.fullmatch(char) is not None


def is_keyword_at(source, index, keyword):
    end = index + len(keyword)
    if not source.startswith(keyword, index):
        return False
    before = source[index - 1] if index > 0 else ""
    after = source[end] if end < len(source) else ""
    return not is_identifier_char(before) and not is_identifier_char(after)


def detect_raw_string_start(source, index):
    if source[index] == "r":
        cursor = index + 1
    elif source[index] == "b" and index + 1 < len(source) and source[index + 1] == "r":
        cursor = index + 2
    else:
        return None

    hash_count = 0
    while cursor < len(source) and source[cursor] == "#":
        hash_count += 1
        cursor += 1

    if cursor < len(source) and source[cursor] == '"':
        return cursor - index + 1, hash_count
    return None


def looks_like_char_literal(source, index):
    if source[index] != "'":
        return False
    before = source[index - 1] if index > 0 else ""
    if is_identifier_char(before):
        return False

    cursor = index + 1
    if cursor >= len(source) or source[cursor] == "\n":
        return False

    while cursor < len(source):
        if source[cursor] == "\n":
            return False
        if source[cursor] == "\\":
            cursor += 2
            continue
        if source[cursor] == "'":
            return True
        cursor += 1
    return False


def sanitize_rust_source(source):
    output = []
    index = 0
    block_comment_depth = 0

    while index < len(source):
        char = source[index]

        if block_comment_depth > 0:
            if source.startswith("/*", index):
                output.extend([" ", " "])
                block_comment_depth += 1
                index += 2
                continue
            if source.startswith("*/", index):
                output.extend([" ", " "])
                block_comment_depth -= 1
                index += 2
                continue
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if source.startswith("//", index):
            output.extend([" ", " "])
            index += 2
            while index < len(source) and source[index] != "\n":
                output.append(" ")
                index += 1
            continue

        if source.startswith("/*", index):
            output.extend([" ", " "])
            block_comment_depth = 1
            index += 2
            continue

        raw_string = detect_raw_string_start(source, index)
        if raw_string is not None:
            prefix_length, hash_count = raw_string
            output.extend(" " for _ in range(prefix_length))
            index += prefix_length
            terminator = '"' + ('#' * hash_count)
            while index < len(source):
                if source.startswith(terminator, index):
                    output.extend(" " for _ in range(len(terminator)))
                    index += len(terminator)
                    break
                output.append("\n" if source[index] == "\n" else " ")
                index += 1
            continue

        if char == '"':
            output.append(" ")
            index += 1
            while index < len(source):
                current = source[index]
                if current == "\\" and index + 1 < len(source):
                    output.append(" ")
                    output.append("\n" if source[index + 1] == "\n" else " ")
                    index += 2
                    continue
                output.append("\n" if current == "\n" else " ")
                index += 1
                if current == '"':
                    break
            continue

        if looks_like_char_literal(source, index):
            output.append(" ")
            index += 1
            while index < len(source):
                current = source[index]
                if current == "\\" and index + 1 < len(source):
                    output.append(" ")
                    output.append("\n" if source[index + 1] == "\n" else " ")
                    index += 2
                    continue
                output.append("\n" if current == "\n" else " ")
                index += 1
                if current == "'":
                    break
            continue

        output.append(char)
        index += 1

    return "".join(output)


def skip_whitespace(source, index):
    while index < len(source) and source[index].isspace():
        index += 1
    return index


def is_unsafe_function_signature(source, index):
    cursor = skip_whitespace(source, index)

    while True:
        if is_keyword_at(source, cursor, "extern"):
            cursor = skip_whitespace(source, cursor + len("extern"))
            continue
        if is_keyword_at(source, cursor, "async"):
            cursor = skip_whitespace(source, cursor + len("async"))
            continue
        if is_keyword_at(source, cursor, "const"):
            cursor = skip_whitespace(source, cursor + len("const"))
            continue
        if is_keyword_at(source, cursor, "default"):
            cursor = skip_whitespace(source, cursor + len("default"))
            continue
        break

    return is_keyword_at(source, cursor, "fn")


def find_matching_brace(source, open_brace_index):
    depth = 1
    cursor = open_brace_index + 1
    while cursor < len(source):
        if source[cursor] == '{':
            depth += 1
        elif source[cursor] == '}':
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def find_construct_body_open_brace(source, index):
    paren_depth = 0
    bracket_depth = 0
    cursor = index

    while cursor < len(source):
        char = source[cursor]

        if char == '(':
            paren_depth += 1
        elif char == ')' and paren_depth > 0:
            paren_depth -= 1
        elif char == '[':
            bracket_depth += 1
        elif char == ']' and bracket_depth > 0:
            bracket_depth -= 1
        elif paren_depth == 0 and bracket_depth == 0:
            if char == '{':
                return cursor
            if char == ';':
                return None

        cursor += 1

    return None


def is_unsafe_braced_construct(source, index):
    return (
        is_unsafe_function_signature(source, index)
        or is_keyword_at(source, skip_whitespace(source, index), "impl")
        or is_keyword_at(source, skip_whitespace(source, index), "trait")
        or is_keyword_at(source, skip_whitespace(source, index), "extern")
    )


def select_outer_intervals(intervals):
    outer_intervals = []
    for interval in sorted(intervals, key=lambda item: (item[0], -item[1])):
        if outer_intervals and interval[0] <= outer_intervals[-1][1]:
            if interval[1] > outer_intervals[-1][1]:
                outer_intervals[-1] = (
                    outer_intervals[-1][0],
                    interval[1],
                    outer_intervals[-1][2],
                )
            continue
        outer_intervals.append(interval)
    return outer_intervals


def collect_unsafe_intervals(source):
    intervals = []
    unsafe_function_count = 0

    for match in UNSAFE_RE.finditer(source):
        start = match.start()
        cursor = skip_whitespace(source, match.end())

        if is_unsafe_function_signature(source, cursor):
            unsafe_function_count += 1

        if cursor < len(source) and source[cursor] == '{':
            open_brace = cursor
        elif is_unsafe_braced_construct(source, cursor):
            open_brace = find_construct_body_open_brace(source, cursor)
        else:
            continue

        if open_brace is None:
            continue

        close_brace = find_matching_brace(source, open_brace)
        if close_brace is None:
            continue

        kind = "block" if cursor < len(source) and source[cursor] == "{" else "construct"
        intervals.append((start, close_brace, kind))
    return select_outer_intervals(intervals), unsafe_function_count


def build_line_starts(source):
    starts = [0]
    for index, char in enumerate(source):
        if char == '\n':
            starts.append(index + 1)
    return starts


def index_to_line(line_starts, index):
    return bisect.bisect_right(line_starts, index)


def analyze_rust_file(file_path):
    source = file_path.read_text()
    total_lines = len(source.splitlines())
    if total_lines == 0:
        return {
            "unsafe_lines": 0,
            "all_lines": 0,
            "unsafe_blocks": 0,
            "unsafe_functions": 0,
        }

    sanitized = sanitize_rust_source(source)
    intervals, unsafe_function_count = collect_unsafe_intervals(sanitized)
    line_starts = build_line_starts(source)
    unsafe_lines = set()

    for start_index, end_index, _kind in intervals:
        start_line = index_to_line(line_starts, start_index)
        end_line = index_to_line(line_starts, end_index)
        unsafe_lines.update(range(start_line, end_line + 1))

    return {
        "unsafe_lines": len(unsafe_lines),
        "all_lines": total_lines,
        "unsafe_blocks": sum(1 for _start, _end, kind in intervals if kind == "block"),
        "unsafe_functions": unsafe_function_count,
    }


def analyze_rust_directory(directory_path):
    rust_files = sorted(directory_path.rglob("*.rs"))
    if len(rust_files) == 0:
        print(f"No Rust files found under: {directory_path}", file=sys.stderr)
        sys.exit(1)

    totals = {
        "path": str(directory_path),
        "unsafe_lines": 0,
        "all_lines": 0,
        "unsafe_blocks": 0,
        "unsafe_functions": 0,
    }

    for rust_file in rust_files:
        file_metrics = analyze_rust_file(rust_file)
        totals["unsafe_lines"] += file_metrics["unsafe_lines"]
        totals["all_lines"] += file_metrics["all_lines"]
        totals["unsafe_blocks"] += file_metrics["unsafe_blocks"]
        totals["unsafe_functions"] += file_metrics["unsafe_functions"]

    totals["unsafe_rate"] = 0.0 if totals["all_lines"] == 0 else totals["unsafe_lines"] / totals["all_lines"]
    return totals


def print_metrics(label, metrics):
    print(f"[{label}]")
    print(f"path: {metrics['path']}")
    print(f"unsafe lines: {metrics['unsafe_lines']}")
    print(f"all lines: {metrics['all_lines']}")
    print(f"unsafe blocks: {metrics['unsafe_blocks']}")
    print(f"unsafe functions: {metrics['unsafe_functions']}")
    print(f"unsafe rate: {metrics['unsafe_rate']:.6f}")


def main():
    args = parse_args()
    root_dir = Path(__file__).resolve().parent
    project_root = Path(args.project_path)
    if not project_root.exists():
        project_root = root_dir / "src" / args.project_path
    # rust_dir = project_root 
    rust_dir = project_root / "rust"
    # rust_wip_dir = project_root 
    rust_wip_dir = project_root / "rust_WIP"
    output_path = project_root / "unsafe_metrics.json"

    if not project_root.exists():
        print(f"Project directory not found: {project_root}", file=sys.stderr)
        sys.exit(1)

    if not rust_dir.exists():
        print(f"Rust directory not found: {rust_dir}", file=sys.stderr)
        sys.exit(1)

    if not rust_wip_dir.exists():
        print(f"Rust WIP directory not found: {rust_wip_dir}", file=sys.stderr)
        sys.exit(1)

    rust_metrics = analyze_rust_directory(rust_dir)
    rust_wip_metrics = analyze_rust_directory(rust_wip_dir)

    output = {
        "project": args.project_path,
        "json_path": str(output_path),
        "rust": rust_metrics,
        "rust_WIP": rust_wip_metrics,
    }

    output_path.write_text(json.dumps(output, indent=4))

    print(f"project: {args.project_path}")
    print_metrics("rust", rust_metrics)
    print()
    print_metrics("rust_WIP", rust_wip_metrics)
    print()
    print(f"json saved to: {output_path}")


if __name__ == "__main__":
    main()
