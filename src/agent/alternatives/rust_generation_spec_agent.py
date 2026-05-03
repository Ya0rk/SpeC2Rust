import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


def _clip(text: str, max_chars: int) -> str:
    content = text or ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "\n...[截断]..."


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
    def _join(values: Sequence[str], default: str, limit: int = 24) -> str:
        cleaned = [str(value).strip() for value in values or [] if str(value).strip()]
        if not cleaned:
            return default
        return ", ".join(cleaned[:limit])

    @classmethod
    def rewrite_contract(cls, planned=None) -> str:
        c_functions = cls._join(
            cls._attr(planned, "source_functions", []),
            "当前文件关联的 C 函数",
            limit=24,
        )
        rust_symbols = cls._join(
            cls._attr(planned, "owns", []),
            "当前文件规划的 Rust 类型和方法",
            limit=24,
        )
        return f"""目标是行为等价，不是 C ABI 等价。
- C 文件名、C 类型名、C 函数名只作为溯源证据；目标 Rust API 必须使用 Rust 命名、所有权和模块组织。
- 当前 C 证据：{c_functions}
- 当前目标 Rust 符号：{rust_symbols}
- `xxx_t`/`struct xxx` 应重构为 `CamelCase` Rust 类型，例如 `quadtree_bounds_t` -> `Bounds`。
- `xxx_new/create/init` 应重构为 `Type::new` 或 `Default`，不要公开 `xxx_new` 自由函数。
- `xxx_free/destroy/delete` 应由所有权和 `Drop` 表达，通常不需要公开 `free` API。
- C 中的 `NULL`、可空指针、缺失值应重构为 `Option<T>`。
- C 中的状态码应按语义重构为 `bool`、`Option<T>` 或 `Result<T, E>`，不要机械返回 `i32`。
- C 中的 `void *` 或用户数据指针应恢复为泛型参数、具体拥有类型、引用或 trait object；不要使用 `c_void`。
- C 回调应重构为闭包参数，例如 `impl FnMut(...)` 或泛型 `F: FnMut(...)`，不要暴露原始函数指针和用户数据指针组合。
- 树、链表、集合等结构应使用 `Option<Box<T>>`、`Vec<T>`、切片、引用和借用表达所有权关系。
- 禁止在普通重写代码中使用 `unsafe`、`*mut`、`*const`、`NonNull`、`Box::into_raw`、`Box::from_raw`、`std::ptr`、`core::ptr`、`c_void`、`#[repr(C)]`、`extern \"C\"`、`#[no_mangle]`。
- 禁止用 `#[allow(non_camel_case_types)]`、`#[allow(non_snake_case)]` 等方式掩盖 C 风格命名。
- 示例映射：`foo_new` -> `Foo::new`，`foo_extend` -> `Foo::extend`，`tree_insert` -> `Tree::insert`，`tree_search` -> `Tree::search`。"""

    @staticmethod
    def evidence_boundary() -> str:
        return """证据边界：
- `translation_contract.json` 是最高优先级范围契约；若与普通 Markdown 冲突，以 contract 为准。
- `docs/rewrite-context/02_interfaces` 提供接口事实，不代表目标 Rust API 必须照抄 C 名称。
- `docs/rewrite-context/03_behaviors` 提供行为约束，应优先用于控制返回语义、边界条件和副作用。
- `specs/*/spec.md` 提供模块级目标和约束；`plan.md`/`tasks.md` 只作为次级辅助，不应覆盖接口与行为事实。
- `.specify/memory/constitution.md` 是治理约束，用于限制范围、依赖和质量要求。
- pointer/macro 风险文档只能作为迁移风险提示，不能直接授权 FFI/raw pointer 设计。"""

    @classmethod
    def context_guide(cls, planned=None) -> str:
        path = cls._attr(planned, "path", "当前目标文件")
        return f"""=== RUST GENERATION CONTEXT GUIDE ===
目标文件：{path}
使用方式：
- 下面的 spec section 是按目标文件筛选后的局部上下文，不是完整项目 dump。
- 优先从当前目标 Rust 符号、source_functions、source_files 中确认职责边界。
- 如果行为、类型字段、调用关系或依赖仍不清楚，使用 `<CGR_READ>` 请求更多 spec/source/rust/registry。
- 不要因为上下文片段中出现其它 C 函数，就把其它模块职责搬进当前文件。

{cls.evidence_boundary()}

{cls.rewrite_contract(planned)}"""

    @staticmethod
    def project_planning_system_prompt() -> str:
        return (
            "你是严谨的 Rust 项目结构规划助手。"
            "你必须基于 spec 和迁移契约规划文件结构，输出 <CGR_PLAN>JSON</CGR_PLAN> 或 <CGR_READ> 请求。"
            "禁止把 C 函数名当成目标 Rust API，禁止规划无证据扩展功能。"
        )

    @classmethod
    def project_planning_prompt(cls, fallback_files: Sequence[str], static_context: str) -> str:
        files_json = "\n".join(f'- "{path}"' for path in fallback_files)
        return f"""请规划这个 C 到 Rust 重写项目的 Rust 文件结构和自底向上生成顺序。

你只能输出 JSON，并用 <CGR_PLAN> 包裹。JSON schema：
{{
  "files": [
    {{
      "path": "src/example.rs",
      "role": "该文件职责，必须克制",
      "owns": ["该文件唯一拥有的目标 Rust 类型、方法或自由函数；禁止填写 C 函数名"],
      "depends_on": ["必须先生成的文件路径"],
      "spec_queries": ["生成该文件时最需要读取的 spec 关键词"],
      "source_files": ["对应 C 源文件"],
      "source_functions": ["对应 C 函数；只作证据"]
    }}
  ],
  "order": ["Cargo.toml", "src/example.rs", "src/lib.rs", "README.md"]
}}

规划规则：
1. 自底向上：基础类型、错误、常量、数据结构先生成；聚合容器和算法后生成；lib.rs 最后由本地程序重建。
2. 一个 Rust 类型只能由一个文件拥有；不要让 node.rs、data.rs、tree.rs 重复定义同一结构体。
3. 每个文件只承担一个明确职责。文件太大时优先按类型或模块拆分，但不要无证据扩展工程规模。
4. `owns` 必须写 Rust 目标符号，例如 `Bounds`、`Bounds::new`、`Quadtree::insert`；C 函数名只能放进 `source_functions` 或 `spec_queries`。
5. 不要规划 spec/C 源码未体现的功能；不要主动添加 serde、async、线程安全、恢复机制等。
6. 如果有迁移契约 allowed_rust_files，必须只在允许文件集合内选择。
7. 除非配置或文档明确要求，不规划 tests/examples/benches。
8. 如果信息不足，使用 <CGR_READ> 请求更多 spec/source/registry；不要猜。

可选兜底文件集合：
{files_json or "- (empty)"}

{cls.evidence_boundary()}

静态项目上下文：
{static_context}
"""

    @staticmethod
    def file_generation_system_prompt() -> str:
        return (
            "你是一个按需读取上下文的 Rust 代码生成助手。"
            "你的任务是生成单个目标文件，严格遵守已规划文件边界、已有符号表和迁移契约。"
            "你必须重构为 Rust 风格 API，而不是模拟 C ABI；禁止 raw pointer、unsafe、c_void 和 C 风格函数名。"
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
        return f"""请生成目标文件的最终内容。

目标文件：{path}
文件职责：{cls._attr(planned, "role", "") or '按计划实现该文件职责'}
该文件唯一拥有的目标 Rust 符号：{cls._join(cls._attr(planned, "owns", []), '(由当前文件内容自然决定，但不得重复已有符号)')}
对应 C 源文件：{cls._join(cls._attr(planned, "source_files", []), '(无直接源文件映射)')}
对应 C 函数（只作为行为证据，禁止照抄为 Rust API 名）：{cls._join(cls._attr(planned, "source_functions", []), '(无直接函数映射)')}
必须先依赖的文件：{cls._join(cls._attr(planned, "depends_on", []), '(无明确依赖)')}
允许/计划文件集合：{cls._join(planned_files, '(未提供)', limit=80)}

项目生成计划：
{plan_summary}

已生成 Rust 符号表：
{registry_summary}

当前文件相关 spec/source 上下文：
{spec_context or '(当前没有匹配到 spec 片段，可用 <CGR_READ> 请求 spec)'}

相关 C 源码（关键函数已内联，其余为索引）：
{source_context or '(当前没有匹配到源码，可用 <CGR_READ> 请求 source)'}

Rust 化迁移契约：
{cls.rewrite_contract(planned)}

生成约束：
1. 只输出 `{path}` 的最终内容，不要解释。
2. 不要重新定义符号表中已经由其他文件拥有的 struct/enum/type/trait/free fn/const/static。
3. 已生成 Rust 符号表中的 `references` 包含 public/private、函数参数、返回类型和结构体字段；跨文件只能引用 public 符号，private 符号只能在其定义文件内部使用。
4. 调用已有函数或方法时必须匹配符号表中的参数列表和返回类型；访问结构体字段时只能访问 `references` 中存在的 public field；不要只凭 C 源码字段名猜 Rust 成员。
5. 可以 `use crate::...` 引用已生成符号表中的 public 模块和 public 符号；不要引用未规划模块。
6. 如果必须引用尚未生成的文件，优先改为通过当前文件已有依赖或标准库实现；不要凭空创建新模块。
7. 不要添加无证据功能，不要引入未授权第三方依赖，不要生成内联测试模块，除非配置明确允许。
8. 不要生成 C ABI 适配层，不要公开 `项目名前缀_*`/`*_free`/`*_new` 这类 C 风格自由函数。
9. 不要使用 raw pointer、`unsafe`、`c_void`、`repr(C)` 或 `extern \"C\"` 来模拟原 C 项目。
10. 代码应符合 Rust 命名习惯：类型 `CamelCase`，方法/函数 `snake_case`，模块职责清晰。
11. C 源码区域中只内联了关键函数，其余函数仅提供索引。如果需要查看索引中某个函数的完整源码，或信息不足，请输出：
<CGR_READ>
[{{"kind":"source","query":"函数名或文件名"}}, {{"kind":"spec","query":"关键词"}}, {{"kind":"rust","query":"src/existing.rs"}}, {{"kind":"registry"}}]
</CGR_READ>
一次可发多个请求。source 支持按函数名（如 "quadtree_insert"）或文件名（如 "node.c"）查询。
12. 信息足够时输出完整文件内容，并在最后单独添加 `<CGR_DONE>`。
"""

    @staticmethod
    def read_materials_followup(materials: str) -> str:
        return (
            "下面是你请求读取的材料。请继续；如果信息已经足够，直接输出目标结果。"
            "不要重复请求同一材料，不要把读取到的无关模块职责搬进当前文件。\n\n"
            + materials
        )

    @staticmethod
    def repair_system_prompt() -> str:
        return (
            "你是严格的 Rust 文件边界和 Rust 风格修复助手。"
            "只修当前文件，不能扩写项目功能，不能保留 C ABI 模拟层。"
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
        return f"""上一次生成的 `{path}` 违反了项目边界，请在保持文件职责不变的前提下修正。

违规项：
{findings_text}

已有符号表：
{registry_summary}

项目计划：
{plan_summary}

当前错误内容：
```rust
{current_content}
```

Rust 化迁移契约：
{cls.rewrite_contract(planned)}

要求：
1. 只输出 `{path}` 的完整修正版内容。
2. 删除重复定义和越界能力，不要把其它文件职责搬进当前文件。
3. 跨文件引用只能使用符号表中标记为 public 的引用；调用已有函数或方法必须匹配符号表里的参数和返回类型，字段访问也必须存在于符号表 field 引用中。
4. 如果违规项来自 C ABI 或 C 风格代码，必须重构为 Rust 类型、方法、Option/Result、所有权和闭包；不要继续修补 raw pointer 版本。
5. 如果需要其它上下文，可以使用 <CGR_READ>，否则直接输出最终内容并以 <CGR_DONE> 结束。
"""

    @staticmethod
    def force_write_system_prompt() -> str:
        return (
            "你是 Rust 文件最终写入决策助手。"
            "你会收到仍然违反边界检查的文件。"
            "优先修复；只有当你明确认为当前内容必须保留且用户需要强制推进时，才允许输出 <CGR_FORCE_WRITE>。"
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
        return f"""`{path}` 修复后仍然触发禁止写入规则。请做最后一次决策。

剩余违规项：
{findings_text}

已有符号表：
{registry_summary}

项目计划：
{plan_summary}

当前候选内容：
```rust
{current_content}
```

决策规则：
1. 首选：继续修复文件，让它不再违反上述规则。此时只输出完整修正版内容，并以 `<CGR_DONE>` 结束。
2. 如果你认为这些违规是误报，或者为了保持项目可继续生成必须写入当前候选内容，可以强制写入。
3. 强制写入时必须输出完整文件内容，并额外包含：
<CGR_FORCE_WRITE>
用一句话说明为什么必须越过当前禁止写入规则。
</CGR_FORCE_WRITE>
4. 没有 `<CGR_FORCE_WRITE>` 标记时，外层 agent 仍会按禁止写入处理。
5. 不要只输出标记；必须输出可写入 `{path}` 的完整文件内容。
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
            return RustFilePlan(path=normalized, role="Cargo package manifest，本地生成最小可编译配置")
        if normalized == "src/lib.rs":
            return RustFilePlan(path=normalized, role="crate 入口，本地根据已生成模块和符号表重建")
        if normalized.lower() == "readme.md":
            return RustFilePlan(path=normalized, role="项目说明文档，只说明构建、使用和当前能力")

        source_files = self._source_files_for_rust_path(normalized)
        source_functions: List[str] = []
        source_types: List[str] = []
        for source_file in source_files:
            source_functions.extend(self.source_to_functions.get(source_file, []))
            source_types.extend(self.source_to_types.get(source_file, []))

        owns = self._target_rust_symbols_for_sources(normalized, source_files, source_functions, source_types)
        stem = _stem(normalized)
        spec_queries = _dedupe([stem] + source_files + source_functions[:12] + source_types[:8])
        role = (
            f"根据 `{', '.join(source_files)}` 的行为证据实现 Rust 化 API；C 函数名只作溯源，不作为目标 API"
            if source_files
            else f"实现 `{stem}` 相关的 Rust 模块，不承载其它文件职责；目标 API 必须 Rust 化"
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

    def _source_files_for_rust_path(self, rust_path: str) -> List[str]:
        normalized = rust_path.replace("\\", "/")
        base = _stem(normalized)
        c_matches: List[str] = []
        h_matches: List[str] = []
        for source_file in self.source_files:
            if _stem(source_file).lower() == base.lower():
                if source_file.endswith(".c"):
                    c_matches.append(source_file)
                elif source_file.endswith(".h"):
                    h_matches.append(source_file)
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
            if total + len(block) > max_chars and parts:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts).strip() or "没有找到匹配的 spec section。"

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
        lines.append("文档 section 统计：")
        for kind in sorted(by_kind):
            lines.append(f"- {kind}: {by_kind[kind]}")
        lines.append("")

        if isinstance(self.translation_contract, dict) and self.translation_contract:
            boundary = self.translation_contract.get("generation_boundary", {})
            functions = self.translation_contract.get("functions", [])
            types = self.translation_contract.get("types", [])
            lines.append("迁移契约统计：")
            lines.append(f"- project kind: {self.translation_contract.get('project', {}).get('kind', 'unknown')}")
            lines.append(f"- allowed_rust_files: {len(boundary.get('allowed_rust_files', []) if isinstance(boundary, dict) else [])}")
            lines.append(f"- functions: {len(functions) if isinstance(functions, list) else 0}")
            lines.append(f"- types: {len(types) if isinstance(types, list) else 0}")
            lines.append(f"- dependency_policy: {boundary.get('dependency_policy', 'unspecified') if isinstance(boundary, dict) else 'unspecified'}")
            lines.append("")

        lines.append("C 源文件到 Rust 文件候选映射：")
        for source_file in self.source_files:
            rust_path = self._rust_path_for_source(source_file)
            if rust_path:
                lines.append(f"- {source_file} -> {rust_path}")

        if self.function_signatures:
            lines.append("")
            lines.append("已提取的函数声明数：")
            lines.append(f"- signatures: {len(self.function_signatures)}")

        return _clip("\n".join(lines), max_chars)


__all__ = [
    "DocSection",
    "RustFilePlan",
    "RustGenerationSpecAgent",
    "RustGenerationSpecPrompts",
]
