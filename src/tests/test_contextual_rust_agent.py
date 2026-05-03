import json
import shutil
import sys
import unittest
import uuid
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import main as main_module
from agent.alternatives.contextual_rust_agent import (
    ContextualRustAgent,
    PlannedFile,
    RustProjectRegistry,
    SpecDocumentIndex,
)
from agent.alternatives.rust_generation_spec_agent import RustGenerationSpecAgent, RustGenerationSpecPrompts
from agent.code_fixer_agent import CodeFixer
from config.config import Config


class FakeModel:
    def __init__(self):
        self.label = ""

    def set_request_label(self, label: str):
        self.label = label

    def generate(self, messages):
        if "项目规划" in self.label:
            return [
                """
<CGR_PLAN>
{
  "files": [
    {"path":"Cargo.toml"},
    {"path":"src/core.rs", "role":"core type", "owns":["Core"]},
    {"path":"src/lib.rs"},
    {"path":"README.md"}
  ],
  "order": ["Cargo.toml", "src/core.rs", "src/lib.rs", "README.md"]
}
</CGR_PLAN>
"""
            ]
        if "代码生成 src/core.rs" in self.label:
            return ["pub struct Core;\n<CGR_DONE>"]
        if "代码生成 README.md" in self.label:
            return ["# demo\n\nGenerated README.\n<CGR_DONE>"]
        raise AssertionError(f"unexpected LLM label: {self.label}")


class ForceWriteModel:
    def __init__(self):
        self.label = ""

    def set_request_label(self, label: str):
        self.label = label

    def generate(self, messages):
        bad_code = "pub fn c_new() -> *mut i32 {\n    core::ptr::null_mut()\n}\n"
        if "代码生成 src/unsafe_api.rs" in self.label:
            return [bad_code + "<CGR_DONE>"]
        if "边界修复 src/unsafe_api.rs" in self.label:
            return [bad_code + "<CGR_DONE>"]
        if "强制写入确认 src/unsafe_api.rs" in self.label:
            return [
                bad_code
                + "<CGR_FORCE_WRITE>\n"
                + "registry lint is intentionally overridden for this migration step.\n"
                + "</CGR_FORCE_WRITE>\n"
                + "<CGR_DONE>"
            ]
        raise AssertionError(f"unexpected LLM label: {self.label}")


class CapturePromptModel:
    def __init__(self, response: str):
        self.label = ""
        self.response = response
        self.messages = []

    def set_request_label(self, label: str):
        self.label = label

    def generate(self, messages):
        self.messages = messages
        return [self.response]


class PromptCaptureCodeFixer(CodeFixer):
    def __init__(self, config: Config, project_path: str):
        super().__init__(config, project_path)
        self.fixed_file = ""

    def _fix_file(self, file_path: str, error_type: str, error_message: str, prefer_local: bool = True) -> bool:
        self.fixed_file = file_path
        return True


class ContextualRustAgentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f"_tmp_contextual_rust_agent_{uuid.uuid4().hex}"
        self.root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def _write_doc(self, rel_path: str, content: str) -> Path:
        path = self.root / "c_docs" / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")
        return path

    def test_spec_index_selects_relevant_module_docs_without_full_project_context(self):
        manifest = self._write_doc(
            "docs/rewrite-context/00_repo_manifest.md",
            """
# generic 仓库清单
## 源文件清单
- `src/bounds.c`
- `src/tree.c`
""",
        )
        bounds = self._write_doc(
            "docs/rewrite-context/02_interfaces/002_module_src.md",
            """
# 接口事实：module_src
### `quadtree_bounds_new`
- 源文件：`src/bounds.c`
### `quadtree_bounds_extend`
- 源文件：`src/bounds.c`
""",
        )
        main = self._write_doc(
            "specs/001-main_root-rust-port/spec.md",
            """
# main_root
benchmark runner and top-level driver only.
""",
        )

        index = SpecDocumentIndex(
            {
                str(manifest): manifest.read_text(encoding="utf-8"),
                str(bounds): bounds.read_text(encoding="utf-8"),
                str(main): main.read_text(encoding="utf-8"),
            }
        )

        selected = index.select_for_file("src/bounds.rs", owns=["Bounds"], spec_queries=["bounds"])

        self.assertIn("quadtree_bounds_new", selected)
        self.assertIn("00_repo_manifest.md", selected)
        self.assertNotIn("benchmark runner", selected)

    def test_spec_index_infers_generic_rust_files_from_source_manifest(self):
        manifest = self._write_doc(
            "docs/rewrite-context/00_repo_manifest.md",
            """
# generic 仓库清单
## 源文件清单
- `src/bounds.c`
- `src/node.c`
- `src/main.c`
- `test.c`
""",
        )
        index = SpecDocumentIndex({str(manifest): manifest.read_text(encoding="utf-8")})

        files = index.infer_candidate_rust_files()

        self.assertIn("Cargo.toml", files)
        self.assertIn("src/bounds.rs", files)
        self.assertIn("src/node.rs", files)
        self.assertIn("src/main.rs", files)
        self.assertIn("tests/test.rs", files)
        self.assertIn("src/lib.rs", files)

    def test_rust_generation_spec_agent_builds_distinct_file_contexts(self):
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            """
# 接口事实：module_src
### `quadtree_bounds_new`
- 源文件：`src/bounds.c`
- 观察到的声明：`quadtree_bounds_t* quadtree_bounds_new(void);`

### `quadtree_bounds_extend`
- 源文件：`src/bounds.c`
- 观察到的声明：`void quadtree_bounds_extend(quadtree_bounds_t *bounds, double x, double y);`

### `quadtree_node_new`
- 源文件：`src/node.c`
- 观察到的声明：`quadtree_node_t* quadtree_node_new(void);`

### `quadtree_node_isleaf`
- 源文件：`src/node.c`
- 观察到的声明：`int quadtree_node_isleaf(quadtree_node_t *node);`
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})
        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/bounds.rs", "src/node.rs"])}

        bounds_context = spec_agent.context_for_file(plan_by_path["src/bounds.rs"])
        node_context = spec_agent.context_for_file(plan_by_path["src/node.rs"])

        self.assertIn("quadtree_bounds_new", bounds_context)
        self.assertIn("quadtree_bounds_extend", bounds_context)
        self.assertNotIn("quadtree_node_isleaf", bounds_context)
        self.assertIn("quadtree_node_new", node_context)
        self.assertNotIn("quadtree_bounds_extend", node_context)

    def test_rust_generation_spec_agent_context_includes_generation_prompt_guide(self):
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            """
# 接口事实：module_src
### `quadtree_bounds_new`
- 源文件：`src/bounds.c`
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})
        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/bounds.rs"])}

        context = spec_agent.context_for_file(plan_by_path["src/bounds.rs"])

        self.assertIn("RUST GENERATION CONTEXT GUIDE", context)
        self.assertIn("translation_contract.json", context)
        self.assertIn("目标是行为等价，不是 C ABI 等价", context)
        self.assertIn("<CGR_READ>", context)

    def test_rust_generation_spec_agent_context_extracts_local_excerpt_instead_of_full_doc(self):
        repeated_noise = "\n".join(f"- unrelated benchmark detail line {i}" for i in range(80))
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            f"""
# 接口事实：module_src
## bounds area
### `quadtree_bounds_new`
- 源文件：`src/bounds.c`
- 观察到的声明：`quadtree_bounds_t* quadtree_bounds_new(void);`
- 语义：create bounds object

{repeated_noise}

## node area
### `quadtree_node_new`
- 源文件：`src/node.c`
- 观察到的声明：`quadtree_node_t* quadtree_node_new(void);`
- 语义：create node object

{repeated_noise}
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})
        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/bounds.rs"])}

        context = spec_agent.context_for_file(plan_by_path["src/bounds.rs"])

        self.assertIn("quadtree_bounds_new", context)
        self.assertNotIn("quadtree_node_new", context)
        self.assertLess(len(context), 5000)
        self.assertNotIn("[截断]", context)
        self.assertNotIn("预算已满", context)

    def test_rust_generation_spec_agent_keeps_full_required_block_without_char_truncation(self):
        long_relevant_detail = "\n".join(f"- relevant detail line {i}" for i in range(180))
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            f"""
# 接口事实：module_src
## bounds area
### `quadtree_bounds_new`
- 源文件：`src/bounds.c`
- 观察到的声明：`quadtree_bounds_t* quadtree_bounds_new(void);`
- 语义：create bounds object
{long_relevant_detail}
- tail sentinel for bounds block

## node area
### `quadtree_node_new`
- 源文件：`src/node.c`
- 观察到的声明：`quadtree_node_t* quadtree_node_new(void);`
- 语义：create node object
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})
        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/bounds.rs"])}

        context = spec_agent.context_for_file(plan_by_path["src/bounds.rs"])

        self.assertIn("quadtree_bounds_new", context)
        self.assertIn("tail sentinel for bounds block", context)
        self.assertNotIn("[截断]", context)
        self.assertNotIn("预算已满", context)

    def test_rust_generation_spec_agent_plans_rust_target_symbols_not_c_names(self):
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            """
# 接口事实：module_src
### `quadtree_bounds_new`
- 源文件：`src/bounds.c`
- 观察到的声明：`quadtree_bounds_t* quadtree_bounds_new(void);`

### `quadtree_bounds_extend`
- 源文件：`src/bounds.c`
- 观察到的声明：`void quadtree_bounds_extend(quadtree_bounds_t *bounds, double x, double y);`
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})
        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/bounds.rs"])}

        owns = plan_by_path["src/bounds.rs"].owns

        self.assertIn("Bounds", owns)
        self.assertIn("Bounds::new", owns)
        self.assertIn("Bounds::extend", owns)
        self.assertNotIn("quadtree_bounds_new", owns)
        self.assertNotIn("quadtree_bounds_extend", owns)

    def test_rust_generation_spec_agent_prefers_source_module_name_for_target_type(self):
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            """
# 接口事实：module_src
### `node_contains_`
- 源文件：`src/quadtree.c`
- 观察到的声明：`int node_contains_(quadtree_node_t *node, double x, double y);`

### `quadtree_new`
- 源文件：`src/quadtree.c`
- 观察到的声明：`quadtree_t* quadtree_new(double minx, double miny, double maxx, double maxy);`
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})
        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/quadtree.rs"])}

        owns = plan_by_path["src/quadtree.rs"].owns

        self.assertIn("Quadtree", owns)
        self.assertIn("Quadtree::new", owns)
        self.assertNotIn("QuadtreeNode", owns)

    def test_rust_generation_spec_agent_ingests_translation_contract_facts(self):
        contract = {
            "project": {"kind": "library"},
            "files": [
                {"path": "src/bounds.c", "role": "source"},
                {"path": "include/bounds.h", "role": "header"},
            ],
            "functions": [
                {"name": "bounds_new", "file": "src/bounds.c"},
                {"name": "bounds_free", "file": "src/bounds.c"},
            ],
            "types": [
                {"name": "bounds_t", "file": "include/bounds.h"},
            ],
            "generation_boundary": {
                "allowed_rust_files": ["Cargo.toml", "src/bounds.rs", "src/lib.rs", "README.md"],
                "dependency_policy": "std_only_by_default",
            },
        }
        spec_agent = RustGenerationSpecAgent(doc_contents={}, translation_contract=contract)

        plan_by_path = {item.path: item for item in spec_agent.build_file_plan(["src/bounds.rs"])}

        self.assertIn("src/bounds.c", spec_agent.source_files)
        self.assertIn("Bounds::new", plan_by_path["src/bounds.rs"].owns)
        self.assertIn("Drop for Bounds", plan_by_path["src/bounds.rs"].owns)
        self.assertIn("bounds_new", plan_by_path["src/bounds.rs"].source_functions)

    def test_rust_generation_spec_agent_prefers_bottom_up_order(self):
        interface = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_module_src.md",
            """
# 接口事实：module_src
### `point_new`
- 源文件：`src/point.c`
### `bounds_new`
- 源文件：`src/bounds.c`
### `node_new`
- 源文件：`src/node.c`
### `tree_insert`
- 源文件：`src/quadtree.c`
""",
        )
        spec_agent = RustGenerationSpecAgent({str(interface): interface.read_text(encoding="utf-8")})

        paths = [
            item.path
            for item in spec_agent.build_file_plan(
                ["README.md", "src/lib.rs", "src/quadtree.rs", "src/node.rs", "src/bounds.rs", "src/point.rs", "Cargo.toml"]
            )
        ]

        self.assertEqual(paths[0], "Cargo.toml")
        self.assertLess(paths.index("src/node.rs"), paths.index("src/quadtree.rs"))
        self.assertLess(paths.index("src/quadtree.rs"), paths.index("src/lib.rs"))
        self.assertEqual(paths[-1], "README.md")

    def test_registry_detects_duplicate_owned_symbols_across_files(self):
        registry = RustProjectRegistry()
        registry.update_file(
            "src/node.rs",
            """
pub struct Node {
    value: i32,
}

pub fn make_node() -> Node {
    Node { value: 1 }
}
""",
        )

        findings = registry.duplicate_findings(
            "src/data.rs",
            """
pub struct Node {
    value: i32,
}
""",
        )

        self.assertTrue(any("重复定义类型 `Node`" in item for item in findings))

    def test_registry_does_not_treat_impl_methods_as_free_functions(self):
        registry = RustProjectRegistry()
        registry.update_file(
            "src/bounds.rs",
            """
pub struct Bounds;

impl Bounds {
    pub fn new() -> Self {
        Self
    }

    pub fn extend(&mut self) {}
}

impl Drop for Bounds {
    fn drop(&mut self) {}
}
""",
        )

        symbols = registry.files["src/bounds.rs"]
        findings = registry.duplicate_findings(
            "src/node.rs",
            """
pub struct Node;

impl Node {
    pub fn new() -> Self {
        Self
    }
}
""",
        )

        self.assertEqual(symbols.functions, [])
        self.assertEqual(symbols.methods["Bounds"], ["new", "extend", "drop"])
        refs_by_name = {ref.display_name(): ref for ref in symbols.references}
        self.assertEqual(refs_by_name["Bounds"].visibility, "public")
        self.assertEqual(refs_by_name["Bounds::new"].visibility, "public")
        self.assertEqual(refs_by_name["Bounds::new"].params, [])
        self.assertEqual(refs_by_name["Bounds::new"].return_type, "Self")
        self.assertEqual(refs_by_name["Bounds::extend"].params, ["&mut self"])
        self.assertEqual(refs_by_name["Bounds::drop"].visibility, "private")
        summary = registry.summary()
        self.assertIn("visibility policy: public unless", summary)
        self.assertIn("method public Bounds::new() -> Self", summary)
        self.assertIn("method private Bounds::drop(&mut self)", summary)
        self.assertFalse(any("重复定义自由函数 `new`" in item for item in findings))
        self.assertFalse(any("重复定义自由函数 `drop`" in item for item in findings))

    def test_registry_records_reference_visibility_and_function_params(self):
        registry = RustProjectRegistry()
        registry.update_file(
            "src/api.rs",
            """
mod inner;
pub(crate) fn shared(value: i32, name: &str) -> bool {
    value > 0 && !name.is_empty()
}

fn local(flag: bool) {
    let _ = flag;
}

pub const LIMIT: usize = 16;
""",
        )

        symbols = registry.files["src/api.rs"]
        refs_by_name = {ref.name: ref for ref in symbols.references if ref.kind != "method"}

        self.assertEqual(refs_by_name["inner"].visibility, "private")
        self.assertEqual(refs_by_name["shared"].visibility, "public")
        self.assertEqual(refs_by_name["shared"].params, ["value: i32", "name: &str"])
        self.assertEqual(refs_by_name["shared"].return_type, "bool")
        self.assertEqual(refs_by_name["local"].visibility, "private")
        self.assertEqual(refs_by_name["local"].params, ["flag: bool"])
        self.assertEqual(refs_by_name["LIMIT"].visibility, "public")
        self.assertEqual(refs_by_name["LIMIT"].return_type, "usize")
        self.assertIn("shared", symbols.all_exportable_items())
        self.assertIn("LIMIT", symbols.all_exportable_items())
        self.assertNotIn("local", symbols.all_exportable_items())

    def test_registry_records_fields_and_rejects_invented_members(self):
        registry = RustProjectRegistry()
        registry.update_file(
            "src/bounds.rs",
            """
pub struct Bounds {
    pub min_x: f64,
    pub max_y: f64,
    max_x: f64,
    min_y: f64,
}

impl Bounds {
    pub fn new() -> Self {
        Self { min_x: 0.0, max_y: 0.0, max_x: 0.0, min_y: 0.0 }
    }
}
""",
        )
        registry.update_file(
            "src/node.rs",
            """
use crate::bounds::Bounds;

pub struct Node {
    pub bounds: Option<Bounds>,
}
""",
        )

        bounds = registry.files["src/bounds.rs"]
        refs = {ref.display_name(): ref for ref in bounds.references}
        self.assertEqual(bounds.fields["Bounds"], ["min_x", "max_y", "max_x", "min_y"])
        self.assertEqual(refs["Bounds::min_x"].visibility, "public")
        self.assertEqual(refs["Bounds::min_x"].return_type, "f64")
        self.assertEqual(refs["Bounds::max_x"].visibility, "private")
        self.assertIn("field public Bounds::min_x: f64", registry.summary())

        findings = registry.reference_findings(
            "src/quadtree.rs",
            """
use crate::bounds::Bounds;
use crate::node::Node;

pub fn contains(node: &Node) -> bool {
    let Some(bounds) = node.bounds.as_ref() else {
        return false;
    };
    bounds.nw.x <= 0.0 && bounds.max_x >= 0.0
}

pub fn make() -> Bounds {
    Bounds::new(0.0, 0.0, 1.0, 1.0)
}
""",
            planned_files=["src/bounds.rs", "src/node.rs", "src/quadtree.rs"],
        )

        self.assertTrue(any("`Bounds` 不存在字段 `nw`" in item for item in findings))
        self.assertTrue(any("private 字段 `Bounds::max_x`" in item for item in findings))
        self.assertTrue(any("方法调用参数不匹配 `Bounds::new`" in item for item in findings))

    def test_registry_flags_unplanned_crate_module_references(self):
        registry = RustProjectRegistry()
        registry.update_file("src/node.rs", "pub struct Node;\n")

        findings = registry.reference_findings(
            "src/tree.rs",
            "use crate::node::Node;\nuse crate::storage::Arena;\n",
            planned_files=["src/node.rs", "src/tree.rs"],
        )

        self.assertFalse(any("crate::node" in item for item in findings))
        self.assertTrue(any("未规划模块 `crate::storage`" in item for item in findings))

    def test_read_request_materialization_is_human_readable(self):
        c_root = self.root / "c_project"
        rust_root = self.root / "rust_project"
        (c_root / "src").mkdir(parents=True)
        (rust_root / "src").mkdir(parents=True)
        (c_root / "src" / "bounds.c").write_text("int bounds_new(void) { return 1; }\n", encoding="utf-8")
        (rust_root / "src" / "bounds.rs").write_text("pub struct Bounds;\n", encoding="utf-8")
        doc = self._write_doc("docs/rewrite-context/02_interfaces/001_bounds.md", "# Bounds\nbounds_new behavior")

        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        agent.project_path = str(rust_root)
        agent.source_project_path = str(c_root)
        agent.source_records = []
        agent.spec_index = SpecDocumentIndex({str(doc): doc.read_text(encoding="utf-8")})
        agent.registry = RustProjectRegistry()
        agent.registry.update_file("src/bounds.rs", "pub struct Bounds;\n")

        requests = agent._parse_read_requests(
            """
<CGR_READ>
[
  {"kind":"spec","query":"bounds"},
  {"kind":"source","path":"src/bounds.c"},
  {"kind":"rust","path":"src/bounds.rs"},
  {"kind":"registry"}
]
</CGR_READ>
"""
        )
        material = agent._materialize_read_requests(requests)

        self.assertIn("READ spec: bounds", material)
        self.assertIn("bounds_new behavior", material)
        self.assertIn("int bounds_new", material)
        self.assertIn("pub struct Bounds", material)
        self.assertIn("types: Bounds", material)

    def test_contextual_plan_respects_allowed_contract_files(self):
        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        agent.allowed_rust_files = ["Cargo.toml", "src/lib.rs", "src/core.rs", "README.md"]

        plan = agent._normalize_plan_payload(
            {
                "files": [
                    {"path": "Cargo.toml"},
                    {"path": "src/core.rs", "owns": ["Core"]},
                    {"path": "src/extra.rs", "owns": ["Extra"]},
                ],
                "order": ["Cargo.toml", "src/extra.rs", "src/core.rs", "src/lib.rs", "README.md"],
            },
            fallback_files=agent.allowed_rust_files,
        )

        paths = [item.path for item in plan]
        self.assertEqual(paths, ["Cargo.toml", "src/core.rs", "src/lib.rs", "README.md"])

    def test_generate_code_uses_registry_not_full_generated_file_context(self):
        doc = self._write_doc(
            "docs/rewrite-context/02_interfaces/001_core.md",
            """
# Core
The core module owns Core only.
""",
        )
        project = self.root / "demo-rust"
        project.mkdir()

        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        agent.llm = FakeModel()
        agent.project_name = "demo-rust"
        agent.project_path = str(project)
        agent.doc_contents = {str(doc): doc.read_text(encoding="utf-8")}
        agent.source_records = []
        agent.source_context_summary = ""
        agent.source_interface_summary = ""
        agent.tool_interface_constraints = ""
        agent.allowed_rust_files = ["Cargo.toml", "src/core.rs", "src/lib.rs", "README.md"]

        generated = agent.generate_code()

        self.assertTrue((project / "Cargo.toml").exists())
        self.assertEqual((project / "src" / "core.rs").read_text(encoding="utf-8"), "pub struct Core;\n")
        lib_rs = (project / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn("pub mod core;", lib_rs)
        self.assertIn("pub use core::Core;", lib_rs)
        api_contract = json.loads((project / ".cgr_api_contract.json").read_text(encoding="utf-8"))
        core_refs = api_contract["files"]["src/core.rs"]["references"]
        self.assertEqual(core_refs[0]["name"], "Core")
        self.assertEqual(core_refs[0]["visibility"], "public")
        self.assertIn("params", core_refs[0])
        self.assertTrue(any(str(path).endswith("src\\core.rs") or str(path).endswith("src/core.rs") for path in generated))

    def test_registry_lib_rs_declares_only_generated_modules_and_reexports_types(self):
        project = self.root / "demo-rust"
        (project / "src").mkdir(parents=True)

        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        agent.project_path = str(project)
        agent.registry = RustProjectRegistry()
        agent.registry.update_file(
            "src/bounds.rs",
            """
pub struct Bounds;

impl Bounds {
    pub fn new() -> Self {
        Self
    }
}
""",
        )

        lib_rs = agent._build_registry_lib_rs(["src/bounds.rs", "src/node.rs", "src/lib.rs"])

        self.assertIn("pub mod bounds;", lib_rs)
        self.assertNotIn("pub mod node;", lib_rs)
        self.assertIn("pub use bounds::Bounds;", lib_rs)
        self.assertNotIn("pub use bounds::new;", lib_rs)

    def test_contextual_lint_rejects_c_abi_style_rust_generation(self):
        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        planned = PlannedFile(
            path="src/bounds.rs",
            owns=["Bounds", "Bounds::new"],
            source_functions=["quadtree_bounds_new"],
        )
        content = """
#[allow(non_camel_case_types)]
pub struct quadtree_bounds_t {
    value: i32,
}

pub fn quadtree_bounds_new() -> *mut quadtree_bounds_t {
    unsafe { Box::into_raw(Box::new(quadtree_bounds_t { value: 0 })) }
}
"""

        findings = agent._lint_contextual_file("src/bounds.rs", content, ["src/bounds.rs"], planned)
        fatal = agent._fatal_contextual_findings(findings)

        self.assertTrue(any("Rust 风格违规" in item and "*mut" in item for item in fatal))
        self.assertTrue(any("Rust 风格违规" in item and "`unsafe`" in item for item in fatal))
        self.assertTrue(any("C ABI 泄漏" in item and "quadtree_bounds_t" in item for item in fatal))
        self.assertTrue(any("C ABI 泄漏" in item and "quadtree_bounds_new" in item for item in fatal))

    def test_force_write_marker_allows_file_after_remaining_fatal_findings(self):
        project = self.root / "demo-rust"
        project.mkdir()

        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        agent.llm = ForceWriteModel()
        agent.project_name = "demo-rust"
        agent.project_path = str(project)
        planned = PlannedFile(
            path="src/unsafe_api.rs",
            role="forced test file",
            source_functions=["c_new"],
        )
        agent._initialize_generation_plan("", "", ["src/unsafe_api.rs"])

        ok, content = agent._generate_contextual_file(planned, ["src/unsafe_api.rs"])

        self.assertTrue(ok)
        self.assertIn("*mut i32", content)
        self.assertTrue((project / "src" / "unsafe_api.rs").exists())
        state = agent.generation_plan["files"]["src/unsafe_api.rs"]
        self.assertEqual(state["status"], "completed")
        self.assertIn("contextual_force_write", state["note"])

    def test_force_write_marker_is_stripped_from_written_content(self):
        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))

        content, force_write, reason = agent._extract_force_write_marker(
            "pub struct X;\n<CGR_FORCE_WRITE>\nneeded for experiment\n</CGR_FORCE_WRITE>\n"
        )

        self.assertTrue(force_write)
        self.assertEqual(content, "pub struct X;")
        self.assertIn("needed for experiment", reason)

    def test_file_prompt_states_c_symbols_are_evidence_not_target_api(self):
        agent = ContextualRustAgent(Config(config_path=None, model_name="qwen32"))
        planned = PlannedFile(
            path="src/bounds.rs",
            role="bounds module",
            owns=["Bounds", "Bounds::new"],
            source_files=["src/bounds.c"],
            source_functions=["quadtree_bounds_new"],
        )

        prompt = agent._build_file_prompt(planned, ["src/bounds.rs"])

        self.assertIn("目标是行为等价，不是 C ABI 等价", prompt)
        self.assertIn("禁止照抄为 Rust API 名", prompt)
        self.assertIn("不要使用 raw pointer", prompt)
        self.assertIn("Bounds::new", prompt)

    def test_rust_generation_prompt_library_centralizes_generation_prompts(self):
        planned = PlannedFile(
            path="src/bounds.rs",
            owns=["Bounds", "Bounds::new"],
            source_functions=["bounds_new"],
        )

        system_prompt = RustGenerationSpecPrompts.file_generation_system_prompt()
        file_prompt = RustGenerationSpecPrompts.file_generation_prompt(
            planned=planned,
            planned_files=["src/bounds.rs", "src/lib.rs"],
            plan_summary="1. src/bounds.rs",
            registry_summary="(empty)",
            spec_context="spec context",
            source_context="source context",
        )

        self.assertIn("禁止 raw pointer", system_prompt)
        self.assertIn("目标是行为等价，不是 C ABI 等价", file_prompt)
        self.assertIn("跨文件只能引用 public 符号", file_prompt)
        self.assertIn("必须匹配符号表中的参数列表和返回类型", file_prompt)
        self.assertIn("访问结构体字段时只能访问 `references` 中存在的 public field", file_prompt)
        self.assertIn("source context", file_prompt)
        self.assertIn("<CGR_READ>", file_prompt)

    def test_code_fixer_summary_includes_structured_references_and_skips_rustc_notes(self):
        project = self.root / "demo-rust"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "bounds.rs").write_text(
            "pub struct Bounds { pub min_x: f64 }\nimpl Bounds { pub fn new() -> Self { Self { min_x: 0.0 } } }\n",
            encoding="utf-8",
        )
        (project / "src" / "quadtree.rs").write_text("pub fn f() {}\n", encoding="utf-8")
        (project / ".cgr_api_contract.json").write_text(
            json.dumps(
                {
                    "files": {
                        "src/bounds.rs": {
                            "contract": {
                                "public_structs": [
                                    {
                                        "name": "Bounds",
                                        "fields": [
                                            {"name": "min_x", "public": True, "type": "f64"},
                                        ],
                                    }
                                ],
                                "references": [
                                    {
                                        "kind": "field",
                                        "visibility": "public",
                                        "owner_type": "Bounds",
                                        "name": "min_x",
                                        "return_type": "f64",
                                        "signature": "Bounds::min_x: f64",
                                    },
                                    {
                                        "kind": "method",
                                        "visibility": "public",
                                        "owner_type": "Bounds",
                                        "name": "new",
                                        "params": [],
                                        "return_type": "Self",
                                        "signature": "Bounds::new() -> Self",
                                    },
                                ],
                            }
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        fixer = CodeFixer(Config(config_path=None, model_name="qwen32"), str(project))

        summary = fixer._load_api_contract_summary()

        self.assertIn("min_x(pub:f64)", summary)
        self.assertIn("reference kind=field; visibility=public", summary)
        self.assertIn("owner_type=Bounds; name=min_x; params=[]; return_type=f64; signature=Bounds::min_x: f64", summary)
        self.assertIn("reference kind=method; visibility=public", summary)
        self.assertIn("owner_type=Bounds; name=new; params=[]; return_type=Self; signature=Bounds::new() -> Self", summary)
        self.assertIn("当前 src/*.rs 实时引用表", summary)
        self.assertIn("method public Bounds::new() -> Self", summary)

        error = f"""
error[E0061]: this function takes 0 arguments but 4 arguments were supplied
 --> src/quadtree.rs:10:20
  |
note: associated function defined here
  --> src/bounds.rs:3:12
"""
        grouped = fixer._group_errors_by_file(error)

        rel_paths = [Path(item["file_path"]).name for item in grouped]
        self.assertEqual(rel_paths, ["quadtree.rs"])

    def test_candidate_selection_prompt_includes_complete_reference_table(self):
        project = self.root / "demo-rust"
        project.mkdir()
        (project / "src").mkdir()
        quadtree_path = project / "src" / "quadtree.rs"
        quadtree_path.write_text("pub fn build() { let _ = crate::bounds::Bounds::new(1.0); }\n", encoding="utf-8")
        (project / ".cgr_api_contract.json").write_text(
            json.dumps(
                {
                    "files": {
                        "src/bounds.rs": {
                            "contract": {
                                "references": [
                                    {
                                        "path": "src/bounds.rs",
                                        "kind": "method",
                                        "visibility": "public",
                                        "public": True,
                                        "owner_type": "Bounds",
                                        "name": "new",
                                        "params": [],
                                        "return_type": "Self",
                                        "signature": "Bounds::new() -> Self",
                                    }
                                ]
                            }
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        fixer = PromptCaptureCodeFixer(Config(config_path=None, model_name="qwen32"), str(project))
        fixer.llm = CapturePromptModel("<target_file>src/quadtree.rs</target_file>")

        ok = fixer._fix_from_candidates(
            error_type="check",
            error_message="error[E0061]: this function takes 0 arguments but 1 argument was supplied",
            candidate_files=[str(quadtree_path)],
            prefer_local=False,
        )

        prompt = fixer.llm.messages[1]["content"]
        self.assertTrue(ok)
        self.assertEqual(Path(fixer.fixed_file).name, "quadtree.rs")
        self.assertIn("接口契约与完整引用表", prompt)
        self.assertIn("kind=method", prompt)
        self.assertIn("owner_type=Bounds", prompt)
        self.assertIn("name=new", prompt)
        self.assertIn("params=[]", prompt)
        self.assertIn("return_type=Self", prompt)
        self.assertIn("signature=Bounds::new() -> Self", prompt)
        self.assertIn("不要因为 `note: associated function defined here` 选择被调用方定义文件", prompt)

    def test_main_selects_contextual_agent_mode(self):
        args = Namespace(
            use_contextual_rust_agent=True,
            use_growth_rust_agent=False,
            use_stable_rust_agent=False,
        )

        self.assertEqual(main_module.selected_rust_agent_mode(args), "ContextualRustAgent")


if __name__ == "__main__":
    unittest.main()
