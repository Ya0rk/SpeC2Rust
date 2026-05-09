"""结构化修复编排器。

对外仅暴露 try_deterministic_repair 函数。其内部流程：
1. 读取文件
2. 用 bracket_scanner 扫描，若已平衡则直接返回（不修改）
3. 依次应用 R1（截断闭合）、R2（孤立闭括号删除）
4. 用 validator 验证修复后必须平衡才写回，否则放弃
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .bracket_scanner import scan_brackets
from .repair_rules import apply_all_rules
from .validator import is_safe_repair, validate_bracket_balance


@dataclass
class RepairOutcome:
    file_path: str
    changed: bool
    description: str
    details: List[str] = field(default_factory=list)
    pre_imbalance: int = 0
    post_imbalance: int = 0

    def __str__(self) -> str:
        if not self.changed:
            return f"[skip] {self.file_path}: {self.description}"
        return (
            f"[fixed] {self.file_path}: {self.description} "
            f"(imbalance {self.pre_imbalance} -> {self.post_imbalance})"
        )


def try_deterministic_repair(file_path: str, *, encoding: str = "utf-8") -> RepairOutcome:
    """尝试对单个 Rust 文件做确定性括号修复。

    若文件已平衡或修复不安全，则不修改文件，返回 changed=False。
    """
    if not os.path.exists(file_path):
        return RepairOutcome(file_path, False, "file_not_found")

    try:
        with open(file_path, "r", encoding=encoding) as f:
            original = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        return RepairOutcome(file_path, False, f"read_error: {exc}")

    pre = scan_brackets(original)
    if pre.is_balanced:
        return RepairOutcome(file_path, False, "already_balanced")

    pre_imbalance = _count_imbalance(pre)

    rule_result = apply_all_rules(original)
    if not rule_result.changed:
        return RepairOutcome(
            file_path,
            False,
            f"no_applicable_rule: {rule_result.description}",
            details=rule_result.details,
            pre_imbalance=pre_imbalance,
            post_imbalance=pre_imbalance,
        )

    if not is_safe_repair(original, rule_result.source):
        validation = validate_bracket_balance(rule_result.source)
        return RepairOutcome(
            file_path,
            False,
            f"unsafe_repair: {validation.description}",
            details=rule_result.details,
            pre_imbalance=pre_imbalance,
            post_imbalance=_count_imbalance(scan_brackets(rule_result.source)),
        )

    try:
        with open(file_path, "w", encoding=encoding, newline="") as f:
            f.write(rule_result.source)
    except OSError as exc:
        return RepairOutcome(
            file_path,
            False,
            f"write_error: {exc}",
            details=rule_result.details,
            pre_imbalance=pre_imbalance,
        )

    return RepairOutcome(
        file_path,
        True,
        rule_result.description,
        details=rule_result.details,
        pre_imbalance=pre_imbalance,
        post_imbalance=0,
    )


def try_deterministic_repair_project(project_dir: str) -> List[RepairOutcome]:
    """对项目下所有 .rs 文件尝试结构化修复，返回每个文件的结果。"""
    outcomes: List[RepairOutcome] = []
    for root, _dirs, files in os.walk(project_dir):
        # 跳过 target 目录
        rel = os.path.relpath(root, project_dir).replace("\\", "/")
        if rel.startswith("target") or "/target/" in f"/{rel}/":
            continue
        for name in files:
            if not name.endswith(".rs"):
                continue
            outcomes.append(try_deterministic_repair(os.path.join(root, name)))
    return outcomes


def _count_imbalance(scan_result) -> int:
    return (
        len(scan_result.unclosed_opens)
        + len(scan_result.orphan_closes)
        + len(scan_result.mismatches)
    )
