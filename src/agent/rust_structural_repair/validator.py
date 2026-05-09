"""修复后验证。

最小可用切片仅做"括号是否平衡"的快速验证，足以拦截 R1/R2 的回归。
未来可扩展为调用 syn-based Rust 二进制做完整语法检查。
"""

from __future__ import annotations

from dataclasses import dataclass

from .bracket_scanner import scan_brackets


@dataclass
class ValidationResult:
    is_valid: bool
    description: str


def validate_bracket_balance(source: str) -> ValidationResult:
    """轻量校验：源码中的括号必须完全平衡且无类型不匹配。"""
    result = scan_brackets(source)
    if result.is_balanced:
        return ValidationResult(True, "balanced")
    parts = []
    if result.unclosed_opens:
        parts.append(f"{len(result.unclosed_opens)} unclosed open(s)")
    if result.orphan_closes:
        parts.append(f"{len(result.orphan_closes)} orphan close(s)")
    if result.mismatches:
        parts.append(f"{len(result.mismatches)} mismatch(es)")
    return ValidationResult(False, "; ".join(parts))


def is_safe_repair(before: str, after: str) -> bool:
    """判定一次修复是否"安全"：
    - 修复后括号完全平衡
    - 修复前是不平衡的（否则没有必要修改）
    """
    if before == after:
        return False
    before_balanced = scan_brackets(before).is_balanced
    after_balanced = scan_brackets(after).is_balanced
    return after_balanced and not before_balanced
