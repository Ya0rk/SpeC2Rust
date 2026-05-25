import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


def _clip(text: str, max_chars: int = 0) -> str:
    return text or ""


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        value = (item or "").replace("\\", "/").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _tokens(text: str) -> Set[str]:
    result: Set[str] = set()
    for piece in re.split(r"[^A-Za-z0-9_]+", text or ""):
        if not piece:
            continue
        lowered = piece.lower()
        result.add(lowered)
        for part in lowered.split("_"):
            if part:
                result.add(part)
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", piece):
            result.add(part.lower())

    stopwords = {
        "src",
        "rs",
        "c",
        "h",
        "md",
        "json",
        "rust",
        "spec",
        "plan",
        "task",
        "module",
        "root",
        "docs",
        "context",
        "rewrite",
    }
    return {item for item in result if len(item) > 1 and item not in stopwords}


def _rel_path(path: str, root: str) -> str:
    if not root:
        return path.replace("\\", "/")
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename((path or "").replace("\\", "/")))[0]


def _identifier_parts(name: str) -> List[str]:
    parts: List[str] = []
    for piece in re.split(r"[^A-Za-z0-9]+", name or ""):
        if not piece:
            continue
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", piece)
        parts.extend(part.lower() for part in (camel_parts or [piece]) if part)
    return parts


def _pascal_case(name: str) -> str:
    parts = _identifier_parts(name)
    if not parts:
        return ""
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _snake_case(name: str) -> str:
    parts = _identifier_parts(name)
    return "_".join(parts)


@dataclass
class DocSection:
    doc_path: str
    rel_path: str
    title: str
    text: str
    kind: str
    source_files: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    tokens: Set[str] = field(default_factory=set)


@dataclass
class RustFilePlan:
    path: str
    role: str = ""
    owns: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    spec_queries: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    source_functions: List[str] = field(default_factory=list)


class RustGenerationSpecPrompts:
    """Prompt library for Rust generation over `c_docs`."""

    @staticmethod
    def _attr(item, name: str, default=None):
        return getattr(item, name, default)

    @staticmethod
    def _join(values: Sequence[str], default: str, limit: int = 80) -> str:
        cleaned = [str(value).strip() for value in values or [] if str(value).strip()]
        if not cleaned:
            return default
        return ", ".join(cleaned[:limit])

    @classmethod
    def rewrite_contract(cls, planned=None) -> str:
        c_functions = cls._join(
            cls._attr(planned, "source_functions", []),
            "Current C functions associated with this file",
        )
        rust_symbols = cls._join(
            cls._attr(planned, "owns", []),
            "Planned Rust types and methods for this file",
        )
        return f"""The goal is behavioral equivalence, not C ABI equivalence.
- C file names, C type names, and C function names are only trace evidence; the target Rust API must use Rust naming, ownership, and module organization.
- Current C evidence: {c_functions}
- Current target Rust symbols: {rust_symbols}
- `xxx_t` / `struct xxx` should be refactored into `CamelCase` Rust types, for example `quadtree_bounds_t` -> `Bounds`.
- `xxx_new` / `create` / `init` should be refactored into `Type::new` or `Default`; do not expose a free `xxx_new` function.
- `xxx_free` / `destroy` / `delete` should be expressed through ownership and `Drop`; a public `free` API is usually unnecessary.
- `NULL`, nullable pointers, and missing values in C should be refactored into `Option<T>`.
- C status codes should be refactored by meaning into `bool`, `Option<T>`, or `Result<T, E>`; do not mechanically return `i32`.
- `void *` or user-data pointers in C should be recovered as generic parameters, concrete owned types, references, or trait objects; do not use `c_void`.
- C callbacks should be refactored into closure parameters, such as `impl FnMut(...)` or generic `F: FnMut(...)`; do not expose the original function-pointer plus user-data-pointer combination.
- Trees, linked lists, sets, and similar structures should use `Option<Box<T>>`, `Vec<T>`, slices, references, and borrowing to express ownership relationships.
- Do not use `unsafe`, `*mut`, `*const`, `NonNull`, `Box::into_raw`, `Box::from_raw`, `std::ptr`, `core::ptr`, `c_void`, `#[repr(C)]`, `extern \"C\"`, or `#[no_mangle]` in ordinary rewrite code.
- Do not hide C-style naming with `#[allow(non_camel_case_types)]`, `#[allow(non_snake_case)]`, or similar attributes.
- Example mappings: `foo_new` -> `Foo::new`, `foo_extend` -> `Foo::extend`, `tree_insert` -> `Tree::insert`, `tree_search` -> `Tree::search`."""

    @staticmethod
    def evidence_boundary() -> str:
        return """Evidence boundary:
- `translation_contract.json` is the highest-priority scope contract; if it conflicts with ordinary Markdown, the contract wins.
- `docs/rewrite-context/02_interfaces` provides interface facts, but does not mean the target Rust API must copy C names.
- `docs/rewrite-context/03_behaviors` provides behavioral constraints and should be used first to control return semantics, boundary conditions, and side effects.
- `specs/*/spec.md` provides module-level goals and constraints; `plan.md` and `tasks.md` are only secondary aids and must not override interface or behavior facts.
- `.specify/memory/constitution.md` is a governance constraint used to limit scope, dependencies, and quality requirements.
- Pointer/macro risk documents are only migration risk hints and do not directly authorize FFI or raw-pointer designs."""

    @classmethod
    def context_guide(cls, planned=None) -> str:
        path = cls._attr(planned, "path", "current target file")
        return f"""=== RUST GENERATION CONTEXT GUIDE ===
Target file: {path}
Usage:
- The spec sections below are local context filtered for the target file, not a full project dump.
- Confirm the responsibility boundary first from the current target Rust symbols, source_functions, and source_files.
- If behavior, type fields, call relationships, or dependencies are still unclear, use `<CGR_READ>` to request more spec/source/rust/registry data.
- **For functions that appear only as signatures in the C source index and do not have inline source, you must use `<CGR_READ>` in your first reply to request all missing source at once, then generate code. Do not guess function implementations from signatures.**
- Do not move responsibilities from other modules into the current file just because other C functions appear in the context snippets.

{cls.evidence_boundary()}

{cls.rewrite_contract(planned)}"""

    @staticmethod
    def project_structure_system_prompt() -> str:
        return (
            "You are a Rust architecture design expert, skilled at designing native Rust project structures from spec documents and migration contracts.\n\n"
            "Design principles:\n"
            "1. Follow Rust idioms, but migration scope takes priority over 'best-practice flourish'\n"
            "2. Introduce traits or extra abstraction only when input evidence supports it; default to keeping things simple and direct\n"
            "3. Do not invent core modules, instruction sets, state machines, protocols, threading models, or recovery mechanisms that did not exist in the original C project\n"
            "4. The default dependency policy is std-only; do not introduce third-party crates without explicit evidence\n"
            "5. Keep module boundaries clear, but do not expand into capabilities that are not present in the input"
        )

    @classmethod
    def project_structure_prompt(
        cls,
        project_name: str,
        plan_summary: str,
        static_context: str,
        spec_overview: str,
    ) -> str:
        return f"""Design a native Rust project structure based on the following spec documents and migration contract.

Project name: {project_name}

Programmatically inferred initial file plan (for reference; you may adjust the module split):
{plan_summary}

Static project context (including migration contract):
{static_context}

Spec document overview:
{spec_overview or '(no spec overview)'}

Please design the project structure, including:
1. The project directory layout (use tree command format, wrapped in `<project_file>` tags)
2. The main module split and the responsibility of each module
3. Core data structures and trait design
4. Key function and method signatures
5. Error handling strategy
6. If needed, request more information with `<CGR_READ>`

{cls.evidence_boundary()}

Constraints:
- The directory tree must stay within the file scope allowed by the migration contract
- Do not split out lots of extra modules just to be "more Rust"
- Do not output tests/examples/benches/ffi/release directories unless the context explicitly requires them
- C source facts take priority over summary descriptions
"""

    @staticmethod
    def implementation_plan_system_prompt() -> str:
        return (
            "You are a Rust implementation expert, skilled at creating detailed code implementation plans.\n\n"
            "Implementation principles:\n"
            "1. Move from simple to complex, analyze dependencies, and implement bottom-up step by step\n"
            "2. Reduce `unsafe` usage and prefer safe Rust standard library APIs\n"
            "3. Follow Rust coding conventions\n"
            "4. Do not expand into technical capabilities or engineering infrastructure that are not supported by the input evidence"
        )

    @classmethod
    def implementation_plan_prompt(
        cls,
        project_structure: str,
        plan_summary: str,
        files_list: Sequence[str],
    ) -> str:
        files_text = "\n".join(f"- {f}" for f in files_list)
        return f"""Create a detailed implementation plan based on the following project structure design and file plan.

Project structure design:
{project_structure}

Programmatically inferred file plan (including C function mapping):
{plan_summary}

Files to generate:
{files_text}

Please create a step-by-step implementation plan, including:
1. Dependency analysis: relationships between modules
2. Generation order: a bottom-up file generation plan (save the new file order in `<new_files_to_generate>` tags)
3. Implementation strategy for each file: key types and methods to implement, and algorithmic notes
4. Cross-file interface conventions: shared types and error propagation approach

Constraints:
- The new file order may only reorder existing files; no new files may be added
- Default to using only the Rust standard library
- Use the C source bodies and interface facts as the source of truth; do not expand unsupported parts
- Keep the number of phases restrained, preferably 3-5
- Do not rewrite the same fact repeatedly

{cls.evidence_boundary()}

Please wrap the implementation plan in `<implementation_plan>` tags."""

    @staticmethod
    def project_planning_system_prompt() -> str:
        return (
            "You are a rigorous Rust project structure planning assistant."
            "You must plan the file structure based on the spec and migration contract, and output either `<CGR_PLAN>JSON</CGR_PLAN>` or a `<CGR_READ>` request."
            "Do not treat C function names as the target Rust API, and do not plan unsupported expanded functionality."
        )

    @classmethod
    def project_planning_prompt(cls, fallback_files: Sequence[str], static_context: str) -> str:
        files_json = "\n".join(f'- "{path}"' for path in fallback_files)
        return f"""Plan the Rust file structure and bottom-up generation order for this C-to-Rust rewrite project.

You may only output JSON wrapped in `<CGR_PLAN>`. JSON schema:
{{
  "files": [
    {{
      "path": "src/example.rs",
      "role": "The file's responsibility, kept deliberately restrained",
      "owns": ["The unique target Rust types, methods, or free functions owned by this file; do not fill in C function names"],
      "depends_on": ["Files that must be generated first"],
      "spec_queries": ["Spec keywords most needed when generating this file"],
      "source_files": ["Corresponding C source files"],
      "source_functions": ["Corresponding C functions; evidence only"]
    }}
  ],
  "order": ["Cargo.toml", "src/example.rs", "src/lib.rs", "README.md"]
}}

Planning rules:
1. Bottom-up: generate base types, errors, constants, and data structures first; generate aggregate containers and algorithms later; rebuild lib.rs locally last.
2. A Rust type may be owned by only one file; do not let node.rs, data.rs, and tree.rs define the same struct repeatedly.
3. Each file should serve one clear responsibility. If a file is too large, split it by type or module first, but do not expand the project size without evidence.
4. `owns` must contain Rust target symbols, such as `Bounds`, `Bounds::new`, and `Quadtree::insert`; C function names may only go into `source_functions` or `spec_queries`.
5. Do not plan functionality that is not reflected in the spec or C source; do not proactively add serde, async, threading, or recovery mechanisms.
6. If the migration contract has `allowed_rust_files`, select only from the allowed file set.
7. Unless configuration or documentation explicitly requires it, do not plan tests/examples/benches.
8. If information is insufficient, use `<CGR_READ>` to request more spec/source/registry data; do not guess.

Optional fallback file set:
{files_json or "- (empty)"}

{cls.evidence_boundary()}

Static project context:
{static_context}
"""

    @staticmethod
    def file_generation_system_prompt() -> str:
        return (
            "You are a context-on-demand Rust code generation assistant."
            "Your task is to generate a single target file while strictly honoring the planned file boundaries, the existing symbol table, and the migration contract."
            "You must refactor into a Rust-style API rather than simulating the C ABI; raw pointers, `unsafe`, `c_void`, and C-style function names are forbidden."
            "\n\nKey principle: you must implement every symbol in the owns list."
            "If a function is only indexed by signature and does not have full source, you are **forbidden to guess the implementation** and must immediately use `<CGR_READ>` to request the full source."
            "You may only write the corresponding Rust implementation after seeing the complete C source."
            "It is better to send one more round of `<CGR_READ>` than to generate an incomplete file or skip any symbol in `owns`."
        )

    @classmethod
    def file_generation_prompt(
        cls,
        planned,
        planned_files: Sequence[str],
        plan_summary: str,
        registry_summary: str,
        spec_context: str,
        source_context: str,
    ) -> str:
        path = cls._attr(planned, "path", "")
        return f"""Generate the final content of the target file.

Target file: {path}
File responsibility: {cls._attr(planned, "role", "") or 'Implement this file responsibility as planned'}
Unique target Rust symbols owned by this file: {cls._join(cls._attr(planned, "owns", []), '(determined naturally by the current file content, but may not duplicate existing symbols)')}
Corresponding C source files: {cls._join(cls._attr(planned, "source_files", []), '(no direct source-file mapping)')}
Corresponding C functions (behavior evidence only; do not copy as Rust API names): {cls._join(cls._attr(planned, "source_functions", []), '(no direct function mapping)')}
Files that must be depended on first: {cls._join(cls._attr(planned, "depends_on", []), '(no explicit dependencies)')}
Allowed/planned file set: {cls._join(planned_files, '(not provided)', limit=80)}

Project generation plan:
{plan_summary}

Generated Rust symbol table:
{registry_summary}

Current spec/source context for this file:
{spec_context or '(no matching spec snippet found; use <CGR_READ> to request spec)'}

Relevant C source (key functions are inlined; the rest are indexed):
{source_context or '(no matching source found; use <CGR_READ> to request source)'}

Rust migration contract:
{cls.rewrite_contract(planned)}

Generation constraints:
1. Output only the final content of `{path}`; do not explain.
2. Do not redefine any struct/enum/type/trait/free fn/const/static that is already owned by another file in the symbol table.
3. The `references` in the generated Rust symbol table include public/private visibility, function parameters, return types, and struct fields; cross-file references may only use public symbols, and private symbols may only be used inside their defining file.
4. When calling existing functions or methods, match the parameter list and return types in the symbol table; when accessing struct fields, only access public fields that exist in `references`; do not guess Rust members from C source field names alone.
5. You may `use crate::...` to reference public modules and public symbols already present in the generated symbol table; do not reference unplanned modules.
6. If you must reference a file that has not been generated yet, first try to implement it through existing dependencies in the current file or the standard library; do not invent new modules out of thin air.
7. Do not add unsupported features, do not introduce unauthorized third-party dependencies, and do not generate inline test modules unless configuration explicitly allows it.
8. Do not generate a C ABI adapter layer, and do not expose C-style free functions such as `project_prefix_*` / `*_free` / `*_new`.
9. Do not use raw pointers, `unsafe`, `c_void`, `repr(C)`, or `extern \"C\"` to simulate the original C project.
10. The code must follow Rust naming conventions: types in `CamelCase`, methods/functions in `snake_case`, and clear module responsibilities.
11. Only key functions are inlined in the C source area; the rest are index entries only.
    **You must implement every symbol in the owns list.**
    If a symbol corresponds to a C function that only has a signature index and no inline source, you **must** first use `<CGR_READ>` to request the full source before implementing it, and you are forbidden to guess the function body from the signature.
    Request format:
<CGR_READ>
[{{"kind":"source","query":"function name or file name"}}, {{"kind":"spec","query":"keyword"}}, {{"kind":"rust","query":"src/existing.rs"}}, {{"kind":"registry"}}]
</CGR_READ>
    You may send multiple requests at once. `source` supports querying by function name (for example `"quadtree_insert"`) or file name (for example `"node.c"`).
    **In your first reply, inspect all non-inlined functions in the index and request all required source at once; do not split the request across multiple rounds.**
12. When information is sufficient, output the complete file content and append `<CGR_DONE>` on its own at the end.
    **If the output file is missing any symbol in `owns`, treat it as a failure.**
"""

    @staticmethod
    def read_materials_followup(materials: str) -> str:
        return (
            "Here is the material you requested to read. Continue; if the information is already sufficient, output the target result directly."
            "Do not request the same material again, and do not bring unrelated module responsibilities from the retrieved material into the current file.\n\n"
            + materials
        )

    @staticmethod
    def repair_system_prompt() -> str:
        return (
            "You are a strict Rust file-boundary and Rust-style repair assistant."
            "Only fix the current file; do not expand project functionality or keep a C ABI simulation layer."
        )

    @classmethod
    def repair_prompt(
        cls,
        planned,
        findings: Sequence[str],
        registry_summary: str,
        plan_summary: str,
        current_content: str,
    ) -> str:
        path = cls._attr(planned, "path", "")
        findings_text = "\n".join("- " + item for item in findings)
        return f"""The previously generated `{path}` violated the project boundary. Please fix it while keeping the file responsibility unchanged.

Violations:
{findings_text}

Existing symbol table:
{registry_summary}

Project plan:
{plan_summary}

Current erroneous content:
```rust
{current_content}
```

Rust migration contract:
{cls.rewrite_contract(planned)}

Requirements:
1. Output only the complete corrected content for `{path}`.
2. Remove duplicate definitions and out-of-bounds capabilities; do not move responsibilities from other files into the current file.
3. Cross-file references may only use references marked as public in the symbol table; calls to existing functions or methods must match the parameter and return types in the symbol table, and field access must also exist in the symbol table's field references.
4. If the violations come from C ABI or C-style code, you must refactor them into Rust types, methods, `Option` / `Result`, ownership, and closures; do not keep patching the raw-pointer version.
5. If more context is needed, use `<CGR_READ>`; otherwise output the final content directly and end with `<CGR_DONE>`.
"""

    @staticmethod
    def force_write_system_prompt() -> str:
        return (
            "You are the final write-decision assistant for Rust files."
            "You will receive files that still violate boundary checks."
            "Prefer repair; only allow `<CGR_FORCE_WRITE>` when you clearly believe the current content must be kept and the user needs to force progress."
        )

    @classmethod
    def force_write_prompt(
        cls,
        planned,
        findings: Sequence[str],
        registry_summary: str,
        plan_summary: str,
        current_content: str,
    ) -> str:
        path = cls._attr(planned, "path", "")
        findings_text = "\n".join("- " + item for item in findings)
        return f"""After repair, `{path}` still triggers the write prohibition rules. Make a final decision.

Remaining violations:
{findings_text}

Existing symbol table:
{registry_summary}

Project plan:
{plan_summary}

Current candidate content:
```rust
{current_content}
```

Decision rules:
1. Preferred: keep repairing the file until it no longer violates the above rules. In that case, output only the complete corrected content and end with `<CGR_DONE>`.
2. If you believe these violations are false positives, or if the current candidate content must be written to keep the project generation moving, you may force the write.
3. When forcing a write, you must output the full file content and additionally include:
<CGR_FORCE_WRITE>
Explain in one sentence why the current write prohibition rule must be bypassed.
</CGR_FORCE_WRITE>
4. Without the `<CGR_FORCE_WRITE>` marker, the outer agent will still treat the result as prohibited.
5. Do not output only the marker; you must output the complete file content that can be written to `{path}`.
"""


class RustGenerationSpecAgent:
    """
    Read-only Rust generation view over `c_docs`.

    It only indexes spec evidence, maps source facts to planned Rust files,
    and returns small file-specific context slices.
    """

    IMPORTANT_KINDS = {"manifest", "constitution", "interface", "behavior", "module-spec"}
    SECONDARY_KINDS = {"module-plan", "risk", "doc"}
    GENERIC_ANCHOR_PARTS = {
        "new",
        "get",
        "set",
        "init",
        "create",
        "make",
        "free",
        "drop",
        "read",
        "write",
        "call",
        "run",
        "tree",
        "node",
        "data",
        "type",
        "file",
        "module",
        "project",
        "source",
        "rust",
        "spec",
    }

    def __init__(
        self,
        doc_contents: Optional[Dict[str, str]] = None,
        source_records: Optional[List[Dict]] = None,
        translation_contract: Optional[Dict] = None,
        config=None,
    ):
        self.doc_contents = doc_contents or {}
        self.source_records = source_records or []
        self.translation_contract = translation_contract or {}
        self.config = config
        self.root = self._infer_root(list(self.doc_contents.keys()))
        self.sections: List[DocSection] = []
        self.source_files: List[str] = []
        self.function_to_source: Dict[str, str] = {}
        self.source_to_functions: Dict[str, List[str]] = {}
        self.source_to_types: Dict[str, List[str]] = {}
        self.function_signatures: Dict[str, str] = {}
        self._build()

    def _infer_root(self, paths: Sequence[str]) -> str:
        if not paths:
            return ""
        abs_paths = [os.path.abspath(path) for path in paths]
        for path in abs_paths:
            parts = Path(path).parts
            lowered = [part.lower() for part in parts]
            if "c_docs" in lowered:
                return str(Path(*parts[: lowered.index("c_docs") + 1]))
        try:
            return os.path.commonpath(abs_paths)
        except ValueError:
            return os.path.dirname(abs_paths[0])

    def _build(self):
        self.sections = []
        self.function_signatures = {}

        for path, content in self.doc_contents.items():
            normalized = path.replace("\\", "/").lower()
            if normalized.endswith("translation_lint.json") or normalized.endswith("translation_contract.json"):
                continue
            rel = _rel_path(path, self.root)
            kind = self._kind_for_rel_path(rel)
            sections = self._split_document(path, rel, content or "", kind)
            self.sections.extend(sections)
            for section in sections:
                self.function_signatures.update(self._extract_function_signatures(section.text))

        source_files: List[str] = []
        for section in self.sections:
            source_files.extend(section.source_files)

        for record in self.source_records:
            source_file = str(record.get("file", "")).replace("\\", "/")
            name = str(record.get("name", "")).strip()
            if source_file.endswith((".c", ".h")):
                source_files.append(source_file)
            if name and source_file:
                self.function_to_source[name] = source_file
                self.source_to_functions.setdefault(source_file, []).append(name)
            calls = record.get("calls", []) if isinstance(record.get("calls", []), list) else []
            for call in calls:
                call_name = str(call).strip()
                if call_name and call_name not in self.function_to_source:
                    self.function_to_source.setdefault(call_name, "")

        source_files.extend(self._ingest_translation_contract())
        self.source_files = _dedupe(source_files)

        for section in self.sections:
            if section.kind in {"manifest", "constitution", "doc"}:
                continue
            section_sources = self._owning_c_sources_for_section(section)
            if not section_sources:
                continue
            source_stem = _stem(section_sources[0]).lower()
            for symbol in section.symbols:
                if symbol in self.function_to_source and self.function_to_source[symbol]:
                    continue
                if symbol.endswith("_t"):
                    if source_stem and source_stem in symbol.lower():
                        for source_file in section_sources:
                            self.source_to_types.setdefault(source_file, []).append(symbol)
                elif self._looks_like_c_function(symbol):
                    for source_file in section_sources:
                        self.function_to_source[symbol] = source_file
                        self.source_to_functions.setdefault(source_file, []).append(symbol)

        for key in list(self.source_to_functions.keys()):
            self.source_to_functions[key] = _dedupe(self.source_to_functions[key])
        for key in list(self.source_to_types.keys()):
            self.source_to_types[key] = _dedupe(self.source_to_types[key])

    def _ingest_translation_contract(self) -> List[str]:
        if not isinstance(self.translation_contract, dict) or not self.translation_contract:
            return []

        source_files: List[str] = []
        files = self.translation_contract.get("files", [])
        for item in files if isinstance(files, list) else []:
            path = str(item.get("path", "") if isinstance(item, dict) else item).replace("\\", "/").strip()
            if path.endswith((".c", ".h")):
                source_files.append(path)

        functions = self.translation_contract.get("functions", [])
        for item in functions if isinstance(functions, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            source_file = str(item.get("file", "") or item.get("declared_in", "")).replace("\\", "/").strip()
            if source_file.endswith((".c", ".h")):
                source_files.append(source_file)
            if name and source_file:
                self.function_to_source[name] = source_file
                self.source_to_functions.setdefault(source_file, []).append(name)

        types = self.translation_contract.get("types", [])
        for item in types if isinstance(types, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            source_file = str(item.get("file", "")).replace("\\", "/").strip()
            if source_file.endswith((".c", ".h")):
                source_files.append(source_file)
            if name and source_file:
                self.source_to_types.setdefault(source_file, []).append(name)

        return source_files

    def _kind_for_rel_path(self, rel_path: str) -> str:
        normalized = rel_path.replace("\\", "/").lower()
        if normalized.endswith("00_repo_manifest.md"):
            return "manifest"
        if ".specify/memory" in normalized:
            return "constitution"
        if "/01_subsystems/" in normalized:
            return "subsystem"
        if "/02_interfaces/" in normalized:
            return "interface"
        if "/03_behaviors/" in normalized:
            return "behavior"
        if "/04_gaps_and_risks/" in normalized:
            return "risk"
        if normalized.startswith("specs/") or "/specs/" in normalized:
            if normalized.endswith("/spec.md"):
                return "module-spec"
            if normalized.endswith("/plan.md"):
                return "module-plan"
            if normalized.endswith("/tasks.md"):
                return "module-tasks"
        return "doc"

    def _split_document(self, path: str, rel: str, content: str, kind: str) -> List[DocSection]:
        chunks: List[Tuple[str, List[str]]] = []
        current_title = rel
        current_lines: List[str] = []

        for line in content.splitlines():
            if re.match(r"^#{1,4}\s+\S", line):
                if current_lines:
                    chunks.append((current_title, current_lines))
                current_title = line.strip().lstrip("#").strip() or rel
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            chunks.append((current_title, current_lines))
        if not chunks:
            chunks = [(rel, content.splitlines())]

        sections: List[DocSection] = []
        for title, lines in chunks:
            text = "\n".join(lines).strip()
            if not text:
                continue
            source_files = self._extract_source_files(text)
            symbols = self._extract_symbols(title + "\n" + text)
            token_text = f"{rel} {title} {' '.join(source_files)} {' '.join(symbols)}"
            sections.append(
                DocSection(
                    doc_path=path,
                    rel_path=rel,
                    title=title,
                    text=text,
                    kind=kind,
                    source_files=source_files,
                    symbols=symbols,
                    tokens=_tokens(token_text),
                )
            )
        return sections

    def _extract_source_files(self, text: str) -> List[str]:
        matches = re.findall(r"(?<![A-Za-z0-9_./\\-])([A-Za-z0-9_./\\-]+\.(?:c|h))\b", text or "")
        return _dedupe(match.replace("\\", "/") for match in matches)

    def _extract_symbols(self, text: str) -> List[str]:
        symbols: List[str] = []
        symbols.extend(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text or ""))
        symbols.extend(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*_t)\b", text or ""))
        for declaration in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text or ""):
            if declaration not in {"if", "for", "while", "switch", "return", "sizeof"}:
                symbols.append(declaration)
        return _dedupe(symbols)

    def _extract_function_signatures(self, text: str) -> Dict[str, str]:
        lines = (text or "").splitlines()
        signatures: Dict[str, str] = {}
        current_symbol = ""
        for raw_line in lines:
            stripped = raw_line.strip()
            symbol_match = re.match(r"^###\s+`([^`]+)`", stripped)
            if symbol_match:
                current_symbol = symbol_match.group(1).strip()
                continue
            if not current_symbol:
                continue
            declaration_match = re.search(r"`([^`]+\([^`]*\)[^`]*)`", stripped)
            if "观察到的声明" in stripped and declaration_match:
                signatures[current_symbol] = declaration_match.group(1).strip()
        return signatures

    def _looks_like_c_function(self, symbol: str) -> bool:
        text = symbol or ""
        if text.upper() == text:
            return False
        return bool(re.search(r"_[A-Za-z0-9_]*$", text)) and not text.endswith("_t")

    def _owning_c_sources_for_section(self, section: DocSection) -> List[str]:
        c_sources = [source for source in section.source_files if source.endswith(".c")]
        if not c_sources:
            return []
        stems = {_stem(source).lower() for source in c_sources if _stem(source)}
        if len(stems) != 1:
            return []
        with_dir = [source for source in c_sources if "/" in source]
        return _dedupe(with_dir or c_sources)

    def infer_candidate_files(self) -> List[str]:
        files = ["Cargo.toml"]
        for source_file in self.source_files:
            rust_path = self._rust_path_for_source(source_file)
            if rust_path:
                files.append(rust_path)
        files.extend(["src/lib.rs", "README.md"])
        return _dedupe(files)

    def build_file_plan(self, allowed_files: Optional[Sequence[str]] = None) -> List[RustFilePlan]:
        files = _dedupe(allowed_files or self.infer_candidate_files())
        plans = [self._plan_for_path(path) for path in files]
        return self._sort_plan(plans)

    def _plan_for_path(self, path: str) -> RustFilePlan:
        normalized = path.replace("\\", "/")
        if normalized == "Cargo.toml":
            return RustFilePlan(path=normalized, role="Cargo package manifest; locally generate a minimal compilable configuration")
        if normalized == "src/lib.rs":
            return RustFilePlan(path=normalized, role="crate entry point; locally rebuilt from generated modules and the symbol table")
        if normalized.lower() == "readme.md":
            return RustFilePlan(path=normalized, role="project documentation; describe only build, usage, and current capabilities")

        source_files = self._source_files_for_rust_path(normalized)
        source_functions: List[str] = []
        source_types: List[str] = []
        for source_file in source_files:
            key = self._strip_leading_slash(source_file)
            source_functions.extend(self.source_to_functions.get(key, []))
            source_types.extend(self.source_to_types.get(key, []))

        owns = self._target_rust_symbols_for_sources(normalized, source_files, source_functions, source_types)
        stem = _stem(normalized)
        spec_queries = _dedupe([stem] + source_files + source_functions[:12] + source_types[:8])
        role = (
            f"Implement a Rust-style API based on the behavior evidence from `{', '.join(source_files)}`; C function names are only traceability evidence, not the target API"
            if source_files
            else f"Implement the Rust module related to `{stem}`; do not carry responsibilities from other files, and the target API must be Rust-style"
        )

        return RustFilePlan(
            path=normalized,
            role=role,
            owns=owns,
            depends_on=[],
            spec_queries=spec_queries,
            source_files=source_files,
            source_functions=_dedupe(source_functions),
        )

    def _target_rust_symbols_for_sources(
        self,
        rust_path: str,
        source_files: Sequence[str],
        source_functions: Sequence[str],
        source_types: Sequence[str],
    ) -> List[str]:
        type_name = self._target_type_name(rust_path, source_files, source_types)
        symbols = [type_name] if type_name else []
        for function_name in source_functions:
            target = self._target_method_name(function_name, source_files, type_name)
            if target:
                symbols.append(target)
        return _dedupe(symbols)

    def _target_type_name(
        self,
        rust_path: str,
        source_files: Sequence[str],
        source_types: Sequence[str],
    ) -> str:
        rust_stem = _stem(rust_path)
        source_stem = _stem(source_files[0]) if source_files else rust_stem
        if source_stem and source_stem.lower() not in {"main", "test", "tests", "benchmark"}:
            return _pascal_case(source_stem)

        for c_type in source_types:
            core = re.sub(r"_t$", "", c_type or "")
            parts = _identifier_parts(core)
            if parts:
                return "".join(part[:1].upper() + part[1:] for part in parts)
        return _pascal_case(source_stem or rust_stem)

    def _target_method_name(
        self,
        c_function: str,
        source_files: Sequence[str],
        type_name: str,
    ) -> str:
        raw_name = (c_function or "").strip()
        if not raw_name:
            return ""

        base = raw_name.rstrip("_")
        source_stems = [_stem(source) for source in source_files if _stem(source)]
        method_base = self._strip_source_prefix(base, source_stems)
        method_name = self._rust_method_from_c_suffix(method_base)
        if not method_name:
            return ""
        if method_name in {"free", "destroy", "delete", "release"}:
            return f"Drop for {type_name}" if type_name else "Drop"
        if type_name:
            return f"{type_name}::{method_name}"
        return method_name

    def _strip_source_prefix(self, name: str, source_stems: Sequence[str]) -> str:
        lowered_parts = _identifier_parts(name)
        if not lowered_parts:
            return name

        for source_stem in source_stems:
            source_parts = _identifier_parts(source_stem)
            if not source_parts:
                continue
            for index in range(0, len(lowered_parts) - len(source_parts) + 1):
                if lowered_parts[index:index + len(source_parts)] == source_parts:
                    suffix = lowered_parts[index + len(source_parts):]
                    if suffix:
                        return "_".join(suffix)

            collapsed = _snake_case(source_stem)
            lowered = name.lower()
            if collapsed and lowered.startswith(collapsed):
                suffix = name[len(source_stem):].lstrip("_")
                if suffix:
                    return suffix
        return name

    def _rust_method_from_c_suffix(self, name: str) -> str:
        cleaned = _snake_case(name.strip("_"))
        if not cleaned:
            return ""
        aliases = {
            "init": "new",
            "create": "new",
            "alloc": "new",
            "isempty": "is_empty",
            "isleaf": "is_leaf",
            "ispointer": "is_pointer",
        }
        if cleaned in aliases:
            return aliases[cleaned]
        if cleaned.startswith("is") and "_" not in cleaned and len(cleaned) > 2:
            return "is_" + cleaned[2:]
        if cleaned.startswith("has") and "_" not in cleaned and len(cleaned) > 3:
            return "has_" + cleaned[3:]
        if cleaned[0].isdigit():
            return "run_" + cleaned
        return cleaned

    @staticmethod
    def _strip_leading_slash(path: str) -> str:
        """Remove leading '/' from paths like '/sds.c' extracted from spec text."""
        return path.lstrip("/") if path else path

    def _source_files_for_rust_path(self, rust_path: str) -> List[str]:
        normalized = rust_path.replace("\\", "/")
        base = _stem(normalized)
        c_matches: List[str] = []
        h_matches: List[str] = []
        for source_file in self.source_files:
            cleaned = self._strip_leading_slash(source_file)
            if _stem(cleaned).lower() == base.lower():
                if cleaned.endswith(".c"):
                    c_matches.append(cleaned)
                elif cleaned.endswith(".h"):
                    h_matches.append(cleaned)
        with_dir = [source for source in c_matches if "/" in source]
        return _dedupe(with_dir or c_matches or h_matches)

    def _rust_path_for_source(self, source_file: str) -> str:
        normalized = source_file.replace("\\", "/")
        stem = _stem(normalized)
        lowered = stem.lower()
        if not stem:
            return ""
        if lowered == "main":
            return "src/main.rs"
        if lowered in {"test", "tests"} or normalized.startswith("tests/"):
            return "tests/" + stem + ".rs"
        if "bench" in lowered or normalized.startswith("bench"):
            return "benches/" + stem + ".rs"
        return "src/" + stem + ".rs"

    def _infer_dependencies(self, plan: RustFilePlan, plans_by_source: Dict[str, RustFilePlan]) -> List[str]:
        deps: List[str] = []
        for record in self.source_records:
            source_file = str(record.get("file", "")).replace("\\", "/")
            if source_file not in plan.source_files:
                continue
            calls = record.get("calls", []) if isinstance(record.get("calls", []), list) else []
            for call in calls:
                owner_source = self.function_to_source.get(str(call).strip(), "")
                owner_plan = plans_by_source.get(owner_source)
                if owner_plan and owner_plan.path != plan.path:
                    deps.append(owner_plan.path)
        return _dedupe(deps)

    def _sort_plan(self, plans: Sequence[RustFilePlan]) -> List[RustFilePlan]:
        by_path = {plan.path: plan for plan in plans}
        by_source: Dict[str, RustFilePlan] = {}
        for plan in plans:
            for source_file in plan.source_files:
                by_source[source_file] = plan

        for plan in plans:
            if plan.path.startswith("src/") and plan.path not in {"src/lib.rs", "src/main.rs"}:
                plan.depends_on = [dep for dep in self._infer_dependencies(plan, by_source) if dep in by_path]
            elif plan.path == "src/lib.rs":
                plan.depends_on = [
                    item.path
                    for item in plans
                    if item.path.startswith("src/")
                    and item.path.endswith(".rs")
                    and item.path not in {"src/lib.rs", "src/main.rs"}
                ]

        pending = {plan.path: plan for plan in plans}
        emitted: List[RustFilePlan] = []
        while pending:
            ready = [plan for plan in pending.values() if all(dep not in pending for dep in plan.depends_on)]
            if not ready:
                ready = list(pending.values())
            ready.sort(key=self._file_priority)
            plan = ready[0]
            emitted.append(plan)
            pending.pop(plan.path, None)

        readme = [plan for plan in emitted if plan.path.lower() == "readme.md"]
        others = [plan for plan in emitted if plan.path.lower() != "readme.md"]
        return others + readme

    def _file_priority(self, plan: RustFilePlan) -> Tuple[int, str]:
        normalized = plan.path.lower()
        name = os.path.basename(normalized)
        if name == "cargo.toml":
            return (0, normalized)
        if normalized.startswith("src/") and name != "lib.rs":
            if any(token in normalized for token in ["type", "const", "error", "data", "model", "point", "bound", "node"]):
                return (1, normalized)
            return (2, normalized)
        if name == "lib.rs":
            return (3, normalized)
        if name == "readme.md":
            return (4, normalized)
        return (5, normalized)

    def context_for_file(self, plan: RustFilePlan) -> str:
        parts = [RustGenerationSpecPrompts.context_guide(plan)]

        reference_table = self._reference_table_for_plan(plan)
        if reference_table:
            parts.append(reference_table)

        manifest = self._manifest_excerpt_for_plan(plan)
        if manifest:
            parts.append("=== PROJECT FACTS ===\n" + manifest)

        ranked = self._rank_sections_for_plan(plan)

        # 仅当没有模块级 spec section 时才附加 constitution
        has_module_sections = any(
            s.kind in {"interface", "behavior", "module-spec"} for s in ranked
        )
        if not has_module_sections:
            constitution = self._constitution_excerpt()
            if constitution:
                parts.append("=== GOVERNANCE ===\n" + constitution)

        for section in ranked:
            if section.kind in {"manifest", "constitution"}:
                continue
            excerpt = self._focused_section_excerpt(section, plan)
            if not excerpt:
                continue
            block = (
                f"\n\n=== SPEC SECTION {section.rel_path} | {section.kind} | {section.title} ===\n"
                f"{excerpt}"
            )
            parts.append(block)
        return "\n".join(parts).strip()

    def context_for_query(self, query: str, max_chars: int = 18000) -> str:
        query_tokens = _tokens(query)
        scored = []
        for section in self.sections:
            score = self._section_score(section, query_tokens, query)
            if score > 0:
                scored.append((score, section))
        scored.sort(key=lambda item: (-item[0], item[1].rel_path, item[1].title))

        parts: List[str] = []
        total = 0
        for _, section in scored[:8]:
            excerpt = self._focused_section_excerpt_for_query(section, query_tokens, soft_max_chars=2400)
            if not excerpt:
                continue
            block = (
                f"\n\n=== SPEC SECTION {section.rel_path} | {section.kind} | {section.title} ===\n"
                f"{excerpt}"
            )
            parts.append(block)
            total += len(block)
        return "\n".join(parts).strip() or "No matching spec section found."

    def _reference_table_for_plan(self, plan: RustFilePlan) -> str:
        lines = [
            "=== FILE REFERENCE TABLE ===",
            f"- target_file: `{plan.path}`",
            f"- role: {plan.role or 'unspecified'}",
            f"- owns: {', '.join(f'`{item}`' for item in plan.owns) or 'none'}",
            f"- depends_on: {', '.join(f'`{item}`' for item in plan.depends_on) or 'none'}",
            f"- source_files: {', '.join(f'`{item}`' for item in plan.source_files) or 'none'}",
        ]

        if plan.source_functions:
            lines.append("- source_function_references:")
            for function_name in plan.source_functions:
                signature = self.function_signatures.get(function_name, "")
                target = self._target_method_name(
                    function_name,
                    plan.source_files,
                    self._target_type_name(plan.path, plan.source_files, []),
                )
                if signature:
                    lines.append(f"  - `{function_name}` -> `{target or function_name}` | signature: `{signature}`")
                else:
                    lines.append(f"  - `{function_name}` -> `{target or function_name}`")
        else:
            lines.append("- source_function_references: none")

        if plan.spec_queries:
            lines.append(f"- spec_queries: {', '.join(f'`{item}`' for item in plan.spec_queries[:20])}")
        return "\n".join(lines)

    def _rank_sections_for_plan(self, plan: RustFilePlan) -> List[DocSection]:
        query_text = " ".join(
            [plan.path, plan.role]
            + plan.source_files
            + plan.source_functions
            + plan.owns
            + plan.spec_queries
        )
        query_tokens = _tokens(query_text)
        plan_source_stems = {_stem(source).lower() for source in plan.source_files if _stem(source)}
        plan_target_mod = _stem(plan.path).lower().replace("-", "_")

        scored = []
        for section in self.sections:
            if section.kind not in self.IMPORTANT_KINDS and section.kind not in self.SECONDARY_KINDS:
                continue
            if section.kind == "module-tasks":
                continue

            section_c_stems = {
                _stem(source).lower()
                for source in section.source_files
                if source.endswith(".c") and _stem(source)
            }

            # --- 严格模块过滤 ---
            has_source_overlap = self._section_matches_plan_source(section, plan)
            has_symbol_overlap = any(symbol in section.symbols for symbol in plan.owns)
            has_function_overlap = any(fn in section.symbols for fn in plan.source_functions)

            # 如果 plan 有明确 source_files，只允许：
            # (a) source 重叠的 section
            # (b) 直接提到 plan owns/source_functions 的 section
            # (c) 全局类型 section（manifest/constitution）
            if plan_source_stems:
                if section.kind not in {"manifest", "constitution"}:
                    if section_c_stems and not section_c_stems.issubset(plan_source_stems):
                        continue
                    if not has_source_overlap and not has_symbol_overlap and not has_function_overlap:
                        # 没有明确 source 也没有符号重叠 → 检查 module 名
                        if not section.source_files:
                            mapped_stems = {
                                _stem(self.function_to_source.get(sym, "")).lower()
                                for sym in section.symbols
                                if self.function_to_source.get(sym, "")
                            }
                            mapped_stems.discard("")
                            if mapped_stems and not mapped_stems.issubset(plan_source_stems):
                                continue
                            if not mapped_stems:
                                continue

            score = self._section_score(section, query_tokens, query_text)
            if has_source_overlap:
                score += 80
            if has_symbol_overlap:
                score += 40
            if has_function_overlap:
                score += 30
            if section.kind == "interface":
                score += 20
            elif section.kind == "behavior":
                score += 14
            elif section.kind == "module-spec":
                score += 10
            elif section.kind == "constitution":
                score += 6
            elif section.kind == "module-plan":
                score += 2
            if score > 0:
                scored.append((score, section))

        scored.sort(key=lambda item: (-item[0], item[1].rel_path, item[1].title))

        ordered: List[DocSection] = []
        seen = set()
        for _, section in scored:
            key = (section.rel_path, section.title, section.text[:120])
            if key in seen:
                continue
            seen.add(key)
            ordered.append(section)
        return ordered[:3]

    def _focused_section_excerpt(self, section: DocSection, plan: RustFilePlan, soft_max_chars: int = 1400) -> str:
        text = section.text or ""
        if not text.strip():
            return ""

        anchors = self._section_focus_anchors(plan, section)
        if section.kind == "manifest":
            return self._manifest_excerpt_for_plan(plan)
        if section.kind == "constitution":
            return self._constitution_excerpt()
        if not anchors:
            return text

        blocks = self._semantic_blocks(text)
        if not blocks:
            return text

        scored_blocks: List[Tuple[int, int, str]] = []
        for index, block in enumerate(blocks):
            score = self._block_score(block, anchors)
            if score > 0:
                scored_blocks.append((score, index, block))

        if not scored_blocks:
            return text if self._section_matches_plan_source(section, plan) else ""

        required_blocks = self._required_blocks_for_plan(blocks, plan)
        selected_indexes: List[int] = []
        seen_indexes = set()

        for index in required_blocks:
            if index not in seen_indexes:
                selected_indexes.append(index)
                seen_indexes.add(index)

        for _, index, _ in sorted(scored_blocks, key=lambda item: (-item[0], item[1])):
            if index in seen_indexes:
                continue
            selected_indexes.append(index)
            seen_indexes.add(index)

        selected_indexes.sort()
        selected_blocks = self._select_blocks_by_soft_budget(
            blocks=blocks,
            ordered_indexes=selected_indexes,
            required_indexes=required_blocks,
            soft_max_chars=soft_max_chars,
        )

        if not selected_blocks:
            return text
        return "\n\n".join(block for block in selected_blocks if block).strip()

    def _semantic_blocks(self, text: str) -> List[str]:
        lines = (text or "").splitlines()
        if not lines:
            return []

        blocks: List[str] = []
        heading_stack: List[Tuple[int, str]] = []
        current_group: List[str] = []

        def flush_group() -> None:
            nonlocal current_group
            content = "\n".join(current_group).strip()
            current_group = []
            if not content:
                return
            heading_prefix = "\n".join(item[1] for item in heading_stack).strip()
            if heading_prefix:
                blocks.append(f"{heading_prefix}\n{content}".strip())
            else:
                blocks.append(content)

        for line in lines:
            heading_match = re.match(r"^(#{1,4})\s+\S", line)
            if heading_match:
                flush_group()
                level = len(heading_match.group(1))
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, line.strip()))
                continue

            stripped = line.strip()
            if not stripped:
                flush_group()
                continue

            if current_group and self._should_start_new_group(current_group[-1], line):
                flush_group()
            current_group.append(line)

        flush_group()
        refined: List[str] = []
        for block in blocks:
            refined.extend(self._refine_semantic_block(block))
        return [block for block in refined if block.strip()]

    def _block_score(self, block: str, anchors: Set[str]) -> int:
        lowered = block.lower()
        score = 0
        for anchor in anchors:
            if not anchor or not self._contains_anchor(lowered, anchor):
                continue
            score += 12 if "`" + anchor + "`" in lowered else 5
        if block.strip().startswith("### `"):
            score += 2
        return score

    def _required_blocks_for_plan(self, blocks: Sequence[str], plan: RustFilePlan) -> List[int]:
        required: List[int] = []
        required_tokens = set(item.lower() for item in plan.source_functions + plan.owns if item)
        required_tokens.update(
            stem.lower()
            for stem in (_stem(source) for source in plan.source_files)
            if stem
        )
        for index, block in enumerate(blocks):
            lowered = block.lower()
            if any(self._contains_anchor(lowered, token) for token in required_tokens):
                required.append(index)
        return required

    def _section_focus_anchors(self, plan: RustFilePlan, section: DocSection) -> Set[str]:
        anchors: Set[str] = set()
        values = (
            list(plan.owns)
            + list(plan.source_functions)
            + list(plan.spec_queries)
            + list(plan.source_files)
            + list(section.source_files)
        )
        for value in values:
            normalized = str(value or "").strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            anchors.add(lowered)
            stem = _stem(lowered)
            if stem:
                anchors.add(stem.lower())
            snake = _snake_case(normalized)
            if snake:
                anchors.add(snake.lower())
            for part in _identifier_parts(normalized):
                if len(part) >= 4 and part.lower() not in self.GENERIC_ANCHOR_PARTS:
                    anchors.add(part.lower())
        return anchors

    def _section_is_required_for_plan(self, section: DocSection, plan: RustFilePlan) -> bool:
        if self._section_matches_plan_source(section, plan):
            return True
        plan_symbols = set(plan.owns) | set(plan.source_functions)
        if not plan_symbols:
            return False
        return any(symbol in section.symbols for symbol in plan_symbols)

    def _should_start_new_group(self, previous_line: str, current_line: str) -> bool:
        prev = (previous_line or "").strip()
        curr = (current_line or "").strip()
        if not prev or not curr:
            return False
        prev_is_list = bool(re.match(r"^([-*+]|\d+\.)\s+", prev))
        curr_is_list = bool(re.match(r"^([-*+]|\d+\.)\s+", curr))
        if prev_is_list and curr_is_list:
            return False
        return prev_is_list != curr_is_list

    def _refine_semantic_block(self, block: str, max_list_items: int = 8) -> List[str]:
        lines = [line.rstrip() for line in (block or "").splitlines() if line.strip()]
        if not lines:
            return []

        heading_lines: List[str] = []
        body_start = 0
        for index, line in enumerate(lines):
            if re.match(r"^#{1,4}\s+\S", line):
                heading_lines.append(line)
                body_start = index + 1
                continue
            break

        body = lines[body_start:]
        if len(body) <= max_list_items:
            return [block.strip()]
        if not body or not all(re.match(r"^([-*+]|\d+\.)\s+", line.strip()) for line in body):
            return [block.strip()]

        chunks: List[str] = []
        for start in range(0, len(body), max_list_items):
            chunk_lines = body[start:start + max_list_items]
            if start == 0 and heading_lines:
                chunks.append("\n".join(heading_lines + chunk_lines).strip())
            else:
                chunks.append("\n".join(chunk_lines).strip())
        return chunks

    def _contains_anchor(self, text: str, anchor: str) -> bool:
        lowered = (text or "").lower()
        token = (anchor or "").strip().lower()
        if not lowered or not token:
            return False
        if token in lowered:
            return True
        if re.fullmatch(r"[a-z0-9_]+", token):
            return re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", lowered) is not None
        return False

    def _select_blocks_by_soft_budget(
        self,
        blocks: Sequence[str],
        ordered_indexes: Sequence[int],
        required_indexes: Sequence[int],
        soft_max_chars: int,
    ) -> List[str]:
        selected_blocks: List[str] = []
        current_size = 0
        required_set = set(required_indexes)

        for index in ordered_indexes:
            block = blocks[index].strip()
            if not block:
                continue
            block_size = len(block) + (2 if selected_blocks else 0)
            if current_size + block_size <= soft_max_chars:
                selected_blocks.append(block)
                current_size += block_size
                continue
            if not selected_blocks or index in required_set:
                selected_blocks.append(block)
                current_size += block_size
            break
        return selected_blocks

    def _focused_section_excerpt_for_query(
        self,
        section: DocSection,
        query_tokens: Set[str],
        soft_max_chars: int = 2400,
    ) -> str:
        text = (section.text or "").strip()
        if not text:
            return ""
        blocks = self._semantic_blocks(text)
        if not blocks:
            return text

        anchors = {token for token in query_tokens if len(token) >= 3 and token not in self.GENERIC_ANCHOR_PARTS}
        scored_indexes = [
            (self._block_score(block, anchors), index)
            for index, block in enumerate(blocks)
        ]
        scored_indexes = [(score, index) for score, index in scored_indexes if score > 0]
        if not scored_indexes:
            return text if len(text) <= soft_max_chars else blocks[0].strip()

        ordered_indexes = [index for _, index in sorted(scored_indexes, key=lambda item: (-item[0], item[1]))]
        selected_blocks = self._select_blocks_by_soft_budget(
            blocks=blocks,
            ordered_indexes=ordered_indexes,
            required_indexes=[],
            soft_max_chars=soft_max_chars,
        )
        return "\n\n".join(block for block in selected_blocks if block).strip()

    def _section_matches_plan_source(self, section: DocSection, plan: RustFilePlan) -> bool:
        if not section.source_files or not plan.source_files:
            return False
        plan_stems = {_stem(source).lower() for source in plan.source_files if _stem(source)}
        section_stems = {_stem(source).lower() for source in section.source_files if _stem(source)}
        return bool(plan_stems & section_stems)

    def _section_score(self, section: DocSection, query_tokens: Set[str], query_text: str) -> int:
        score = len(query_tokens & section.tokens) * 6
        lowered = (query_text or "").lower()
        for source_file in section.source_files:
            if source_file.lower() in lowered or _stem(source_file).lower() in query_tokens:
                score += 20
        for symbol in section.symbols:
            symbol_lower = symbol.lower()
            if symbol_lower in lowered or symbol_lower in query_tokens:
                score += 10
        title_lower = section.title.lower()
        score += sum(4 for token in query_tokens if token in title_lower)
        return score

    def _manifest_excerpt_for_plan(self, plan: RustFilePlan) -> str:
        manifest_sections = [section for section in self.sections if section.kind == "manifest"]
        if not manifest_sections:
            return ""
        text = "\n\n".join(section.text for section in manifest_sections[:2])
        lines: List[str] = []
        plan_stems = {_stem(source).lower() for source in plan.source_files if _stem(source)}
        matched_any = False
        for line in text.splitlines():
            lowered = line.lower()
            keep = line.startswith("#") or "源文件" in line or "头文件" in line
            if not keep and line.strip().startswith("-"):
                keep = any(source.lower() in lowered for source in plan.source_files)
                if not keep:
                    keep = any(stem in lowered for stem in plan_stems if stem)
            if keep:
                lines.append(line)
                if line.strip().startswith("-"):
                    matched_any = True
        if matched_any:
            return "\n".join(lines).strip()
        return ""

    def _constitution_excerpt(self) -> str:
        constitution_sections = [section for section in self.sections if section.kind == "constitution"]
        if not constitution_sections:
            return ""
        text = "\n\n".join(section.text for section in constitution_sections[:1])
        blocks = self._semantic_blocks(text)
        if not blocks:
            return text

        preferred: List[Tuple[int, int]] = []
        for index, block in enumerate(blocks):
            lowered = block.lower()
            score = 0
            for keyword in ["行为等价", "rust", "重构", "禁止", "contract", "接口", "模块"]:
                if keyword in lowered:
                    score += 3
            if score > 0:
                preferred.append((score, index))

        if not preferred:
            return "\n\n".join(blocks[:3]).strip()

        ordered_indexes = [index for _, index in sorted(preferred, key=lambda item: (-item[0], item[1]))]
        selected_blocks = self._select_blocks_by_soft_budget(
            blocks=blocks,
            ordered_indexes=ordered_indexes,
            required_indexes=[],
            soft_max_chars=1800,
        )
        return "\n\n".join(block for block in selected_blocks if block).strip()

    def overview(self, max_chars: int = 12000) -> str:
        lines: List[str] = []
        by_kind: Dict[str, int] = {}
        for section in self.sections:
            by_kind[section.kind] = by_kind.get(section.kind, 0) + 1
        lines.append("Document section statistics:")
        for kind in sorted(by_kind):
            lines.append(f"- {kind}: {by_kind[kind]}")
        lines.append("")

        if isinstance(self.translation_contract, dict) and self.translation_contract:
            boundary = self.translation_contract.get("generation_boundary", {})
            functions = self.translation_contract.get("functions", [])
            types = self.translation_contract.get("types", [])
            lines.append("Migration contract statistics:")
            lines.append(f"- project kind: {self.translation_contract.get('project', {}).get('kind', 'unknown')}")
            lines.append(f"- allowed_rust_files: {len(boundary.get('allowed_rust_files', []) if isinstance(boundary, dict) else [])}")
            lines.append(f"- functions: {len(functions) if isinstance(functions, list) else 0}")
            lines.append(f"- types: {len(types) if isinstance(types, list) else 0}")
            lines.append(f"- dependency_policy: {boundary.get('dependency_policy', 'unspecified') if isinstance(boundary, dict) else 'unspecified'}")
            lines.append("")

        lines.append("C source file to candidate Rust file mapping:")
        for source_file in self.source_files:
            rust_path = self._rust_path_for_source(source_file)
            if rust_path:
                lines.append(f"- {source_file} -> {rust_path}")

        if self.function_signatures:
            lines.append("")
            lines.append("Extracted function declaration count:")
            lines.append(f"- signatures: {len(self.function_signatures)}")

        return "\n".join(lines)


__all__ = [
    "DocSection",
    "RustFilePlan",
    "RustGenerationSpecAgent",
    "RustGenerationSpecPrompts",
]
