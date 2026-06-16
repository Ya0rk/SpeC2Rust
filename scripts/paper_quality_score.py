#!/usr/bin/env python3
"""
Paper-inspired automatic quality scoring for Rust translations.

This script borrows the quality dimensions used in:
  "Code Quality Analysis of Translations from C to Rust" (arXiv:2602.00840)

Differences from the paper:
  - No manual analysis
  - No LLM review
  - Numeric weights are an implementation choice inspired by the paper's
    internal-vs-external quality split, because the paper does not define a
    single automatic weighted score

Evidence sources:
  1. clippy_report.json
  2. unsafe_metrics.json
  3. raw_ptr_stats.json
  4. Rust source scan
  5. Optional: c-project-based translation intent score from translation_fidelity.py

The script is pure Python stdlib and is intended to run on both Windows and Linux.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUST_EXTENSIONS = {".rs"}
IGNORED_DIRS = {"target", ".git", ".idea", ".vscode", "__pycache__"}

PUBLIC_ITEM_RE = re.compile(
    r"(?m)^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:unsafe\s+)?"
    r"(?:fn|struct|enum|trait|mod|const|static|type)\b"
)
FN_HEADER_RE = re.compile(
    r'(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+"[^"]+"\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\('
)
ALLOW_ATTR_RE = re.compile(r"#\s*\[\s*allow\s*\(")
EXPECT_ATTR_RE = re.compile(r"#\s*\[\s*expect\s*\(")
UNSAFE_IMPL_SEND_SYNC_RE = re.compile(r"\bunsafe\s+impl\b[^{;]*\b(?:Send|Sync)\b")
STATIC_MUT_RE = re.compile(r"(?m)^\s*static\s+mut\b")
TRANSMUTE_RE = re.compile(r"\btransmute(?:_copy)?\s*[\!<(]")
FROM_RAW_PARTS_RE = re.compile(r"\bfrom_raw_parts(?:_mut)?\s*\(")
PANIC_RE = re.compile(r"\bpanic!\s*\(")
UNREACHABLE_RE = re.compile(r"\bunreachable!\s*\(")
TODO_RE = re.compile(r"\btodo!\s*\(")
UNIMPLEMENTED_RE = re.compile(r"\bunimplemented!\s*\(")
UNWRAP_RE = re.compile(r"\.unwrap\s*\(")
EXPECT_RE = re.compile(r"\.expect\s*\(")
DBG_RE = re.compile(r"\bdbg!\s*\(")
UNIX_API_RE = re.compile(r"\bstd::os::unix\b|\blibc::\b|::libc\b")
PROCFS_RE = re.compile(r"/proc/[A-Za-z0-9_/\-]+")
AS_CAST_RE = re.compile(r"\bas\s+[A-Za-z_][A-Za-z0-9_:<>]*")
DOC_LINE_RE = re.compile(r"^\s*(///|//!|/\*\*)")
LONG_LINE_LIMIT = 120
LARGE_FUNCTION_LINE_LIMIT = 80


INTERNAL_CATEGORIES = [
    "convention_violation",
    "documentation_issues",
    "inflexible_code",
    "misleading_code",
    "non_idiomatic_code",
    "non_production_code",
    "readability_issues",
    "redundant_code",
]

EXTERNAL_CATEGORIES = [
    "arithmetic_issues",
    "attribute_issues",
    "compatibility_issues",
    "error_handling_issues",
    "logical_issues",
    "memory_safety",
    "performance",
    "runtime_panic_risks",
    "thread_safety",
    "type_safety",
]

CATEGORY_TOLERANCE = {
    "convention_violation": 5.0,
    "documentation_issues": 6.0,
    "inflexible_code": 3.0,
    "misleading_code": 2.5,
    "non_idiomatic_code": 4.0,
    "non_production_code": 1.5,
    "readability_issues": 4.0,
    "redundant_code": 4.0,
    "arithmetic_issues": 1.5,
    "attribute_issues": 2.5,
    "compatibility_issues": 2.0,
    "error_handling_issues": 2.0,
    "logical_issues": 1.5,
    "memory_safety": 1.2,
    "performance": 3.0,
    "runtime_panic_risks": 1.5,
    "thread_safety": 1.0,
    "type_safety": 1.5,
}

# Paper-inspired weighting:
# - 50% internal quality, 50% external quality
# - equal base weight per category inside each group
CATEGORY_WEIGHT = {
    **{name: 0.5 / len(INTERNAL_CATEGORIES) for name in INTERNAL_CATEGORIES},
    **{name: 0.5 / len(EXTERNAL_CATEGORIES) for name in EXTERNAL_CATEGORIES},
}


@dataclass
class ScoreReport:
    rust_project: str
    summary: dict[str, Any]
    categories: dict[str, Any]
    evidence: dict[str, Any]
    supplementary: dict[str, Any]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def iter_rust_files(project_root: Path) -> list[Path]:
    src_root = project_root / "src" if (project_root / "src").is_dir() else project_root
    files = []
    for path in src_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in RUST_EXTENSIONS:
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def strip_comments_and_strings(text: str) -> str:
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if ch == "/" and nxt == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            depth = 1
            while i < n and depth > 0:
                if i + 1 < n and text[i] == "/" and text[i + 1] == "*":
                    depth += 1
                    i += 2
                    continue
                if i + 1 < n and text[i] == "*" and text[i + 1] == "/":
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
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                if text[i] == "\n":
                    result.append("\n")
                i += 1
            continue
        if ch == "'" and i + 2 < n:
            if text[i + 1] == "\\" and i + 3 < n and text[i + 3] == "'":
                result.append(" ")
                i += 4
                continue
            if text[i + 2] == "'":
                result.append(" ")
                i += 3
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def auto_report_path(project_root: Path, filename: str) -> Path | None:
    path = project_root / filename
    return path if path.is_file() else None


def load_json_or_none(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_rust_metrics(project_root: Path) -> dict[str, Any]:
    files = iter_rust_files(project_root)
    file_metrics: dict[str, dict[str, int]] = {}
    total_loc = 0
    largest_file_loc = 0
    public_items = 0
    undocumented_public_items = 0
    long_lines = 0
    large_functions = 0
    fn_count = 0
    struct_count = 0
    enum_count = 0
    trait_count = 0
    impl_count = 0
    allow_attrs = 0
    expect_attrs = 0
    unsafe_impl_send_sync = 0
    static_mut = 0
    transmute_count = 0
    from_raw_parts_count = 0
    panic_count = 0
    unreachable_count = 0
    todo_count = 0
    unimplemented_count = 0
    unwrap_count = 0
    expect_count = 0
    dbg_count = 0
    unix_api_count = 0
    procfs_count = 0
    cast_count = 0

    for rust_file in files:
        text = rust_file.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        non_empty_loc = sum(1 for line in lines if line.strip())
        total_loc += non_empty_loc
        largest_file_loc = max(largest_file_loc, non_empty_loc)

        sanitized = strip_comments_and_strings(text)
        fn_count += len(FN_HEADER_RE.findall(sanitized))
        struct_count += len(re.findall(r"(?m)^\s*(?:pub\s+)?struct\b", sanitized))
        enum_count += len(re.findall(r"(?m)^\s*(?:pub\s+)?enum\b", sanitized))
        trait_count += len(re.findall(r"(?m)^\s*(?:pub\s+)?trait\b", sanitized))
        impl_count += len(re.findall(r"(?m)^\s*impl\b", sanitized))

        allow_attrs += len(ALLOW_ATTR_RE.findall(sanitized))
        expect_attrs += len(EXPECT_ATTR_RE.findall(sanitized))
        unsafe_impl_send_sync += len(UNSAFE_IMPL_SEND_SYNC_RE.findall(sanitized))
        static_mut += len(STATIC_MUT_RE.findall(sanitized))
        transmute_count += len(TRANSMUTE_RE.findall(sanitized))
        from_raw_parts_count += len(FROM_RAW_PARTS_RE.findall(sanitized))
        panic_count += len(PANIC_RE.findall(sanitized))
        unreachable_count += len(UNREACHABLE_RE.findall(sanitized))
        todo_count += len(TODO_RE.findall(sanitized))
        unimplemented_count += len(UNIMPLEMENTED_RE.findall(sanitized))
        unwrap_count += len(UNWRAP_RE.findall(sanitized))
        expect_count += len(EXPECT_RE.findall(sanitized))
        dbg_count += len(DBG_RE.findall(sanitized))
        unix_api_count += len(UNIX_API_RE.findall(sanitized))
        procfs_count += len(PROCFS_RE.findall(text))
        cast_count += len(AS_CAST_RE.findall(sanitized))

        for line in lines:
            if len(line) > LONG_LINE_LIMIT:
                long_lines += 1

        public_matches = list(PUBLIC_ITEM_RE.finditer(sanitized))
        public_items += len(public_matches)
        for match in public_matches:
            start_line = sanitized[: match.start()].count("\n")
            cursor = start_line - 1
            documented = False
            while cursor >= 0:
                prev = lines[cursor].strip()
                if not prev:
                    cursor -= 1
                    continue
                if DOC_LINE_RE.match(lines[cursor]):
                    documented = True
                break
            if not documented:
                undocumented_public_items += 1

        for match in FN_HEADER_RE.finditer(sanitized):
            start = sanitized.find("{", match.end())
            if start == -1:
                continue
            depth = 0
            end = start
            while end < len(sanitized):
                if sanitized[end] == "{":
                    depth += 1
                elif sanitized[end] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            body = sanitized[start:end + 1] if end < len(sanitized) else sanitized[start:]
            function_lines = body.count("\n") + 1
            if function_lines >= LARGE_FUNCTION_LINE_LIMIT:
                large_functions += 1

        rel = rust_file.relative_to(project_root).as_posix()
        file_metrics[rel] = {"loc": non_empty_loc}

    largest_file_share = 1.0 if total_loc == 0 else largest_file_loc / total_loc
    abstraction_density = (struct_count + enum_count + trait_count + impl_count) / max(fn_count, 1)

    return {
        "src_file_count": len(files),
        "total_loc": total_loc,
        "largest_file_loc": largest_file_loc,
        "largest_file_share": largest_file_share,
        "public_items": public_items,
        "undocumented_public_items": undocumented_public_items,
        "long_lines": long_lines,
        "large_functions": large_functions,
        "fn_count": fn_count,
        "struct_count": struct_count,
        "enum_count": enum_count,
        "trait_count": trait_count,
        "impl_count": impl_count,
        "abstraction_density": abstraction_density,
        "allow_attrs": allow_attrs,
        "expect_attrs": expect_attrs,
        "unsafe_impl_send_sync": unsafe_impl_send_sync,
        "static_mut": static_mut,
        "transmute_count": transmute_count,
        "from_raw_parts_count": from_raw_parts_count,
        "panic_count": panic_count,
        "unreachable_count": unreachable_count,
        "todo_count": todo_count,
        "unimplemented_count": unimplemented_count,
        "unwrap_count": unwrap_count,
        "expect_count": expect_count,
        "dbg_count": dbg_count,
        "unix_api_count": unix_api_count,
        "procfs_count": procfs_count,
        "cast_count": cast_count,
        "file_metrics": file_metrics,
    }


def clippy_item_category(lint: str, message: str, severity: str, file_name: str) -> tuple[str, float]:
    lint_l = (lint or "").lower()
    msg_l = (message or "").lower()
    file_l = (file_name or "").lower()

    if "bindgen generation failed" in file_l or "bindgen generation failed" in msg_l:
        return "compatibility_issues", 4.0
    if "undeclared" in msg_l or "implicit function declaration" in msg_l:
        return "compatibility_issues", 4.0
    if "parameter list without types" in msg_l:
        return "compatibility_issues", 4.0
    if "transmute" in lint_l or "transmute" in msg_l:
        return "type_safety", 3.0
    if "cast" in lint_l:
        return "type_safety", 2.0
    if "panic" in lint_l or "unwrap" in lint_l or "expect" in lint_l:
        return "runtime_panic_risks", 2.5
    if "missing_docs" in lint_l or "doc" in lint_l:
        return "documentation_issues", 1.5
    if "deprecated" in lint_l or "deprecated" in msg_l:
        return "compatibility_issues", 1.5
    if "unused_attributes" in lint_l or "attribute" in lint_l:
        return "attribute_issues", 1.5
    if any(token in lint_l for token in ["nonstandard", "wrong_self_convention", "module_name_repetitions"]):
        return "convention_violation", 1.5
    if any(token in lint_l for token in ["needless", "useless", "redundant", "duplicate"]):
        return "redundant_code", 1.3
    if any(token in lint_l for token in ["perf", "inefficient", "slow_vector_initialization"]):
        return "performance", 1.5
    if any(token in lint_l for token in ["suspicious", "correctness"]):
        return "logical_issues", 3.0 if severity == "error" else 2.0
    if any(token in lint_l for token in ["complexity", "cognitive", "large_enum_variant"]):
        return "readability_issues", 1.2
    if any(token in lint_l for token in ["manual_", "ptr_arg", "explicit_", "question_mark", "collapsible_if"]):
        return "non_idiomatic_code", 1.2
    if "if statement can be collapsed" in msg_l:
        return "non_idiomatic_code", 1.0
    if severity == "error":
        return "logical_issues", 2.0
    return "readability_issues", 0.8


def accumulate_clippy_penalties(clippy_report: dict[str, Any] | None) -> tuple[dict[str, float], dict[str, Any]]:
    penalties = {category: 0.0 for category in CATEGORY_WEIGHT}
    evidence = {
        "clippy_warnings": 0,
        "clippy_errors": 0,
        "rustfmt_files_needing_format": 0,
        "classified_items": [],
        "clippy_failed": False,
    }
    if not clippy_report:
        return penalties, evidence

    summary = clippy_report.get("summary", {})
    status = clippy_report.get("status", {})
    evidence["clippy_warnings"] = int(summary.get("clippy_warnings", 0) or 0)
    evidence["clippy_errors"] = int(summary.get("clippy_errors", 0) or 0)
    evidence["rustfmt_files_needing_format"] = int(summary.get("rustfmt_files_needing_format", 0) or 0)
    evidence["clippy_failed"] = bool(status.get("clippy_failed", 0))

    for item in clippy_report.get("clippy_details", []):
        category, weight = clippy_item_category(
            str(item.get("lint", "")),
            str(item.get("message", "")),
            "warning",
            str(item.get("file", "")),
        )
        penalties[category] += weight
        evidence["classified_items"].append(
            {"severity": "warning", "category": category, "weight": weight, "message": item.get("message", "")}
        )

    for item in clippy_report.get("clippy_error_details", []):
        category, weight = clippy_item_category(
            str(item.get("code", "")),
            str(item.get("message", "")),
            "error",
            str(item.get("file", "")),
        )
        penalties[category] += weight
        evidence["classified_items"].append(
            {"severity": "error", "category": category, "weight": weight, "message": item.get("message", "")}
        )

    rustfmt_penalty = evidence["rustfmt_files_needing_format"] * 1.0
    penalties["convention_violation"] += rustfmt_penalty * 0.4
    penalties["readability_issues"] += rustfmt_penalty * 0.6
    if evidence["clippy_failed"]:
        penalties["compatibility_issues"] += 3.0

    return penalties, evidence


def unsafe_metrics_value(unsafe_metrics: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not unsafe_metrics:
        return default
    rust = unsafe_metrics.get("rust", {})
    return float(rust.get(key, default) or default)


def raw_ptr_value(raw_ptr_stats: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not raw_ptr_stats:
        return default
    return float(raw_ptr_stats.get(key, default) or default)


def accumulate_source_penalties(
    rust_metrics: dict[str, Any],
    unsafe_metrics: dict[str, Any] | None,
    raw_ptr_stats: dict[str, Any] | None,
) -> tuple[dict[str, float], dict[str, Any]]:
    penalties = {category: 0.0 for category in CATEGORY_WEIGHT}
    evidence = {}
    total_loc = max(1, int(rust_metrics["total_loc"]))

    undocumented = int(rust_metrics["undocumented_public_items"])
    long_lines = int(rust_metrics["long_lines"])
    large_functions = int(rust_metrics["large_functions"])
    todo_count = int(rust_metrics["todo_count"])
    unimplemented_count = int(rust_metrics["unimplemented_count"])
    dbg_count = int(rust_metrics["dbg_count"])
    panic_count = int(rust_metrics["panic_count"])
    unreachable_count = int(rust_metrics["unreachable_count"])
    unwrap_count = int(rust_metrics["unwrap_count"])
    expect_count = int(rust_metrics["expect_count"])
    static_mut = int(rust_metrics["static_mut"])
    unsafe_impl_send_sync = int(rust_metrics["unsafe_impl_send_sync"])
    allow_attrs = int(rust_metrics["allow_attrs"])
    expect_attrs = int(rust_metrics["expect_attrs"])
    transmute_count = int(rust_metrics["transmute_count"])
    from_raw_parts_count = int(rust_metrics["from_raw_parts_count"])
    unix_api_count = int(rust_metrics["unix_api_count"])
    procfs_count = int(rust_metrics["procfs_count"])
    cast_count = int(rust_metrics["cast_count"])

    raw_ptr_types = raw_ptr_value(raw_ptr_stats, "total_raw_ptr_type_occurrences")
    raw_ptr_derefs = raw_ptr_value(raw_ptr_stats, "total_raw_ptr_dereferences")
    unsafe_rate = unsafe_metrics_value(unsafe_metrics, "unsafe_rate")
    unsafe_lines = unsafe_metrics_value(unsafe_metrics, "unsafe_lines")
    unsafe_blocks = unsafe_metrics_value(unsafe_metrics, "unsafe_blocks")
    unsafe_functions = unsafe_metrics_value(unsafe_metrics, "unsafe_functions")

    # Internal quality.
    penalties["documentation_issues"] += undocumented * 1.0
    penalties["readability_issues"] += long_lines * 0.2 + large_functions * 1.5
    penalties["convention_violation"] += allow_attrs * 0.5
    penalties["non_idiomatic_code"] += raw_ptr_types * 0.5 + unsafe_blocks * 0.8 + max(0.0, rust_metrics["largest_file_share"] - 0.45) * 12.0
    penalties["non_production_code"] += todo_count * 4.0 + unimplemented_count * 4.0 + dbg_count * 1.5
    penalties["redundant_code"] += max(0.0, long_lines - total_loc * 0.05) * 0.05
    penalties["inflexible_code"] += max(0.0, rust_metrics["largest_file_share"] - 0.40) * 15.0
    penalties["inflexible_code"] += max(0.0, 0.20 - rust_metrics["abstraction_density"]) * 20.0

    # External quality.
    penalties["runtime_panic_risks"] += panic_count * 2.0 + unreachable_count * 1.5 + unwrap_count * 1.5 + expect_count * 1.5
    penalties["error_handling_issues"] += unwrap_count * 1.2 + expect_count * 1.2
    penalties["thread_safety"] += static_mut * 4.0 + unsafe_impl_send_sync * 4.0
    penalties["attribute_issues"] += allow_attrs * 0.6 + expect_attrs * 0.4
    penalties["compatibility_issues"] += unix_api_count * 1.5 + procfs_count * 2.0
    penalties["memory_safety"] += unsafe_lines * 0.08 + unsafe_blocks * 1.0 + unsafe_functions * 1.0
    penalties["memory_safety"] += raw_ptr_types * 1.5 + raw_ptr_derefs * 2.5 + transmute_count * 3.0 + from_raw_parts_count * 3.0
    penalties["type_safety"] += cast_count * 0.08 + transmute_count * 2.5 + raw_ptr_types * 0.6
    penalties["performance"] += max(0.0, rust_metrics["largest_file_share"] - 0.60) * 5.0
    penalties["logical_issues"] += max(0.0, unsafe_rate - 0.02) * 100.0 * 0.2
    penalties["arithmetic_issues"] += cast_count * 0.03

    evidence.update(
        {
            "undocumented_public_items": undocumented,
            "long_lines": long_lines,
            "large_functions": large_functions,
            "todo_count": todo_count,
            "unimplemented_count": unimplemented_count,
            "dbg_count": dbg_count,
            "panic_count": panic_count,
            "unreachable_count": unreachable_count,
            "unwrap_count": unwrap_count,
            "expect_count": expect_count,
            "static_mut": static_mut,
            "unsafe_impl_send_sync": unsafe_impl_send_sync,
            "allow_attrs": allow_attrs,
            "expect_attrs": expect_attrs,
            "transmute_count": transmute_count,
            "from_raw_parts_count": from_raw_parts_count,
            "unix_api_count": unix_api_count,
            "procfs_count": procfs_count,
            "cast_count": cast_count,
            "raw_ptr_type_occurrences": raw_ptr_types,
            "raw_ptr_dereferences": raw_ptr_derefs,
            "unsafe_rate": unsafe_rate,
            "unsafe_lines": unsafe_lines,
            "unsafe_blocks": unsafe_blocks,
            "unsafe_functions": unsafe_functions,
        }
    )
    return penalties, evidence


def category_score(category: str, penalty_points: float, kloc: float) -> float:
    tolerance = CATEGORY_TOLERANCE[category]
    density = penalty_points / max(kloc, 1e-9)
    return 100.0 / (1.0 + density / tolerance)


def load_optional_translation_intent(c_project: Path | None, rust_project: Path) -> dict[str, Any]:
    if c_project is None:
        return {}
    try:
        from translation_fidelity import extract_c_project_features, build_project_report
    except Exception:
        return {}

    try:
        c_features = extract_c_project_features(c_project)
        report = build_project_report(c_features, rust_project)
        return {
            "translation_intent_index": report.summary.get("translation_intent_index"),
            "intent_token_coverage": report.summary.get("intent_token_coverage"),
            "behavior_marker_coverage": report.summary.get("behavior_marker_coverage"),
            "concept_coverage": report.summary.get("concept_coverage"),
            "rust_design_score": report.summary.get("rust_design_score"),
        }
    except Exception:
        return {}


def build_report(
    rust_project: Path,
    c_project: Path | None,
    clippy_report_path: Path | None,
    unsafe_metrics_path: Path | None,
    raw_ptr_stats_path: Path | None,
) -> ScoreReport:
    rust_metrics = extract_rust_metrics(rust_project)
    clippy_report = load_json_or_none(clippy_report_path)
    unsafe_metrics = load_json_or_none(unsafe_metrics_path)
    raw_ptr_stats = load_json_or_none(raw_ptr_stats_path)

    penalty_from_clippy, clippy_evidence = accumulate_clippy_penalties(clippy_report)
    penalty_from_source, source_evidence = accumulate_source_penalties(rust_metrics, unsafe_metrics, raw_ptr_stats)

    total_penalties = {
        category: penalty_from_clippy[category] + penalty_from_source[category]
        for category in CATEGORY_WEIGHT
    }

    kloc = max(rust_metrics["total_loc"], 1) / 1000.0
    category_reports: dict[str, Any] = {}
    weighted_score = 0.0
    internal_score = 0.0
    external_score = 0.0

    for category in CATEGORY_WEIGHT:
        score = category_score(category, total_penalties[category], kloc)
        weighted_score += CATEGORY_WEIGHT[category] * score
        if category in INTERNAL_CATEGORIES:
            internal_score += CATEGORY_WEIGHT[category] * score / 0.5
        else:
            external_score += CATEGORY_WEIGHT[category] * score / 0.5
        category_reports[category] = {
            "weight": round(CATEGORY_WEIGHT[category], 6),
            "penalty_points": round(total_penalties[category], 4),
            "penalty_density_per_kloc": round(total_penalties[category] / max(kloc, 1e-9), 4),
            "score": round(score, 4),
        }

    summary = {
        "paper_quality_score": round(weighted_score, 4),
        "internal_quality_score": round(internal_score, 4),
        "external_quality_score": round(external_score, 4),
        "total_loc": rust_metrics["total_loc"],
        "src_file_count": rust_metrics["src_file_count"],
        "largest_file_share": round(rust_metrics["largest_file_share"], 6),
        "clippy_report_found": clippy_report is not None,
        "unsafe_metrics_found": unsafe_metrics is not None,
        "raw_ptr_stats_found": raw_ptr_stats is not None,
    }

    supplementary = {
        "source_scan": {
            "fn_count": rust_metrics["fn_count"],
            "struct_count": rust_metrics["struct_count"],
            "enum_count": rust_metrics["enum_count"],
            "trait_count": rust_metrics["trait_count"],
            "impl_count": rust_metrics["impl_count"],
            "public_items": rust_metrics["public_items"],
            "undocumented_public_items": rust_metrics["undocumented_public_items"],
            "abstraction_density": round(rust_metrics["abstraction_density"], 6),
        },
        "translation_intent": load_optional_translation_intent(c_project, rust_project),
    }

    evidence = {
        "reports": {
            "clippy_report": str(clippy_report_path) if clippy_report_path else None,
            "unsafe_metrics": str(unsafe_metrics_path) if unsafe_metrics_path else None,
            "raw_ptr_stats": str(raw_ptr_stats_path) if raw_ptr_stats_path else None,
        },
        "clippy": clippy_evidence,
        "source": source_evidence,
    }

    return ScoreReport(
        rust_project=str(rust_project.resolve()),
        summary=summary,
        categories=category_reports,
        evidence=evidence,
        supplementary=supplementary,
    )


def print_summary_table(reports: list[ScoreReport]) -> None:
    print("Paper-Inspired Rust Translation Quality Score")
    print("-" * 126)
    print(
        f"{'Rust project':<45} {'Score':>8} {'Internal':>10} {'External':>10} "
        f"{'LOC':>8} {'Files':>7} {'MaxShare':>9}"
    )
    print("-" * 126)
    for report in reports:
        print(
            f"{report.rust_project:<45} "
            f"{report.summary['paper_quality_score']:>8.2f} "
            f"{report.summary['internal_quality_score']:>10.2f} "
            f"{report.summary['external_quality_score']:>10.2f} "
            f"{report.summary['total_loc']:>8} "
            f"{report.summary['src_file_count']:>7} "
            f"{report.summary['largest_file_share']:>9.4f}"
        )


def serialize_report(report: ScoreReport, c_project: Path | None) -> dict[str, Any]:
    return {
        "paper_reference": {
            "title": "Code Quality Analysis of Translations from C to Rust",
            "arxiv": "https://arxiv.org/abs/2602.00840",
            "note": "Automatic approximation without the paper's LLM review and manual analysis stages.",
        },
        "weighting_note": (
            "The paper does not define a single automatic weighted score. "
            "This implementation splits total weight 50/50 across internal and external quality, "
            "then distributes weight equally within each group."
        ),
        "c_project": str(c_project.resolve()) if c_project else None,
        "rust_project": report.rust_project,
        "summary": report.summary,
        "categories": report.categories,
        "evidence": report.evidence,
        "supplementary": report.supplementary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute a paper-inspired automatic quality score for one or more Rust translations."
    )
    parser.add_argument("--rust-project", action="append", required=True, help="Path to a Rust project. Repeat to compare multiple projects.")
    parser.add_argument("--c-project", help="Optional original C project path for supplementary translation-intent scoring.")
    parser.add_argument("--clippy-report", action="append", help="Optional clippy_report.json path per Rust project, in the same order.")
    parser.add_argument("--unsafe-metrics", action="append", help="Optional unsafe_metrics.json path per Rust project, in the same order.")
    parser.add_argument("--raw-ptr-stats", action="append", help="Optional raw_ptr_stats.json path per Rust project, in the same order.")
    parser.add_argument("--output-json", help="Optional JSON output path. Stores a report or list of reports.")
    args = parser.parse_args()

    rust_projects = [Path(value).resolve() for value in args.rust_project]
    c_project = Path(args.c_project).resolve() if args.c_project else None
    if c_project and not c_project.is_dir():
        raise SystemExit(f"C project not found: {c_project}")

    def resolve_optional_list(values: list[str] | None, name: str) -> list[Path | None]:
        if not values:
            return [None] * len(rust_projects)
        if len(values) != len(rust_projects):
            raise SystemExit(f"{name} count must match --rust-project count")
        return [Path(value).resolve() for value in values]

    clippy_paths = resolve_optional_list(args.clippy_report, "--clippy-report")
    unsafe_paths = resolve_optional_list(args.unsafe_metrics, "--unsafe-metrics")
    raw_ptr_paths = resolve_optional_list(args.raw_ptr_stats, "--raw-ptr-stats")

    reports: list[ScoreReport] = []
    for index, rust_project in enumerate(rust_projects):
        if not rust_project.is_dir():
            raise SystemExit(f"Rust project not found: {rust_project}")
        clippy_path = clippy_paths[index] or auto_report_path(rust_project, "clippy_report.json")
        unsafe_path = unsafe_paths[index] or auto_report_path(rust_project, "unsafe_metrics.json")
        raw_ptr_path = raw_ptr_paths[index] or auto_report_path(rust_project, "raw_ptr_stats.json")
        reports.append(build_report(rust_project, c_project, clippy_path, unsafe_path, raw_ptr_path))

    print_summary_table(reports)

    if args.output_json:
        payload: Any = [serialize_report(report, c_project) for report in reports]
        if len(payload) == 1:
            payload = payload[0]
        output_path = Path(args.output_json).resolve()
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved JSON report to: {output_path}")


if __name__ == "__main__":
    main()
