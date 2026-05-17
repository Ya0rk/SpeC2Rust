"""Agent package public API.

Keep this module lightweight. Some agents depend on optional native packages
such as tree-sitter, but utility subpackages like agent.rtest should be usable
without importing the full C-analysis stack.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "CDocAgent": ("agent.c_doc_agent", "CDocAgent"),
    "PointerAgent": ("agent.pointer_agent", "PointerAgent"),
    "ErrorOrganizerAgent": ("agent.error_organizer_agent", "ErrorOrganizerAgent"),
    "MacroAgent": ("agent.macro_agent", "MacroAgent"),
    "RustAgent": ("agent.rust_agent", "RustAgent"),
    "StableRustAgent": ("agent.alternatives.stable_rust_agent", "StableRustAgent"),
    "GrowthRustAgent": ("agent.alternatives.growth_rust_agent", "GrowthRustAgent"),
    "Fixer": ("agent.code_fixer_agent", "Fixer"),
    "CodeFixer": ("agent.code_fixer_agent", "CodeFixer"),
    "TestFixer": ("agent.code_fixer_agent", "TestFixer"),
    "SpecAgent": ("agent.spec_agent", "SpecAgent"),
    "SpecJsonAgent": ("agent.spec_json_agent", "SpecJsonAgent"),
    "UnfinishedCodeAgent": ("agent.unfinished_code_agent", "UnfinishedCodeAgent"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
