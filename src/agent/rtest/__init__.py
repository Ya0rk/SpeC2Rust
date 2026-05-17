"""Rust 项目功能测试与修复 Agent。

将 C 项目里的 sh 测试脚本拷贝到翻译后的 Rust 项目里，编译 Rust 项目得到可执行文件，
然后用 sh 脚本驱动测试；若有失败用例，逐个喂给 LLM 进行修复。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["RustTestAgent", "TestCaseResult", "TestRunSummary"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("agent.rtest.rust_test_agent"), name)
    globals()[name] = value
    return value
