"""Rust 结构化修复包。

提供确定性的括号匹配与修复功能，作为 LLM 修复的前置处理层。
对外仅暴露 orchestrator 中的入口函数。
"""

from .orchestrator import try_deterministic_repair, RepairOutcome

__all__ = ["try_deterministic_repair", "RepairOutcome"]
