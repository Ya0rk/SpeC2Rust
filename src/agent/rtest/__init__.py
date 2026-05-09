"""Rust 项目功能测试与修复 Agent。

将 C 项目里的 sh 测试脚本拷贝到翻译后的 Rust 项目里，编译 Rust 项目得到可执行文件，
然后用 sh 脚本驱动测试；若有失败用例，逐个喂给 LLM 进行修复。
"""

from .rust_test_agent import RustTestAgent, TestCaseResult, TestRunSummary

__all__ = ["RustTestAgent", "TestCaseResult", "TestRunSummary"]
