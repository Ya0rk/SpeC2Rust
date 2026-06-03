import sys
import unittest
import json
from types import SimpleNamespace
from pathlib import Path
import shutil
import uuid

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from agent.error_organizer_agent import ErrorOrganizerAgent
from agent.rust_repair_agent import RustRepairAgent
from agent.rust_repair_agent import RepairRunResult


class RustRepairAgentTests(unittest.TestCase):
    def test_clone_project_tree_creates_isolated_run_dir(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            source = root / "source"
            source.mkdir()
            (source / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (source / "src").mkdir()
            (source / "src" / "lib.rs").write_text("pub fn demo() {}\n", encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=3)
            run_dir = agent._clone_project_tree(str(source), str(root / "runs"), 1)

            self.assertTrue(Path(run_dir).exists())
            self.assertTrue((Path(run_dir) / "Cargo.toml").exists())
            self.assertEqual((Path(run_dir) / "src" / "lib.rs").read_text(encoding="utf-8"), "pub fn demo() {}\n")

            (Path(run_dir) / "src" / "lib.rs").write_text("pub fn changed() {}\n", encoding="utf-8")
            self.assertEqual((source / "src" / "lib.rs").read_text(encoding="utf-8"), "pub fn demo() {}\n")
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_group_rust_errors_by_file_extracts_primary_files(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        cargo_output = """
error[E0432]: unresolved import `bounds::Bounds`
 --> src\\lib.rs:9:9
  |
9 | pub use bounds::Bounds;
  |         ^^^^^^^^^^^^^^ no `Bounds` in `bounds`

error[E0507]: cannot move out of `*destroy` which is behind a shared reference
   --> src\\tree.rs:742:13
    |
742 |             destroy((*node_ptr).data);
    |             ^^^^^^^
"""

        grouped = agent._group_rust_errors_by_file(cargo_output)

        self.assertIn("src/lib.rs", grouped)
        self.assertIn("src/tree.rs", grouped)
        self.assertIn("unresolved import", grouped["src/lib.rs"])
        self.assertIn("cannot move out", grouped["src/tree.rs"])

    def test_select_repair_error_batch_keeps_original_behavior_when_disabled(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)
        cargo_output = """
error[E0001]: first issue
 --> src/a.rs:1:1

error[E0002]: second issue
 --> src/b.rs:2:1
"""

        grouped = agent._select_repair_error_batch(cargo_output, ".")
        prompt = agent._build_diagnosis_prompt(grouped, "project")

        self.assertIn("src/a.rs", grouped)
        self.assertIn("src/b.rs", grouped)
        self.assertNotIn("Error organization context", prompt)

    def test_select_repair_error_batch_focuses_one_organized_batch_and_explains_context(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            (root / "src").mkdir()
            (root / "src" / "a.rs").write_text("", encoding="utf-8")
            (root / "src" / "b.rs").write_text("", encoding="utf-8")
            agent = RustRepairAgent(
                config=config,
                max_iterations=3,
                error_organizer_agent=ErrorOrganizerAgent(batch_size=1),
            )
            cargo_output = """
error[E0001]: first issue
 --> src/a.rs:1:1

error[E0002]: second issue
 --> src/b.rs:2:1
"""

            grouped = agent._select_repair_error_batch(cargo_output, str(root))
            diagnosis_prompt = agent._build_diagnosis_prompt(grouped, "project")
            edit_prompt = agent._build_edit_prompt(
                str(root),
                {"target_files": list(grouped)},
                grouped,
                [],
                1,
            )

            self.assertEqual(len(grouped), 1)
            self.assertIn("Error organization context", diagnosis_prompt)
            self.assertIn("2 remaining organized batches", diagnosis_prompt)
            self.assertIn("Error organization context", edit_prompt)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_select_repair_error_batch_includes_source_context_excerpt(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            src_dir = root / "src"
            src_dir.mkdir()
            (src_dir / "a.rs").write_text(
                "\n".join(f"line {i}" for i in range(1, 41)) + "\n",
                encoding="utf-8",
            )
            agent = RustRepairAgent(
                config=config,
                max_iterations=3,
                error_organizer_agent=ErrorOrganizerAgent(batch_size=1),
            )
            cargo_output = """
error[E0001]: first issue
 --> src/a.rs:20:1
"""

            grouped = agent._select_repair_error_batch(cargo_output, str(root))
            diagnosis_prompt = agent._build_diagnosis_prompt(grouped, "project")

            self.assertIn("Error organization context", diagnosis_prompt)
            self.assertIn("Active batch source context", diagnosis_prompt)
            self.assertIn("0005 | line 5", diagnosis_prompt)
            self.assertIn("0020 | line 20", diagnosis_prompt)
            self.assertIn("0035 | line 35", diagnosis_prompt)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_optional_pointer_macro_evidence_is_advertised_only_when_enabled(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            c_docs = root / "c_docs"
            c_docs.mkdir()
            pointer_doc = c_docs / "specs" / "001-core-rust-port" / "pointer.md"
            macro_doc = c_docs / "specs" / "001-core-rust-port" / "macro.md"
            pointer_doc.parent.mkdir(parents=True)
            pointer_doc.write_text("# pointer evidence\n", encoding="utf-8")
            macro_doc.write_text("# macro evidence\n", encoding="utf-8")
            enabled = RustRepairAgent(config=config, max_iterations=3)
            enabled.configure_context_sources(
                c_docs_path=str(c_docs),
                use_pointer_agent=True,
                use_macro_agent=True,
            )
            enabled_prompt = enabled._build_diagnosis_prompt({"src/a.rs": "error"}, "project")
            enabled_tools = enabled._build_repair_tool_protocol(str(root))

            disabled = RustRepairAgent(config=config, max_iterations=3)
            disabled.configure_context_sources(c_docs_path=str(c_docs))
            disabled_prompt = disabled._build_diagnosis_prompt({"src/a.rs": "error"}, "project")
            disabled_overview = disabled._build_project_overview(str(root))

            self.assertIn("specs/**/pointer.md", enabled_prompt)
            self.assertIn("specs/**/macro.md", enabled_prompt)
            self.assertIn("module-local evidence remains authoritative", enabled_tools)
            self.assertNotIn("specs/**/pointer.md", disabled_prompt)
            self.assertNotIn("specs/**/macro.md", disabled_prompt)
            self.assertNotIn("pointer.md", disabled_overview)
            self.assertNotIn("macro.md", disabled_overview)
            self.assertEqual(
                disabled._read_context_file_slice(
                    str(root),
                    "spec",
                    "specs/001-core-rust-port/pointer.md",
                ),
                "",
            )
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_apply_structured_edits_updates_only_target_range(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            target = root / "sample.rs"
            target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

            applied = agent._apply_structured_edits(str(root), [
                {
                    "path": "sample.rs",
                    "mode": "replace_range",
                    "start_line": 2,
                    "end_line": 3,
                    "content": "middle_a\nmiddle_b\n",
                }
            ])

            self.assertTrue(applied)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "line1\nmiddle_a\nmiddle_b\nline4\n",
            )
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_apply_structured_edits_supports_delete_and_insert(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            target = root / "sample.rs"
            target.write_text("a\nb\nc\nd\n", encoding="utf-8")

            applied = agent._apply_structured_edits(str(root), [
                {
                    "path": "sample.rs",
                    "mode": "delete_range",
                    "start_line": 2,
                    "end_line": 3,
                },
                {
                    "path": "sample.rs",
                    "mode": "insert_before",
                    "before_line": 2,
                    "content": "x\n",
                },
            ])

            self.assertTrue(applied)
            self.assertEqual(target.read_text(encoding="utf-8"), "a\nx\nd\n")
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_insert_after_accepts_start_line_fallback(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            target = root / "sample.rs"
            target.write_text("a\nb\n", encoding="utf-8")

            applied = agent._apply_structured_edits(str(root), [
                {
                    "path": "sample.rs",
                    "mode": "insert_after",
                    "start_line": 1,
                    "content": "x\n",
                }
            ])

            self.assertTrue(applied)
            self.assertEqual(target.read_text(encoding="utf-8"), "a\nx\nb\n")
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_insert_before_accepts_target_line_alias(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            target = root / "sample.rs"
            target.write_text("line1\nline2\nline3\n", encoding="utf-8")

            applied, records = agent._apply_structured_edits_with_audit(
                str(root),
                [
                    {
                        "path": "sample.rs",
                        "mode": "insert_before",
                        "target_line": 3,
                        "content": "inserted\n",
                    }
                ],
            )

            self.assertTrue(applied)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "line1\nline2\ninserted\nline3\n",
            )
            self.assertEqual(records[0]["actual_before_line"], 3)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_copy_range_after_copies_project_file_lines(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            source = root / "source.rs"
            target = root / "target.rs"
            source.write_text(
                "ignore\nconst DATA: &[&str] = &[\n    \"a\",\n    \"b\",\n];\n",
                encoding="utf-8",
            )
            target.write_text("fn main() {\n}\n", encoding="utf-8")

            applied, records = agent._apply_structured_edits_with_audit(
                str(root),
                [
                    {
                        "path": "target.rs",
                        "mode": "copy_range_after",
                        "target_line": 1,
                        "source_path": "source.rs",
                        "source_start_line": 2,
                        "source_end_line": 5,
                    }
                ],
            )

            self.assertTrue(applied)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "fn main() {\nconst DATA: &[&str] = &[\n    \"a\",\n    \"b\",\n];\n}\n",
            )
            self.assertEqual(records[0]["mode"], "copy_range_after")
            self.assertEqual(records[0]["source_path"], "source.rs")
            self.assertEqual(records[0]["actual_source_start_line"], 2)
            self.assertEqual(records[0]["actual_source_end_line"], 5)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_cp_mode_can_copy_from_configured_c_project(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            c_root = root / "c_project"
            rust_root = root / "rust_project"
            c_root.mkdir()
            rust_root.mkdir()
            (c_root / "source.c").write_text(
                "line1\nstatic const char *RTC[] = {\n  \"main\",\n};\nline5\n",
                encoding="utf-8",
            )
            target = rust_root / "src.rs"
            target.write_text("pub fn emit() {\n}\n", encoding="utf-8")
            agent.configure_context_sources(c_project_path=str(c_root))

            applied, records = agent._apply_structured_edits_with_audit(
                str(rust_root),
                [
                    {
                        "path": "src.rs",
                        "mode": "cp",
                        "target_line": 1,
                        "source_kind": "c",
                        "source_path": "source.c",
                        "source_start_line": 2,
                        "source_end_line": 4,
                    }
                ],
            )

            self.assertTrue(applied)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "pub fn emit() {\nstatic const char *RTC[] = {\n  \"main\",\n};\n}\n",
            )
            self.assertEqual(records[0]["mode"], "cp")
            self.assertEqual(records[0]["source_kind"], "c")
            self.assertEqual(records[0]["source_path"], "source.c")
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_copy_c_string_array_after_converts_c_strings(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            c_root = root / "c_project"
            rust_root = root / "rust_project"
            c_root.mkdir()
            rust_root.mkdir()
            (c_root / "source.c").write_text(
                "static char *OTHER[] = { \"skip\\n\" };\n"
                "static char *RTC[] = {\n"
                "  \"int main(void) {\\n\",\n"
                "  \"  return 0;\\n\",\n"
                "  \"}\\n\",\n"
                "  0\n"
                "};\n",
                encoding="utf-8",
            )
            target = rust_root / "src.rs"
            target.write_text("mod generated;\n", encoding="utf-8")
            agent.configure_context_sources(c_project_path=str(c_root))

            applied, records = agent._apply_structured_edits_with_audit(
                str(rust_root),
                [
                    {
                        "path": "src.rs",
                        "mode": "copy_c_string_array_after",
                        "target_line": 1,
                        "source_path": "source.c",
                        "source_start_line": 1,
                        "source_end_line": 99,
                        "array_name": "RTC",
                        "constant_name": "RTC_LINES",
                    }
                ],
            )

            content = target.read_text(encoding="utf-8")
            self.assertTrue(applied)
            self.assertIn("static RTC_LINES: &[&str] = &[", content)
            self.assertIn('"int main(void) {\\n",', content)
            self.assertIn('"  return 0;\\n",', content)
            self.assertNotIn("skip", content)
            self.assertEqual(records[0]["actual_source_end_line"], 7)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_apply_structured_edits_with_audit_updates_remaining_positions_for_same_file(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            target = root / "sample.rs"
            target.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

            applied, records = agent._apply_structured_edits_with_audit(
                str(root),
                [
                    {
                        "path": "sample.rs",
                        "mode": "delete_range",
                        "start_line": 2,
                        "end_line": 2,
                    },
                    {
                        "path": "sample.rs",
                        "mode": "insert_after",
                        "after_line": 4,
                        "content": "x\n",
                    },
                    {
                        "path": "sample.rs",
                        "mode": "replace_range",
                        "start_line": 5,
                        "end_line": 5,
                        "content": "z\n",
                    },
                ],
            )

            self.assertTrue(applied)
            self.assertEqual(target.read_text(encoding="utf-8"), "a\nc\nd\nx\nz\n")
            self.assertEqual(len(records), 3)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_apply_structured_edits_with_audit_allows_large_replace_range(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            target = root / "sample.rs"
            original = [f"line{i}\n" for i in range(1, 221)]
            target.write_text("".join(original), encoding="utf-8")

            replacement = "".join(f"new{i}\n" for i in range(1, 171))
            applied, records = agent._apply_structured_edits_with_audit(
                str(root),
                [
                    {
                        "path": "sample.rs",
                        "mode": "replace_range",
                        "start_line": 21,
                        "end_line": 190,
                        "content": replacement,
                    }
                ],
            )

            self.assertTrue(applied)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["mode"], "replace_range")
            text = target.read_text(encoding="utf-8")
            self.assertIn("line20\nnew1\nnew2\n", text)
            self.assertIn("new170\nline191\n", text)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_append_repair_record_writes_jsonl_journal(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            journal_path = root / "repair_journal.jsonl"
            agent._append_repair_record(
                str(journal_path),
                {
                    "iteration": 1,
                    "target_files": ["src/lib.rs"],
                    "result": {"error_count": 3},
                },
            )

            text = journal_path.read_text(encoding="utf-8")
            self.assertIn("\"iteration\": 1", text)
            self.assertIn("\"src/lib.rs\"", text)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_should_accept_result_when_syntax_blocker_is_removed_even_if_error_count_grows(self):
        config = Config(config_path=None, model_name="qwen32")
        agent = RustRepairAgent(config=config, max_iterations=3)

        current_output = """error: this file contains an unclosed delimiter
 --> src\\avl_bf.rs:1044:51
"""
        candidate_output = """error[E0432]: unresolved import `rand`
 --> src\\avl_data.rs:171:9
error[E0599]: no method named `foo`
 --> src\\avl_bf.rs:200:5
"""

        current = RepairRunResult(
            run_dir="run-a",
            check_passed=False,
            test_passed=False,
            error_count=1,
            output=current_output,
            error_signature=agent._error_signature(current_output),
            frontier_metrics=agent._frontier_metrics(current_output),
        )
        candidate = RepairRunResult(
            run_dir="run-b",
            check_passed=False,
            test_passed=False,
            error_count=2,
            output=candidate_output,
            error_signature=agent._error_signature(candidate_output),
            frontier_metrics=agent._frontier_metrics(candidate_output),
        )

        accepted, reason = agent._should_accept_result(current, candidate)
        self.assertTrue(accepted)
        self.assertIn("syntax blocker", reason)

    def test_materialize_search_requests_collects_keyword_hits_with_locations(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            project = root / "project"
            project.mkdir()
            (project / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "lib.rs").write_text(
                "pub fn rotate_right() {}\nfn helper() { rotate_right(); }\n",
                encoding="utf-8",
            )
            (project / "src" / "tree.rs").write_text(
                "pub fn rotate_left() {}\n",
                encoding="utf-8",
            )

            agent = RustRepairAgent(config=config, max_iterations=3)
            materials = agent._materialize_search_requests(
                str(project),
                [
                    {
                        "query": "rotate_right",
                        "path_glob": "src/*.rs",
                        "context_lines": 0,
                        "max_results": 5,
                    }
                ],
            )

            self.assertTrue(materials)
            self.assertEqual(materials[0]["mode"], "search_results")
            self.assertIn("rotate_right", materials[0]["content"])
            self.assertIn("src/lib.rs:1", materials[0]["content"])
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_materialize_read_requests_returns_full_file_when_unbounded(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            project = root / "project"
            project.mkdir()
            (project / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (project / "src").mkdir()
            content = "".join(f"line{i}: {'x' * 80}\n" for i in range(1, 201))
            (project / "src" / "lib.rs").write_text(content, encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=3)
            materials = agent._materialize_read_requests(
                str(project),
                [
                    {
                        "kind": "rust",
                        "path": "src/lib.rs",
                        "mode": "whole_file",
                    }
                ],
                max_chars=None,
            )

            self.assertEqual(len(materials), 1)
            self.assertEqual(materials[0]["content"], content)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_materialize_search_requests_does_not_clamp_requested_hits(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            project = root / "project"
            project.mkdir()
            (project / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (project / "src").mkdir()
            needle_lines = "\n".join(f"needle line {i}" for i in range(1, 31)) + "\n"
            (project / "src" / "lib.rs").write_text(needle_lines, encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=3)
            materials = agent._materialize_search_requests(
                str(project),
                [
                    {
                        "kind": "rust",
                        "query": "needle",
                        "path_glob": "src/*.rs",
                        "context_lines": 0,
                        "max_results": 25,
                    }
                ],
                max_chars=None,
            )

            self.assertEqual(len(materials), 1)
            content = materials[0]["content"]
            self.assertIn(">rust:src/lib.rs:25:", content)
            self.assertNotIn(">rust:src/lib.rs:26:", content)
            self.assertEqual(content.count(">rust:src/lib.rs:"), 25)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_run_single_iteration_keeps_repairing_within_same_round_until_complete(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            source = root / "source"
            source.mkdir()
            (source / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (source / "src").mkdir()
            (source / "src" / "lib.rs").write_text("pub fn demo() {}\n", encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=3)
            agent.iteration_timeout_seconds = 600
            agent._monotonic = lambda: 0
            agent._maybe_rebuild_lib_rs = lambda *args, **kwargs: False
            agent._cargo_test = lambda *_: (True, "")

            cargo_check_results = iter([
                (False, "error[E0001]: first\n --> src/lib.rs:1:1"),
                (False, "error[E0001]: first\n --> src/lib.rs:1:1"),
                (False, "error[E0002]: second\n --> src/lib.rs:2:1"),
            ])
            agent._cargo_check = lambda *_: next(cargo_check_results)
            agent._request_diagnosis_plan = lambda grouped, overview, handoff_summary="": {
                "summary": "先修 lib.rs",
                "target_files": ["src/lib.rs"],
                "read_requests": [{"path": "src/lib.rs", "mode": "whole_file"}],
                "edit_strategy": "局部替换",
                "reasoning": ["测试轮内继续"],
            }

            seen_grouped_errors = []
            structured_actions = iter([
                {
                    "summary": "先做一次局部修复",
                    "updated_summary": "已在 lib.rs 插入 first edit，等待新的编译结果",
                    "edits": [
                        {
                            "path": "src/lib.rs",
                            "mode": "insert_after",
                            "start_line": 1,
                            "content": "// first edit",
                        }
                    ],
                    "more_read_requests": [],
                    "search_requests": [
                        {
                            "query": "demo",
                            "path_glob": "src/*.rs",
                            "context_lines": 0,
                            "max_results": 3,
                        }
                    ],
                    "complete": False,
                },
                {
                    "summary": "看到新的编译结果后，本轮先结束",
                    "updated_summary": "第一处修改已应用，新的错误已经暴露，可以进入下一轮",
                    "edits": [],
                    "more_read_requests": [],
                    "search_requests": [],
                    "complete": True,
                },
            ])

            def fake_request_structured_edits(project_dir, diagnosis_plan, grouped_errors, materials, cycle_index, current_summary="", handoff_summary=""):
                del project_dir, diagnosis_plan, materials, current_summary, handoff_summary
                seen_grouped_errors.append((cycle_index, dict(grouped_errors)))
                return next(structured_actions)

            agent._request_structured_edits = fake_request_structured_edits

            result = agent._run_single_iteration(str(source), str(root / "runs"), 1)

            self.assertFalse(result.check_passed)
            self.assertEqual(len(seen_grouped_errors), 2)
            self.assertIn("first", next(iter(seen_grouped_errors[0][1].values())))
            self.assertIn("second", next(iter(seen_grouped_errors[1][1].values())))
            self.assertIn("新的错误已经暴露", result.round_summary)

            journal_path = Path(result.run_dir) / "repair_journal.jsonl"
            records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(any(r.get("stage") == "llm_cycle_complete" for r in records))
            self.assertTrue(any(r.get("stage") == "llm_search_context" for r in records))
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_run_single_iteration_refreshes_diagnosis_when_organized_batch_changes(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            source = root / "source"
            source.mkdir()
            (source / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (source / "src").mkdir()
            (source / "src" / "a.rs").write_text("pub fn a() {}\n", encoding="utf-8")
            (source / "src" / "b.rs").write_text("pub fn b() {}\n", encoding="utf-8")

            first_output = """
error[E0001]: first issue
 --> src/a.rs:1:1

error[E0002]: second issue
 --> src/b.rs:1:1
"""
            second_output = """
error[E0002]: second issue
 --> src/b.rs:1:1
"""
            agent = RustRepairAgent(
                config=config,
                max_iterations=1,
                error_organizer_agent=ErrorOrganizerAgent(batch_size=1),
            )
            agent._monotonic = lambda: 0
            agent._cargo_test = lambda *_: (True, "")
            results = iter([(False, first_output), (False, first_output), (False, second_output)])
            agent._cargo_check = lambda *_: next(results)
            diagnosis_calls = []

            def fake_diagnosis(grouped, _overview, _handoff_summary=""):
                diagnosis_calls.append(dict(grouped))
                return {
                    "summary": "diagnose active batch",
                    "target_files": list(grouped),
                    "read_requests": [],
                    "search_requests": [],
                }

            agent._request_diagnosis_plan = fake_diagnosis
            actions = iter(
                [
                    {
                        "summary": "fix first batch",
                        "edits": [
                            {
                                "path": "src/a.rs",
                                "mode": "insert_after",
                                "start_line": 1,
                                "content": "// repaired\n",
                            }
                        ],
                        "more_read_requests": [],
                        "search_requests": [],
                        "complete": False,
                    },
                    {
                        "summary": "stop after seeing second batch",
                        "edits": [],
                        "more_read_requests": [],
                        "search_requests": [],
                        "complete": True,
                    },
                ]
            )
            agent._request_structured_edits = lambda *args, **kwargs: next(actions)

            result = agent._run_single_iteration(str(source), str(root / "runs"), 1)

            self.assertFalse(result.check_passed)
            self.assertEqual(len(diagnosis_calls), 2)
            self.assertIn("src/a.rs", diagnosis_calls[0])
            self.assertIn("src/b.rs", diagnosis_calls[1])
            records = [
                json.loads(line)
                for line in (Path(result.run_dir) / "repair_journal.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(r.get("stage") == "organized_error_batch_switched" for r in records))
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_run_single_iteration_records_round_timeout(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            source = root / "source"
            source.mkdir()
            (source / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (source / "src").mkdir()
            (source / "src" / "lib.rs").write_text("pub fn demo() {}\n", encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=3)
            agent.iteration_timeout_seconds = 10
            monotonic_values = iter([0, 0, 11, 11, 11])
            agent._monotonic = lambda: next(monotonic_values)
            agent._maybe_rebuild_lib_rs = lambda *args, **kwargs: False
            agent._cargo_test = lambda *_: (True, "")
            agent._cargo_check = lambda *_: (False, "error[E0001]: first\n --> src/lib.rs:1:1")
            agent._request_diagnosis_plan = lambda grouped, overview, handoff_summary="": {
                "summary": "读取更多上下文",
                "target_files": ["src/lib.rs"],
                "read_requests": [{"path": "src/lib.rs", "mode": "whole_file"}],
                "edit_strategy": "先读后改",
                "reasoning": ["测试超时"],
            }
            agent._request_structured_edits = lambda *args, **kwargs: {
                "summary": "继续读取，不结束本轮",
                "edits": [],
                "more_read_requests": [{"path": "src/lib.rs", "mode": "whole_file"}],
                "complete": False,
            }

            result = agent._run_single_iteration(str(source), str(root / "runs"), 1)

            self.assertFalse(result.check_passed)
            journal_path = Path(result.run_dir) / "repair_journal.jsonl"
            records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(any(r.get("stage") == "round_timeout" for r in records))
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_run_single_iteration_can_repair_in_place_without_cloning(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            project = root / "project"
            project.mkdir()
            (project / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "lib.rs").write_text("pub fn demo() {}\n", encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=3)
            agent._clone_project_tree = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not clone"))
            agent._cargo_check = lambda *_: (True, "")
            agent._cargo_test = lambda *_: (True, "")

            result = agent._run_single_iteration(str(project), str(root / "runs"), 1, in_place=True)

            self.assertEqual(Path(result.run_dir).resolve(), project.resolve())
            self.assertFalse((root / "runs").exists())
            self.assertTrue(result.check_passed)
            self.assertTrue(result.test_passed)
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_repair_project_keeps_baseline_when_worse_run_is_rejected_and_passes_handoff_summary(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            project = root / "project"
            project.mkdir()
            (project / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "lib.rs").write_text("pub fn demo() {}\n", encoding="utf-8")

            run1 = root / "run1"
            run2 = root / "run2"
            run1.mkdir()
            run2.mkdir()

            agent = RustRepairAgent(config=config, max_iterations=2)
            agent._cargo_check = lambda *_: (False, "error: baseline\n --> src/lib.rs:1:1")
            calls = []
            results = iter([
                RepairRunResult(
                    run_dir=str(run1),
                    check_passed=False,
                    test_passed=False,
                    error_count=50,
                    output="error: worse\n --> src/lib.rs:2:1",
                    error_signature="worse",
                    frontier_metrics={
                        "syntax_blockers": 1,
                        "interface_blockers": 0,
                        "total_errors": 50,
                        "signature": "worse",
                    },
                    round_summary="第一轮修改暴露了更多接口错误",
                    timed_out=True,
                ),
                RepairRunResult(
                    run_dir=str(run2),
                    check_passed=False,
                    test_passed=False,
                    error_count=1,
                    output="error: better\n --> src/lib.rs:3:1",
                    error_signature="better",
                    frontier_metrics={
                        "syntax_blockers": 0,
                        "interface_blockers": 1,
                        "total_errors": 1,
                        "signature": "better",
                    },
                    round_summary="第二轮已聚焦更深层问题",
                    timed_out=False,
                ),
            ])

            def fake_run_single_iteration(baseline_dir, runs_root, iteration, handoff_summary="", in_place=False):
                calls.append({
                    "baseline_dir": baseline_dir,
                    "runs_root": runs_root,
                    "iteration": iteration,
                    "handoff_summary": handoff_summary,
                    "in_place": in_place,
                })
                return next(results)

            agent._run_single_iteration = fake_run_single_iteration
            agent._request_handoff_summary = lambda previous_handoff, baseline_output, candidate_output, candidate_summary, accepted_as_best: (
                f"handoff::{candidate_summary}::accepted={accepted_as_best}"
            )

            best = agent.repair_project(str(project), runs_root=str(root / "runs"), apply_best=False, in_place=False)

            self.assertEqual(best.run_dir, str(run2))
            self.assertEqual(len(calls), 2)
            self.assertEqual(Path(calls[0]["baseline_dir"]).resolve(), project.resolve())
            self.assertEqual(Path(calls[1]["baseline_dir"]).resolve(), project.resolve())
            self.assertIn("第一轮修改暴露了更多接口错误", calls[1]["handoff_summary"])
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_repair_project_defaults_to_in_place(self):
        config = Config(config_path=None, model_name="qwen32")
        root = Path(__file__).parent / f"_tmp_rust_repair_agent_{uuid.uuid4().hex}"
        root.mkdir()
        try:
            project = root / "project"
            project.mkdir()
            (project / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "lib.rs").write_text("pub fn demo() {}\n", encoding="utf-8")

            agent = RustRepairAgent(config=config, max_iterations=1)
            agent._cargo_check = lambda *_: (False, "error: baseline\n --> src/lib.rs:1:1")
            calls = []

            def fake_run_single_iteration(baseline_dir, runs_root, iteration, handoff_summary="", in_place=False):
                calls.append({
                    "baseline_dir": baseline_dir,
                    "runs_root": runs_root,
                    "iteration": iteration,
                    "handoff_summary": handoff_summary,
                    "in_place": in_place,
                })
                return RepairRunResult(
                    run_dir=str(project),
                    check_passed=True,
                    test_passed=True,
                    error_count=0,
                    output="",
                )

            agent._run_single_iteration = fake_run_single_iteration
            agent._request_handoff_summary = lambda *args, **kwargs: ""

            best = agent.repair_project(str(project))

            self.assertEqual(Path(best.run_dir).resolve(), project.resolve())
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["in_place"])
            self.assertEqual(Path(calls[0]["baseline_dir"]).resolve(), project.resolve())
        finally:
            if root.exists():
                try:
                    shutil.rmtree(root)
                except PermissionError:
                    pass

    def test_main_optional_rust_repair_agent_runs_in_place(self):
        import importlib

        main_module = importlib.import_module("agent.main")
        config = Config(config_path=None, model_name="qwen32")
        args = SimpleNamespace(use_rust_repair_agent=True, rust_repair_max_iterations=7)
        calls = []

        organizer = object()

        class FakeRepairAgent:
            def __init__(self, config, max_iterations, error_organizer_agent=None):
                calls.append(("init", config, max_iterations, error_organizer_agent))

            def configure_context_sources(self, **kwargs):
                calls.append(("configure_context_sources", kwargs))

            def repair_project(self, **kwargs):
                calls.append(("repair_project", kwargs))
                return RepairRunResult(
                    run_dir=kwargs["project_path"],
                    check_passed=True,
                    test_passed=True,
                    error_count=0,
                    output="",
                )

        original_agent = getattr(main_module, "RustRepairAgent", None)
        try:
            main_module.RustRepairAgent = FakeRepairAgent
            result = main_module.run_optional_rust_repair_agent(
                args,
                config,
                "target-project",
                error_organizer_agent=organizer,
            )
        finally:
            if original_agent is None:
                delattr(main_module, "RustRepairAgent")
            else:
                main_module.RustRepairAgent = original_agent

        self.assertTrue(result.check_passed)
        self.assertEqual(calls[0], ("init", config, 7, organizer))
        self.assertEqual(calls[1][0], "configure_context_sources")
        self.assertEqual(calls[2][0], "repair_project")
        self.assertEqual(calls[2][1]["project_path"], "target-project")
        self.assertTrue(calls[2][1]["in_place"])
        self.assertFalse(calls[2][1]["apply_best"])


if __name__ == "__main__":
    unittest.main()
