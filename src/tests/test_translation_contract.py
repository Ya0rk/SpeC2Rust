import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.spec_agent import SpecAgent
from agent.rust_agent import RustAgent
from config.config import Config


def build_lightweight_spec_agent() -> SpecAgent:
    agent = SpecAgent.__new__(SpecAgent)
    agent.config = Config(config_path=None, model_name="qwen32")
    agent.project_analysis = None
    agent.repo_unit = None
    agent.module_units = []
    agent.file_units = []
    agent.cluster_units = []
    agent.dependency_graph = {}
    agent.pointer_findings = []
    agent.macro_findings = []
    agent.pointer_notes_enabled = False
    agent.macro_notes_enabled = False
    return agent


class TranslationContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f"_tmp_translation_contract_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_spec_agent_builds_generic_contract_with_roles_and_allowed_files(self):
        project = self.root / "generic_project"
        (project / "include").mkdir(parents=True)
        (project / "src").mkdir()
        (project / "examples").mkdir()
        (project / "tests").mkdir()
        (project / "include" / "core.h").write_text(
            """
typedef struct widget {
    int value;
} widget;

int api_create(void);
""".strip(),
            encoding="utf-8",
        )

        project_info = {
            "project_name": "generic_project",
            "c_files": ["src/core.c", "examples/demo_example.c", "tests/core_test.c"],
            "h_files": ["include/core.h"],
            "other_files": ["README.md"],
            "build_system": "unknown",
            "build_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                {
                    "name": "api_create",
                    "file": "src/core.c",
                    "start_line": 10,
                    "end_line": 14,
                    "source": "int api_create(void)\n{\n    return helper();\n}",
                },
                {
                    "name": "helper",
                    "file": "src/core.c",
                    "start_line": 16,
                    "end_line": 18,
                    "source": "static int helper(void)\n{\n    return 1;\n}",
                },
                {
                    "name": "main",
                    "file": "examples/demo_example.c",
                    "start_line": 1,
                    "end_line": 3,
                    "source": "int main(int argc, char **argv)\n{\n    return api_create();\n}",
                },
                {
                    "name": "unit_test_create",
                    "file": "tests/core_test.c",
                    "start_line": 1,
                    "end_line": 3,
                    "source": "int unit_test_create(void)\n{\n    return api_create() == 1;\n}",
                },
            ],
            "structs": [],
            "macros": [],
            "global_vars": [],
            "file_path_map": {
                "src/core.c": str(project / "src" / "core.c"),
                "examples/demo_example.c": str(project / "examples" / "demo_example.c"),
                "tests/core_test.c": str(project / "tests" / "core_test.c"),
                "include/core.h": str(project / "include" / "core.h"),
            },
        }

        agent = build_lightweight_spec_agent()
        contract = agent._build_translation_contract(
            str(project),
            project_info,
            project_analysis,
            module_units=[{"name": "core", "files": ["src/core.c"], "category": "library"}],
        )

        roles = {item["name"]: item["role"] for item in contract["functions"]}
        self.assertEqual(roles["api_create"], "public_api")
        self.assertEqual(roles["helper"], "internal_helper")
        self.assertEqual(roles["main"], "example_entry")
        self.assertEqual(roles["unit_test_create"], "test_case")

        type_names = {item["name"] for item in contract["types"]}
        self.assertIn("widget", type_names)
        self.assertNotIn("anonymous", type_names)

        allowed = set(contract["generation_boundary"]["allowed_rust_files"])
        self.assertIn("Cargo.toml", allowed)
        self.assertIn("src/lib.rs", allowed)
        self.assertIn("src/core.rs", allowed)
        self.assertIn("README.md", allowed)
        self.assertNotIn("src/sync.rs", allowed)
        self.assertNotIn("tests/core_test.rs", allowed)
        self.assertFalse(contract["generation_boundary"]["allow_tests"])

    def test_spec_agent_lints_scope_expansion_without_project_specific_rules(self):
        output = self.root / "out"
        doc_dir = output / "specs" / "001-generic-rust-port"
        doc_dir.mkdir(parents=True)
        (doc_dir / "tasks.md").write_text(
            """
# Tasks

### Phase 8: Safety
- 文件: `src/sync.rs`
- 描述: Add Send and Sync support and recovery mechanism.
""".strip(),
            encoding="utf-8",
        )
        contract = {
            "generation_boundary": {
                "allowed_rust_files": ["Cargo.toml", "src/lib.rs", "src/core.rs", "README.md"],
                "allow_ffi": False,
            }
        }

        agent = build_lightweight_spec_agent()
        findings = agent._lint_generated_docs(str(output), contract)

        self.assertGreaterEqual(len(findings), 2)
        joined = "\n".join(item["message"] for item in findings)
        self.assertIn("src/sync.rs", joined)
        self.assertIn("Phase 8", joined)

    def test_spec_agent_postprocess_generated_markdown_deduplicates_repeated_lines(self):
        agent = build_lightweight_spec_agent()
        content = """
```markdown
# Summary

- keep public API stable
- keep public API stable

This module preserves insertion order.
This module preserves insertion order.

## Tasks
- map node struct
- map node struct
```
""".strip()

        cleaned = agent._postprocess_generated_markdown(content)

        self.assertEqual(cleaned.count("keep public API stable"), 1)
        self.assertEqual(cleaned.count("This module preserves insertion order."), 1)
        self.assertEqual(cleaned.count("map node struct"), 1)
        self.assertNotIn("```markdown", cleaned)


class RustAgentScopeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / f"_tmp_rust_agent_scope_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def _write_contract(self):
        rewrite_context = self.root / "docs" / "rewrite-context"
        rewrite_context.mkdir(parents=True)
        contract = {
            "project": {"name": "generic_project", "kind": "library"},
            "generation_boundary": {
                "allowed_rust_files": ["Cargo.toml", "src/lib.rs", "src/core.rs", "README.md"],
                "allow_tests": False,
                "allow_examples": False,
                "allow_benches": False,
                "allow_ffi": False,
                "dependency_policy": "std_only_by_default",
                "allowed_dependencies": [],
            },
            "forbidden_without_evidence": ["thread_safe_api", "recovery_mechanism", "serde"],
        }
        contract_path = rewrite_context / "translation_contract.json"
        contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        return contract_path

    def test_rust_agent_loads_contract_and_rejects_out_of_scope_files(self):
        self._write_contract()
        agent = RustAgent(Config(config_path=None, model_name="qwen32"))

        agent.load_documents([str(self.root)])
        sanitized = agent._sanitize_generation_file_list(
            [
                "Cargo.toml",
                "src/lib.rs",
                "src/core.rs",
                "src/sync.rs",
                "src/recovery.rs",
                "tests/core_test.rs",
                "README.md",
            ]
        )

        self.assertEqual(sanitized, ["Cargo.toml", "src/lib.rs", "src/core.rs", "README.md"])
        self.assertEqual(agent.translation_contract["project"]["name"], "generic_project")

    def test_rust_agent_contract_fallback_returns_allowed_files_when_model_outputs_only_extras(self):
        self._write_contract()
        agent = RustAgent(Config(config_path=None, model_name="qwen32"))

        agent.load_documents([str(self.root)])
        sanitized = agent._sanitize_generation_file_list(["src/sync.rs", "tests/generated.rs"])

        self.assertEqual(sanitized, ["Cargo.toml", "src/lib.rs", "src/core.rs", "README.md"])

    def test_rust_agent_std_only_contract_filters_detected_dependencies(self):
        self._write_contract()
        agent = RustAgent(Config(config_path=None, model_name="qwen32"))

        agent.load_documents([str(self.root)])
        deps = agent._detect_dependencies(
            "use serde::Serialize;\nuse anyhow::Result;\nuse std::collections::HashMap;\n"
        )

        self.assertEqual(deps, {})

    def test_rust_agent_contract_lint_flags_scope_expansion_inside_allowed_file(self):
        self._write_contract()
        agent = RustAgent(Config(config_path=None, model_name="qwen32"))

        agent.load_documents([str(self.root)])
        findings = agent._lint_generated_code_against_contract(
            "src/core.rs",
            """
use serde::Serialize;
use std::sync::{Arc, Mutex};

extern "C" fn c_api() {}

fn recover_state() {}
""".strip(),
        )

        joined = "\n".join(findings)
        self.assertIn("serde", joined)
        self.assertIn("FFI", joined)
        self.assertIn("线程安全", joined)
        self.assertIn("恢复机制", joined)

    def test_rust_agent_contract_lint_does_not_treat_package_metadata_as_dependencies(self):
        self._write_contract()
        agent = RustAgent(Config(config_path=None, model_name="qwen32"))

        agent.load_documents([str(self.root)])
        findings = agent._lint_generated_code_against_contract(
            "Cargo.toml",
            """
[package]
name = "generic-project"
version = "0.1.0"
edition = "2021"
description = "test package"
readme = "README.md"
license = "MIT"
authors = ["test <test@example.com>"]

[dependencies]

[lib]
name = "generic_project"
path = "src/lib.rs"
""".strip(),
        )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
