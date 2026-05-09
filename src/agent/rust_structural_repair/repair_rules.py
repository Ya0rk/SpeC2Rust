"""确定性括号修复规则。

每条规则都遵循以下契约：
- 输入：原始源码字符串
- 输出：(修复后源码, 是否修改, 描述) 元组；若无法安全修复则 (源码, False, 原因)
- 规则之间幂等：连续应用同一规则不应继续修改源码
- 保守原则：宁可不修改，也不引入新错误
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .bracket_scanner import scan_brackets, ScanResult


@dataclass
class RuleResult:
    source: str
    changed: bool
    description: str
    details: List[str]


def repair_truncation(source: str) -> RuleResult:
    """R1: 文件末尾未闭合 → 按栈逆序追加对应闭合括号，缩进对齐到开括号所在行。

    仅在以下条件满足时执行：
    - 至少有 1 个未闭合开括号
    - 没有孤立的闭括号（避免与 R2 互相干扰）
    - 没有类型不匹配错误
    """
    result: ScanResult = scan_brackets(source)

    if not result.unclosed_opens:
        return RuleResult(source, False, "no_unclosed_opens", [])

    if result.orphan_closes:
        return RuleResult(
            source,
            False,
            "skipped_due_to_orphan_closes",
            [f"orphan {oc.char} at {oc.line}:{oc.col}" for oc in result.orphan_closes],
        )

    if result.mismatches:
        return RuleResult(
            source,
            False,
            "skipped_due_to_mismatch",
            [f"mismatch at {m.line}:{m.col} expected {m.expected} got {m.actual}" for m in result.mismatches],
        )

    # 确保源码以换行结尾
    fixed = source if source.endswith("\n") else source + "\n"
    appended: List[str] = []
    closing_for = {"{": "}", "(": ")", "[": "]"}

    # 栈逆序：最里层（最后入栈）先关闭
    for unclosed in reversed(result.unclosed_opens):
        close_char = closing_for[unclosed.char]
        line = unclosed.indent + close_char + "\n"
        fixed += line
        appended.append(f"{unclosed.char} at line {unclosed.line} -> append {close_char!r}")

    return RuleResult(
        fixed,
        True,
        f"appended_{len(appended)}_closing_brackets",
        appended,
    )


def repair_orphan_closes(source: str) -> RuleResult:
    """R2: 孤立闭括号删除。

    仅删除"独占一行"的孤立闭括号（该行除空白外只有这一个 `}`/`)`/`]`）。
    其他位置的孤立闭括号留给 LLM 处理（避免破坏 inline 语义）。
    """
    result = scan_brackets(source)

    if not result.orphan_closes:
        return RuleResult(source, False, "no_orphan_closes", [])

    if result.mismatches:
        return RuleResult(
            source,
            False,
            "skipped_due_to_mismatch",
            [f"mismatch at {m.line}:{m.col}" for m in result.mismatches],
        )

    lines = source.splitlines(keepends=True)
    # 收集需要删除的行号（1-based），仅保留独占一行的孤立闭括号
    to_delete: List[Tuple[int, str]] = []
    for orphan in result.orphan_closes:
        if orphan.line < 1 or orphan.line > len(lines):
            continue
        line_text = lines[orphan.line - 1]
        stripped = line_text.strip()
        if stripped == orphan.char:
            to_delete.append((orphan.line, orphan.char))

    if not to_delete:
        return RuleResult(
            source,
            False,
            "orphan_closes_not_isolated",
            [f"orphan {oc.char} at {oc.line}:{oc.col} (inline)" for oc in result.orphan_closes],
        )

    delete_lines = {ln for ln, _ in to_delete}
    new_lines = [line for idx, line in enumerate(lines, start=1) if idx not in delete_lines]
    fixed = "".join(new_lines)
    details = [f"removed {ch!r} at line {ln}" for ln, ch in to_delete]
    return RuleResult(fixed, True, f"removed_{len(to_delete)}_orphan_close_lines", details)


def apply_all_rules(source: str, max_passes: int = 3) -> RuleResult:
    """依次尝试 R1、R2，多遍直到稳定或达到上限。

    返回累计的修复结果。如果任何一遍发生修改，下一遍会从最新源码再次尝试。
    """
    current = source
    aggregate_details: List[str] = []
    any_change = False
    last_description = "no_change"

    for _pass in range(max_passes):
        pass_changed = False

        r1 = repair_truncation(current)
        if r1.changed:
            current = r1.source
            aggregate_details.append(f"[R1] {r1.description}")
            aggregate_details.extend([f"  - {d}" for d in r1.details])
            pass_changed = True
            last_description = r1.description

        r2 = repair_orphan_closes(current)
        if r2.changed:
            current = r2.source
            aggregate_details.append(f"[R2] {r2.description}")
            aggregate_details.extend([f"  - {d}" for d in r2.details])
            pass_changed = True
            last_description = r2.description

        if not pass_changed:
            break
        any_change = True

    return RuleResult(
        source=current,
        changed=any_change,
        description=last_description if any_change else "no_change",
        details=aggregate_details,
    )
