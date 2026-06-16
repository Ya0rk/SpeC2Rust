#!/usr/bin/env python3
"""
Compare Rust translations against a C project with a softer, more idiomaticity-aware metric.

This script does NOT require one-to-one C function preservation. Instead it scores:
  1. intent_token_coverage
     Whether salient project/domain concepts from the C code still appear in Rust.
  2. behavior_marker_coverage
     Whether key user-visible markers such as CLI flags, env vars, paths, and message
     vocabulary are preserved.
  3. concept_coverage
     Whether C file/type/macro concepts are represented in Rust modules/types/identifiers.
  4. rust_design_score
     Whether the Rust code exhibits non-trivial modularity and abstraction instead of
     collapsing everything into one large file of free functions.

Composite metric:

    Translation Intent Index (TII) = 100 * (
        0.35 * intent_token_coverage +
        0.25 * behavior_marker_coverage +
        0.20 * concept_coverage +
        0.20 * rust_design_score
    )

Example:
    python scripts/translation_fidelity.py ^
      --c-project datasets/which ^
      --rust-project output/res2/which/which-rust ^
      --rust-project output/smart/which
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
CAMEL_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")
C_STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"', re.DOTALL)
RUST_RAW_STRING_RE = re.compile(r'r(#+)?"(.*?)"\1', re.DOTALL)
LONG_FLAG_RE = re.compile(r"^--[A-Za-z0-9][A-Za-z0-9_-]*$")
SHORT_FLAG_RE = re.compile(r"^-[A-Za-z0-9]$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
TOP_C_TYPE_RE = re.compile(r"\b(?:struct|enum|union)\s+([A-Za-z_][A-Za-z0-9_]*)")
TYPEDEF_RE = re.compile(r"\btypedef\b[^;]*\b([A-Za-z_][A-Za-z0-9_]*)\s*;")
DEFINE_RE = re.compile(r"(?m)^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)")
RUST_TYPE_RE = re.compile(r"(?m)^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)")
RUST_IMPL_RE = re.compile(r"(?m)^\s*impl(?:<[^>]+>)?\s+([A-Za-z_][A-Za-z0-9_:<>]*)")
RUST_FN_RE = re.compile(
    r'(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+"[^"]+"\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\('
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")


C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double",
    "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long",
    "register", "restrict", "return", "short", "signed", "sizeof", "static", "struct",
    "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "include",
    "define", "ifdef", "ifndef", "endif", "elif", "pragma", "NULL",
}
RUST_KEYWORDS = {
    "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum",
    "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
    "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
    "trait", "true", "type", "unsafe", "use", "where", "while",
}
STOP_TOKENS = {
    "argc", "argv", "args", "arg", "main", "file", "files", "line", "lines", "value",
    "values", "name", "names", "path", "paths", "data", "info", "result", "results",
    "output", "input", "string", "strings", "state", "mode", "default", "error", "errors",
    "option", "options", "write", "read", "print", "buffer", "size", "count", "index",
    "start", "end", "new", "old", "tmp", "temp",
}
IGNORED_FILE_STEMS = {"config", "sys", "main"}
RUST_SOURCE_EXTS = {".rs"}
C_SOURCE_EXTS = {".c", ".h"}


@dataclass(frozen=True)
class ProjectMetrics:
    rust_project: str
    summary: dict[str, float | int]
    details: dict[str, object]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def split_identifier(name: str) -> list[str]:
    stripped = name.strip("_")
    if not stripped:
        return []
    parts: list[str] = []
    for chunk in stripped.split("_"):
        if not chunk:
            continue
        for part in CAMEL_SPLIT_RE.sub("_", chunk).split("_"):
            part = part.lower()
            if part:
                parts.append(part)
    return parts


def normalize_token(token: str) -> str:
    token = token.strip()
    if token.startswith("rust_"):
        token = token[5:]
    return token.lower()


def normalize_literal(literal: str) -> str:
    return " ".join(literal.replace("\r", " ").replace("\n", " ").split())


def normalize_path_stem(path: Path) -> str:
    return path.stem.lower()


def iter_source_files(root: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions and "target" not in path.parts
    )


def strip_comments_and_strings(text: str) -> str:
    result: list[str] = []
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < length else ""

        if ch == "/" and nxt == "/":
            i += 2
            while i < length and text[i] != "\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            depth = 1
            while i < length and depth > 0:
                if i + 1 < length and text[i] == "/" and text[i + 1] == "*":
                    depth += 1
                    i += 2
                    continue
                if i + 1 < length and text[i] == "*" and text[i + 1] == "/":
                    depth -= 1
                    i += 2
                    continue
                if text[i] == "\n":
                    result.append("\n")
                i += 1
            continue

        if ch == '"':
            result.append(" ")
            i += 1
            while i < length:
                if text[i] == "\\" and i + 1 < length:
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                if text[i] == "\n":
                    result.append("\n")
                i += 1
            continue

        if ch == "'" and i + 2 < length:
            # Handle character literals but do not eat Rust lifetimes such as `'static`.
            if text[i + 1] == "\\" and i + 3 < length and text[i + 3] == "'":
                result.append(" ")
                i += 4
                continue
            if text[i + 2] == "'":
                result.append(" ")
                i += 3
                continue

        if ch == "r":
            raw_match = RUST_RAW_STRING_RE.match(text, i)
            if raw_match:
                result.append(" ")
                i = raw_match.end()
                continue

        result.append(ch)
        i += 1

    return "".join(result)


def extract_string_literals(text: str) -> list[str]:
    strings = [normalize_literal(match.group(1)) for match in C_STRING_RE.finditer(text)]
    for match in RUST_RAW_STRING_RE.finditer(text):
        strings.append(normalize_literal(match.group(2)))
    return [value for value in strings if value]


def extract_identifier_counter(text: str, keywords: set[str]) -> Counter[str]:
    sanitized = strip_comments_and_strings(text)
    counter: Counter[str] = Counter()
    for ident in IDENT_RE.findall(sanitized):
        if ident in keywords:
            continue
        for token in split_identifier(normalize_token(ident)):
            if token in STOP_TOKENS or token in keywords:
                continue
            if len(token) < 3:
                continue
            counter[token] += 1
    return counter


def extract_message_words(strings: list[str]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for literal in strings:
        if LONG_FLAG_RE.match(literal) or SHORT_FLAG_RE.match(literal) or ENV_RE.match(literal):
            continue
        for word in WORD_RE.findall(literal):
            for token in split_identifier(word):
                token = token.lower()
                if token in STOP_TOKENS or len(token) < 4:
                    continue
                counter[token] += 1
    return counter


def weighted_coverage(weights: dict[str, float], observed: set[str]) -> tuple[float, list[dict[str, float | str]]]:
    total = sum(weights.values())
    matched = sum(weight for token, weight in weights.items() if token in observed)
    missing = [
        {"token": token, "weight": round(weight, 4)}
        for token, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True)
        if token not in observed
    ]
    return (0.0 if total == 0 else matched / total), missing


def extract_c_project_features(c_project: Path) -> dict[str, object]:
    files = iter_source_files(c_project, C_SOURCE_EXTS)
    identifier_counter: Counter[str] = Counter()
    string_literals: list[str] = []
    file_concepts: Counter[str] = Counter()
    type_concepts: Counter[str] = Counter()
    macro_concepts: Counter[str] = Counter()

    for path in files:
        stem = normalize_path_stem(path)
        if stem not in IGNORED_FILE_STEMS and len(stem) >= 3:
            for token in split_identifier(stem):
                if len(token) >= 3:
                    file_concepts[token] += 1

        text = path.read_text(encoding="utf-8", errors="ignore")
        identifier_counter.update(extract_identifier_counter(text, C_KEYWORDS))
        string_literals.extend(extract_string_literals(text))

        sanitized = strip_comments_and_strings(text)
        for match in TOP_C_TYPE_RE.finditer(sanitized):
            for token in split_identifier(match.group(1)):
                if len(token) >= 3:
                    type_concepts[token] += 1
        for match in TYPEDEF_RE.finditer(sanitized):
            for token in split_identifier(match.group(1)):
                if len(token) >= 3:
                    type_concepts[token] += 1
        for match in DEFINE_RE.finditer(text):
            for token in split_identifier(match.group(1)):
                if len(token) >= 3:
                    macro_concepts[token] += 1

    flags = {value for value in string_literals if LONG_FLAG_RE.match(value) or SHORT_FLAG_RE.match(value)}
    env_vars = {value for value in string_literals if ENV_RE.match(value)}
    path_markers = {
        value for value in string_literals
        if "/" in value or value.startswith(".") or value.endswith(".c") or value.endswith(".h")
    }
    message_words = extract_message_words(string_literals)

    intent_weights = {
        token: 1.0 + math.log1p(count)
        for token, count in identifier_counter.items()
    }
    concept_weights: dict[str, float] = {}
    for token, count in file_concepts.items():
        concept_weights[token] = concept_weights.get(token, 0.0) + 1.5 + 0.5 * math.log1p(count)
    for token, count in type_concepts.items():
        concept_weights[token] = concept_weights.get(token, 0.0) + 1.6 + 0.5 * math.log1p(count)
    for token, count in macro_concepts.items():
        concept_weights[token] = concept_weights.get(token, 0.0) + 1.2 + 0.4 * math.log1p(count)

    behavior_weights: dict[str, float] = {}
    for value in flags:
        behavior_weights[value] = behavior_weights.get(value, 0.0) + 2.2
    for value in env_vars:
        behavior_weights[value] = behavior_weights.get(value, 0.0) + 1.8
    for value in path_markers:
        behavior_weights[value] = behavior_weights.get(value, 0.0) + 1.3
    for token, count in message_words.items():
        behavior_weights[token] = behavior_weights.get(token, 0.0) + 0.5 + 0.3 * math.log1p(count)

    return {
        "c_files": [str(path) for path in files],
        "intent_weights": intent_weights,
        "concept_weights": concept_weights,
        "behavior_weights": behavior_weights,
        "flags": sorted(flags),
        "env_vars": sorted(env_vars),
        "path_markers": sorted(path_markers),
    }


def extract_rust_project_features(rust_project: Path) -> dict[str, object]:
    src_root = rust_project / "src" if (rust_project / "src").is_dir() else rust_project
    files = iter_source_files(src_root, RUST_SOURCE_EXTS)
    identifier_counter: Counter[str] = Counter()
    string_literals: list[str] = []
    concept_counter: Counter[str] = Counter()
    total_loc = 0
    largest_file_loc = 0
    struct_count = 0
    enum_count = 0
    trait_count = 0
    impl_count = 0
    fn_count = 0
    result_mentions = 0
    option_mentions = 0
    match_mentions = 0

    for path in files:
        stem = normalize_path_stem(path)
        if len(stem) >= 3:
            for token in split_identifier(stem):
                if len(token) >= 3:
                    concept_counter[token] += 1

        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line for line in text.splitlines() if line.strip()]
        file_loc = len(lines)
        total_loc += file_loc
        largest_file_loc = max(largest_file_loc, file_loc)

        identifier_counter.update(extract_identifier_counter(text, RUST_KEYWORDS))
        string_literals.extend(extract_string_literals(text))

        sanitized = strip_comments_and_strings(text)
        types = RUST_TYPE_RE.findall(sanitized)
        impls = RUST_IMPL_RE.findall(sanitized)
        fns = RUST_FN_RE.findall(sanitized)
        struct_count += sum(1 for match in re.finditer(r"(?m)^\s*(?:pub\s+)?struct\b", sanitized))
        enum_count += sum(1 for match in re.finditer(r"(?m)^\s*(?:pub\s+)?enum\b", sanitized))
        trait_count += sum(1 for match in re.finditer(r"(?m)^\s*(?:pub\s+)?trait\b", sanitized))
        impl_count += len(impls)
        fn_count += len(fns)
        result_mentions += text.count("Result<") + text.count("-> Result")
        option_mentions += text.count("Option<") + text.count("-> Option")
        match_mentions += len(re.findall(r"\bmatch\b", sanitized))

        for name in types:
            for token in split_identifier(name):
                if len(token) >= 3:
                    concept_counter[token] += 2
        for name in impls:
            for token in split_identifier(name.replace("::", "_")):
                if len(token) >= 3:
                    concept_counter[token] += 2
        for name in fns:
            for token in split_identifier(normalize_token(name)):
                if len(token) >= 3 and token not in STOP_TOKENS:
                    concept_counter[token] += 1

    flag_literals = {value for value in string_literals if LONG_FLAG_RE.match(value) or SHORT_FLAG_RE.match(value)}
    env_literals = {value for value in string_literals if ENV_RE.match(value)}
    path_literals = {
        value for value in string_literals
        if "/" in value or value.startswith(".") or value.endswith(".rs")
    }
    message_words = set(extract_message_words(string_literals).keys())

    rust_tokens = set(identifier_counter.keys())
    rust_behavior_tokens = set(flag_literals) | set(env_literals) | set(path_literals) | message_words
    rust_concepts = set(concept_counter.keys()) | rust_tokens

    largest_share = 1.0 if total_loc == 0 else largest_file_loc / total_loc
    modularity_score = 1.0 - clamp((largest_share - 0.35) / 0.55)
    abstraction_score = clamp((struct_count + enum_count + trait_count + impl_count) / max(1.0, fn_count / 4.0))
    idiom_hints = [
        1.0 if impl_count > 0 else 0.0,
        1.0 if (struct_count + enum_count) > 0 else 0.0,
        1.0 if result_mentions > 0 or option_mentions > 0 else 0.0,
        1.0 if match_mentions > 0 else 0.0,
    ]
    idiom_score = sum(idiom_hints) / len(idiom_hints)
    rust_design_score = (
        0.65 * modularity_score +
        0.20 * abstraction_score +
        0.15 * idiom_score
    )

    return {
        "src_root": str(src_root),
        "rust_tokens": rust_tokens,
        "rust_behavior_tokens": rust_behavior_tokens,
        "rust_concepts": rust_concepts,
        "rust_design_score": rust_design_score,
        "rust_stats": {
            "rust_file_count": len(files),
            "fn_count": fn_count,
            "struct_count": struct_count,
            "enum_count": enum_count,
            "trait_count": trait_count,
            "impl_count": impl_count,
            "total_loc": total_loc,
            "largest_file_loc": largest_file_loc,
            "largest_file_share": round(largest_share, 6),
            "modularity_score": round(modularity_score, 6),
            "abstraction_score": round(abstraction_score, 6),
            "idiom_score": round(idiom_score, 6),
        },
    }


def build_project_report(c_features: dict[str, object], rust_project: Path) -> ProjectMetrics:
    rust_features = extract_rust_project_features(rust_project)

    intent_coverage, missing_intent = weighted_coverage(
        c_features["intent_weights"], rust_features["rust_tokens"]
    )
    behavior_coverage, missing_behavior = weighted_coverage(
        c_features["behavior_weights"], rust_features["rust_behavior_tokens"]
    )
    concept_coverage, missing_concepts = weighted_coverage(
        c_features["concept_weights"], rust_features["rust_concepts"]
    )
    rust_design_score = rust_features["rust_design_score"]

    total_score = 100.0 * (
        0.35 * intent_coverage +
        0.25 * behavior_coverage +
        0.20 * concept_coverage +
        0.20 * rust_design_score
    )

    summary = {
        "translation_intent_index": round(total_score, 4),
        "intent_token_coverage": round(intent_coverage, 6),
        "behavior_marker_coverage": round(behavior_coverage, 6),
        "concept_coverage": round(concept_coverage, 6),
        "rust_design_score": round(rust_design_score, 6),
        **rust_features["rust_stats"],
    }

    details = {
        "metric": "translation_intent_index",
        "metric_version": "tii_v2",
        "formula": {
            "intent_token_coverage": 0.35,
            "behavior_marker_coverage": 0.25,
            "concept_coverage": 0.20,
            "rust_design_score": 0.20,
        },
        "top_missing_intent_tokens": missing_intent[:30],
        "top_missing_behavior_markers": missing_behavior[:30],
        "top_missing_concepts": missing_concepts[:30],
        "c_feature_overview": {
            "flag_count": len(c_features["flags"]),
            "env_var_count": len(c_features["env_vars"]),
            "path_marker_count": len(c_features["path_markers"]),
        },
    }

    return ProjectMetrics(
        rust_project=str(rust_project.resolve()),
        summary=summary,
        details=details,
    )


def print_summary_table(reports: list[ProjectMetrics]) -> None:
    print("Translation Intent Index")
    print("-" * 132)
    print(
        f"{'Rust project':<45} {'TII':>8} {'Intent':>9} {'Behavior':>10} "
        f"{'Concept':>9} {'Design':>9} {'Files':>7} {'Fns':>7} {'MaxShare':>9}"
    )
    print("-" * 132)
    for report in reports:
        summary = report.summary
        print(
            f"{report.rust_project:<45} "
            f"{summary['translation_intent_index']:>8.2f} "
            f"{summary['intent_token_coverage']:>9.4f} "
            f"{summary['behavior_marker_coverage']:>10.4f} "
            f"{summary['concept_coverage']:>9.4f} "
            f"{summary['rust_design_score']:>9.4f} "
            f"{summary['rust_file_count']:>7} "
            f"{summary['fn_count']:>7} "
            f"{summary['largest_file_share']:>9.4f}"
        )


def report_payload(c_project: Path, report: ProjectMetrics) -> dict[str, object]:
    return {
        "c_project": str(c_project.resolve()),
        "rust_project": report.rust_project,
        "summary": report.summary,
        "details": report.details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare one C project against one or more Rust translations."
    )
    parser.add_argument("--c-project", required=True, help="Path to the original C project")
    parser.add_argument(
        "--rust-project",
        action="append",
        required=True,
        help="Path to a Rust project. Repeat this flag to compare multiple Rust projects.",
    )
    parser.add_argument(
        "--output-json",
        help="Optional JSON output path. Stores a single report or a list of reports.",
    )
    args = parser.parse_args()

    c_project = Path(args.c_project).resolve()
    if not c_project.is_dir():
        raise SystemExit(f"C project not found: {c_project}")

    c_features = extract_c_project_features(c_project)
    reports = [
        build_project_report(c_features, Path(project).resolve())
        for project in args.rust_project
    ]
    print_summary_table(reports)

    if args.output_json:
        payload = [report_payload(c_project, report) for report in reports]
        if len(payload) == 1:
            payload = payload[0]
        output_path = Path(args.output_json).resolve()
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved JSON report to: {output_path}")


if __name__ == "__main__":
    main()
