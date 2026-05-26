"""Rust 项目功能测试与修复 Agent。

将 C 项目里的 sh 测试脚本拷贝到翻译后的 Rust 项目里，编译 Rust 项目得到可执行文件，
然后用 sh 脚本驱动测试；若有失败用例，逐个喂给 LLM 进行修复。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "RustTestAgent",
    "TestCaseResult",
    "TestRunSummary",
    "CProjectBuilder",
    "CBuildResult",
    "RuntimeProbeService",
    "SuiteRepairCoordinator",
    "SuiteRepairContext",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in {"CProjectBuilder", "CBuildResult"}:
        module_name = "agent.rtest.c_project_builder"
    elif name in {"RuntimeProbeService"}:
        module_name = "agent.rtest.runtime_probe"
    elif name in {"SuiteRepairCoordinator", "SuiteRepairContext"}:
        module_name = "agent.rtest.suite_repair_coordinator"
    else:
        module_name = "agent.rtest.rust_test_agent"
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
