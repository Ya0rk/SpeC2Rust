import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from agent.rust_agent import RustAgent
from agent.alternatives.contextual_spec_agent import ContextualSpecAgent
from config.config import Config


def _dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered = []
    for item in items:
        value = (item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _clip_text(text: str, max_chars: int) -> str:
    content = text or ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "\n...[截断]..."


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _tokenize_text(text: str) -> Set[str]:
    tokens: Set[str] = set()
    for piece in re.split(r"[^A-Za-z0-9_]+", text or ""):
        if not piece:
            continue
        lowered_piece = piece.lower()
        tokens.add(lowered_piece)
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", piece):
            lowered = part.lower()
            if lowered:
                tokens.add(lowered)
        for part in lowered_piece.split("_"):
            if part:
                tokens.add(part)

    stopwords = {
        "src",
        "lib",
        "main",
        "mod",
        "rs",
        "md",
        "json",
        "toml",
        "docs",
        "specs",
        "rust",
        "port",
        "module",
        "root",
        "project",
        "context",
        "rewrite",
    }
    return {token for token in tokens if len(token) > 1 and token not in stopwords}


def _pascal_case(name: str) -> str:
    pieces = [item for item in re.split(r"[^A-Za-z0-9]+", name or "") if item]
    if not pieces:
        return ""
    return "".join(piece[:1].upper() + piece[1:] for piece in pieces)


@dataclass
class DocumentSlice:
    path: str
    rel_path: str
    title: str
    text: str
    kind: str
    module: str = ""
    tokens: Set[str] = field(default_factory=set)


class SpecDocumentIndex:
    """
    Lightweight index over c_docs.

    The index stores the available documents and selects a small, relevant subset
    for each Rust file. It intentionally keeps the raw document text out of the
    static project prompt.
    """

    def __init__(self, doc_contents: Optional[Dict[str, str]] = None):
        self.slices: List[DocumentSlice] = []
        self.root: str = ""
        if doc_contents:
            self.build(doc_contents)

    def build(self, doc_contents: Dict[str, str]):
        paths = [os.path.abspath(path) for path in doc_contents.keys()]
        self.root = self._infer_root(paths)
        self.slices = []

        for path, content in doc_contents.items():
            normalized = path.replace("\\", "/")
            lowered = normalized.lower()
            if lowered.endswith("translation_lint.json"):
                continue

            rel_path = self._relative_path(path)
            title = self._extract_title(content, rel_path)
            module = self._infer_module(rel_path)
            kind = self._infer_kind(rel_path)
            tokens = _tokenize_text(f"{rel_path} {title} {module} {self._heading_text(content)}")
            self.slices.append(
                DocumentSlice(
                    path=path,
                    rel_path=rel_path,
                    title=title,
                    text=content or "",
                    kind=kind,
                    module=module,
                    tokens=tokens,
                )
            )

    def _infer_root(self, paths: Sequence[str]) -> str:
        if not paths:
            return ""

        for path in paths:
            parts = Path(path).parts
            lowered = [part.lower() for part in parts]
            if "c_docs" in lowered:
                index = lowered.index("c_docs")
                return str(Path(*parts[: index + 1]))

        try:
            return os.path.commonpath(paths)
        except ValueError:
            return os.path.dirname(paths[0])

    def _relative_path(self, path: str) -> str:
        if not self.root:
            return path.replace("\\", "/")
        try:
            return os.path.relpath(path, self.root).replace("\\", "/")
        except ValueError:
            return path.replace("\\", "/")

    def _extract_title(self, content: str, rel_path: str) -> str:
        for line in (content or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or rel_path
        return rel_path

    def _heading_text(self, content: str, limit: int = 80) -> str:
        headings = []
        for line in (content or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                headings.append(stripped.lstrip("#").strip())
                if len(headings) >= limit:
                    break
        return " ".join(headings)

    def _infer_kind(self, rel_path: str) -> str:
        normalized = rel_path.replace("\\", "/").lower()
        if ".specify/memory" in normalized:
            return "constitution"
        if normalized.endswith("00_repo_manifest.md"):
            return "manifest"
        if "/01_subsystems/" in normalized:
            return "subsystem"
        if "/02_interfaces/" in normalized:
            return "interface"
        if "/03_behaviors/" in normalized:
            return "behavior"
        if "/04_gaps_and_risks/" in normalized:
            return "risk"
        in_specs = normalized.startswith("specs/") or "/specs/" in normalized
        if in_specs and normalized.endswith("/spec.md"):
            return "spec"
        if in_specs and normalized.endswith("/plan.md"):
            return "plan"
        if in_specs and normalized.endswith("/tasks.md"):
            return "tasks"
        if normalized.endswith(".json"):
            return "json"
        return "doc"

    def _infer_module(self, rel_path: str) -> str:
        normalized = rel_path.replace("\\", "/")
        parts = normalized.split("/")

        if "specs" in parts:
            index = parts.index("specs")
            if index + 1 < len(parts):
                folder = parts[index + 1]
                match = re.match(r"\d+-(.+?)-rust-port$", folder)
                if match:
                    return match.group(1).replace("-", "_")

        stem = os.path.splitext(os.path.basename(normalized))[0]
        match = re.match(r"\d+[_-](.+)$", stem)
        if match:
            stem = match.group(1)
        return stem.replace("-", "_")

    def overview(self, max_chars: int = 12000) -> str:
        grouped: Dict[str, List[DocumentSlice]] = {}
        for item in self.slices:
            grouped.setdefault(item.kind, []).append(item)

        lines = []
        for kind in sorted(grouped.keys()):
            lines.append(f"[{kind}]")
            for item in sorted(grouped[kind], key=lambda current: current.rel_path):
                module = f" module={item.module}" if item.module else ""
                lines.append(f"- {item.rel_path}{module}: {item.title}")
        return _clip_text("\n".join(lines), max_chars)

    def _target_module(self, rel_path: str) -> str:
        """从 Rust 文件路径推导目标模块名：src/sds.rs → sds"""
        stem = os.path.splitext(os.path.basename((rel_path or "").replace("\\", "/")))[0].lower()
        if stem in {"lib", "main", "mod", "cargo", "readme"}:
            return ""
        return stem.replace("-", "_")

    def select_for_file(
        self,
        rel_path: str,
        owns: Optional[Sequence[str]] = None,
        spec_queries: Optional[Sequence[str]] = None,
    ) -> str:
        target_mod = self._target_module(rel_path)
        query = " ".join([rel_path or "", " ".join(owns or []), " ".join(spec_queries or [])])

        if target_mod:
            module_slices = [
                s for s in self.slices
                if s.module and s.module.lower() == target_mod
            ]
        else:
            module_slices = []

        if module_slices:
            selected = self._rank_slices_in(module_slices, query)
        else:
            ranked_all = self._rank_slices(query)
            selected = ranked_all[:3]
        return self.format_slices(selected)

    def select_for_query(self, query: str, max_slices: int = 5) -> str:
        selected = self._rank_slices(query)[:max_slices]
        return self.format_slices(selected)

    def _rank_slices_in(self, candidates: Sequence[DocumentSlice], query: str) -> List[DocumentSlice]:
        """对已预筛选的 slice 列表按相关度排序。"""
        query_tokens = _tokenize_text(query)
        if not query_tokens:
            return list(candidates)
        kind_bonus = {"interface": 8, "spec": 6, "plan": 5, "behavior": 4, "subsystem": 3}
        scored = []
        for idx, item in enumerate(candidates):
            path_text = f"{item.rel_path} {item.title} {item.module}".lower()
            overlap = len(query_tokens & item.tokens)
            direct_hits = sum(1 for t in query_tokens if t in path_text)
            score = overlap * 5 + direct_hits * 8 + kind_bonus.get(item.kind, 0)
            if score > 0:
                scored.append((score, -idx, item))
        scored.sort(key=lambda c: (-c[0], c[1]))
        return [item for _, _, item in scored]

    def _rank_slices(self, query: str) -> List[DocumentSlice]:
        query_tokens = _tokenize_text(query)
        if not query_tokens:
            return list(self.slices)

        scored: List[Tuple[int, int, str, DocumentSlice]] = []
        kind_bonus = {
            "interface": 8,
            "spec": 6,
            "plan": 5,
            "behavior": 4,
            "subsystem": 3,
            "tasks": 2,
            "manifest": 1,
            "constitution": 1,
        }
        for index, item in enumerate(self.slices):
            path_text = f"{item.rel_path} {item.title} {item.module}".lower()
            overlap = len(query_tokens & item.tokens)
            direct_hits = sum(1 for token in query_tokens if token in path_text)
            body_hits = sum(1 for token in query_tokens if token in (item.text[:5000].lower()))
            base_score = overlap * 5 + direct_hits * 8 + min(body_hits, 8)
            if base_score > 0:
                score = base_score + kind_bonus.get(item.kind, 0)
                scored.append((score, -index, item.rel_path, item))

        scored.sort(key=lambda current: (-current[0], current[1], current[2]))
        return [item for _, _, _, item in scored]

    def format_slices(self, slices: Sequence[DocumentSlice]) -> str:
        parts = []
        for item in slices:
            block = (
                f"\n\n=== SPEC {item.rel_path} | kind={item.kind}"
                f"{' | module=' + item.module if item.module else ''} ===\n"
                f"{item.text}\n"
            )
            parts.append(block)
        return "".join(parts).strip()

    def infer_candidate_rust_files(self) -> List[str]:
        text = "\n".join(item.text for item in self.slices if item.kind in {"manifest", "interface", "subsystem"})
        c_files = re.findall(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.c)\b", text)
        candidates = ["Cargo.toml"]

        for c_file in _dedupe_keep_order(c_files):
            normalized = c_file.replace("\\", "/")
            stem = os.path.splitext(os.path.basename(normalized))[0]
            if not stem:
                continue
            lowered = stem.lower()
            if lowered in {"test", "tests"}:
                candidates.append(f"tests/{stem}.rs")
            elif "bench" in lowered:
                candidates.append(f"benches/{stem}.rs")
            elif lowered == "main":
                candidates.append("src/main.rs")
            else:
                candidates.append(f"src/{stem}.rs")

        candidates.extend(["src/lib.rs", "README.md"])
        return _dedupe_keep_order(candidates)


@dataclass
class PlannedFile:
    path: str
    role: str = ""
    owns: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    spec_queries: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    source_functions: List[str] = field(default_factory=list)


@dataclass
class RustSymbolReference:
    path: str
    kind: str
    name: str
    visibility: str = "public"
    owner_type: str = ""
    params: List[str] = field(default_factory=list)
    return_type: str = ""
    signature: str = ""

    @property
    def is_public(self) -> bool:
        return self.visibility != "private"

    def display_name(self) -> str:
        if self.owner_type:
            return f"{self.owner_type}::{self.name}"
        return self.name

    def display_signature(self) -> str:
        if self.kind == "field":
            suffix = f": {self.return_type}" if self.return_type else ""
            return f"{self.display_name()}{suffix}"
        if self.kind not in {"function", "method"}:
            return self.display_name()
        params = ", ".join(self.params)
        suffix = f" -> {self.return_type}" if self.return_type else ""
        return f"{self.display_name()}({params}){suffix}"

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "name": self.name,
            "visibility": self.visibility,
            "public": self.is_public,
            "owner_type": self.owner_type,
            "params": list(self.params),
            "return_type": self.return_type,
            "signature": self.signature or self.display_signature(),
        }

    def detail_line(self) -> str:
        vis = "pub " if self.is_public else ""
        kind_prefix = {"function": "fn ", "method": "fn ", "field": "field ", "type": "type ", "constant": "const "}.get(self.kind, f"{self.kind} ")
        return f"{vis}{kind_prefix}{self.display_signature()}: path={self.path or '?'}"


@dataclass
class RustFileSymbols:
    path: str
    modules: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    constants: List[str] = field(default_factory=list)
    methods: Dict[str, List[str]] = field(default_factory=dict)
    fields: Dict[str, List[str]] = field(default_factory=dict)
    references: List[RustSymbolReference] = field(default_factory=list)

    def all_exportable_items(self) -> List[str]:
        public_refs = [
            ref.name
            for ref in self.references
            if ref.is_public and ref.kind in {"type", "function", "constant"}
        ]
        return _dedupe_keep_order(public_refs or self.types + self.functions + self.constants)


class RustProjectRegistry:
    """
    Symbol table for generated Rust files.

    It is deliberately simple: the registry is a guardrail and prompt input, not
    a full Rust parser.
    """

    def __init__(self):
        self.files: Dict[str, RustFileSymbols] = {}

    def update_file(self, rel_path: str, content: str):
        normalized = rel_path.replace("\\", "/")
        self.files[normalized] = self.extract_symbols(normalized, content)

    def remove_file(self, rel_path: str):
        self.files.pop(rel_path.replace("\\", "/"), None)

    def extract_symbols(self, rel_path: str, content: str) -> RustFileSymbols:
        text = self._strip_comments(content or "")
        top_level_text = self._strip_nested_item_blocks(text, keywords=("impl", "trait"))
        symbols = RustFileSymbols(path=rel_path.replace("\\", "/"))
        references = self._extract_top_level_references(symbols.path, top_level_text)
        method_refs = self._extract_method_references(symbols.path, text)
        references.extend(ref for refs in method_refs.values() for ref in refs)

        symbols.modules = _dedupe_keep_order(ref.name for ref in references if ref.kind == "module")
        symbols.types = _dedupe_keep_order(ref.name for ref in references if ref.kind == "type")
        symbols.functions = _dedupe_keep_order(ref.name for ref in references if ref.kind == "function")
        symbols.constants = _dedupe_keep_order(ref.name for ref in references if ref.kind == "constant")
        symbols.methods = self._extract_methods(text)
        symbols.fields = {
            type_name: _dedupe_keep_order(ref.name for ref in refs)
            for type_name, refs in self._group_references_by_owner(references, "field").items()
        }
        symbols.references = references
        return symbols

    def _strip_comments(self, content: str) -> str:
        without_block = re.sub(r"(?s)/\*.*?\*/", "", content or "")
        return re.sub(r"(?m)//.*$", "", without_block)

    def _strip_nested_item_blocks(self, text: str, keywords: Sequence[str]) -> str:
        """
        Remove nested item bodies whose functions are not top-level free fns.

        The registry is a lightweight guardrail. Treating methods inside `impl`
        or trait declarations as free functions causes false duplicate reports
        for ordinary Rust APIs such as `Bounds::new` and `Node::new`.
        """
        if not text:
            return ""
        result = text
        for keyword in keywords:
            pattern = re.compile(rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?{keyword}\b[^\n{{;]*\{{")
            search_start = 0
            while True:
                match = pattern.search(result, search_start)
                if not match:
                    break
                open_index = result.find("{", match.start(), match.end())
                if open_index == -1:
                    search_start = match.end()
                    continue
                close_index = self._find_matching_brace(result, open_index)
                if close_index == -1:
                    search_start = match.end()
                    continue
                replacement = "\n" * result[match.start(): close_index + 1].count("\n")
                result = result[:match.start()] + replacement + result[close_index + 1:]
                search_start = match.start() + len(replacement)
        return result

    def _visibility_from_prefix(self, visibility_prefix: str) -> str:
        # Public unless the symbol is definitely private in its own file.
        return "public" if (visibility_prefix or "").strip().startswith("pub") else "private"

    def _split_params(self, params_text: str) -> List[str]:
        text = (params_text or "").strip()
        if not text:
            return []

        params = []
        current = []
        angle = paren = bracket = brace = 0
        for char in text:
            if char == "," and angle == paren == bracket == brace == 0:
                param = _collapse_spaces("".join(current))
                if param:
                    params.append(param)
                current = []
                continue
            current.append(char)
            if char == "<":
                angle += 1
            elif char == ">" and angle:
                angle -= 1
            elif char == "(":
                paren += 1
            elif char == ")" and paren:
                paren -= 1
            elif char == "[":
                bracket += 1
            elif char == "]" and bracket:
                bracket -= 1
            elif char == "{":
                brace += 1
            elif char == "}" and brace:
                brace -= 1

        param = _collapse_spaces("".join(current))
        if param:
            params.append(param)
        return params

    def _return_type_from_match(self, return_part: str) -> str:
        text = (return_part or "").strip()
        if text.startswith("->"):
            text = text[2:].strip()
        return _collapse_spaces(text)

    def _function_signature(self, name: str, params: Sequence[str], return_type: str) -> str:
        suffix = f" -> {return_type}" if return_type else ""
        return f"{name}({', '.join(params)}){suffix}"

    def _group_references_by_owner(
        self,
        references: Sequence[RustSymbolReference],
        kind: str,
    ) -> Dict[str, List[RustSymbolReference]]:
        grouped: Dict[str, List[RustSymbolReference]] = {}
        for ref in references:
            if ref.kind == kind and ref.owner_type:
                grouped.setdefault(ref.owner_type, []).append(ref)
        return grouped

    def _extract_top_level_references(self, rel_path: str, text: str) -> List[RustSymbolReference]:
        references: List[RustSymbolReference] = []

        module_pattern = re.compile(
            r"(?m)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[;{]"
        )
        for match in module_pattern.finditer(text):
            name = match.group("name")
            references.append(
                RustSymbolReference(
                    path=rel_path,
                    kind="module",
                    name=name,
                    visibility=self._visibility_from_prefix(match.group("vis") or ""),
                    signature=name,
                )
            )

        type_pattern = re.compile(
            r"(?m)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<kind>struct|enum|trait|type)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        )
        for match in type_pattern.finditer(text):
            name = match.group("name")
            references.append(
                RustSymbolReference(
                    path=rel_path,
                    kind="type",
                    name=name,
                    visibility=self._visibility_from_prefix(match.group("vis") or ""),
                    signature=f"{match.group('kind')} {name}",
                )
            )

        references.extend(self._extract_struct_field_references(rel_path, text))

        function_pattern = re.compile(
            r"(?ms)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]+\"\s+)?fn\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>{}]*>)?\s*\((?P<params>.*?)\)\s*(?P<ret>->\s*[^;{\n]+)?"
        )
        for match in function_pattern.finditer(text):
            name = match.group("name")
            params = self._split_params(match.group("params") or "")
            return_type = self._return_type_from_match(match.group("ret") or "")
            references.append(
                RustSymbolReference(
                    path=rel_path,
                    kind="function",
                    name=name,
                    visibility=self._visibility_from_prefix(match.group("vis") or ""),
                    params=params,
                    return_type=return_type,
                    signature=self._function_signature(name, params, return_type),
                )
            )

        constant_pattern = re.compile(
            r"(?m)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<kind>const|static)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<ty>[^=;]+)"
        )
        for match in constant_pattern.finditer(text):
            name = match.group("name")
            ty = _collapse_spaces(match.group("ty") or "")
            references.append(
                RustSymbolReference(
                    path=rel_path,
                    kind="constant",
                    name=name,
                    visibility=self._visibility_from_prefix(match.group("vis") or ""),
                    return_type=ty,
                    signature=f"{name}: {ty}" if ty else name,
                )
            )
        return references

    def _extract_struct_field_references(self, rel_path: str, text: str) -> List[RustSymbolReference]:
        references: List[RustSymbolReference] = []
        struct_pattern = re.compile(
            r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>{}]*>)?\s*\{"
        )
        field_pattern = re.compile(
            r"(?m)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<ty>[^,\n]+)\s*,?"
        )
        for struct_match in struct_pattern.finditer(text):
            type_name = struct_match.group("name")
            open_index = struct_match.end() - 1
            close_index = self._find_matching_brace(text, open_index)
            if close_index == -1:
                continue
            body = text[open_index + 1:close_index]
            for field_match in field_pattern.finditer(body):
                field_name = field_match.group("name")
                field_type = _collapse_spaces(field_match.group("ty") or "")
                references.append(
                    RustSymbolReference(
                        path=rel_path,
                        kind="field",
                        name=field_name,
                        owner_type=type_name,
                        visibility=self._visibility_from_prefix(field_match.group("vis") or ""),
                        return_type=field_type,
                        signature=f"{type_name}::{field_name}: {field_type}" if field_type else f"{type_name}::{field_name}",
                    )
                )
        return references

    def _extract_method_references(self, rel_path: str, text: str) -> Dict[str, List[RustSymbolReference]]:
        method_refs: Dict[str, List[RustSymbolReference]] = {}
        impl_pattern = re.compile(r"\bimpl(?:\s*<[^>{}]*>)?\s+([^{]+)\{", re.MULTILINE)
        fn_pattern = re.compile(
            r"(?ms)^\s*(?P<vis>pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]+\"\s+)?fn\s+"
            r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>{}]*>)?\s*\((?P<params>.*?)\)\s*(?P<ret>->\s*[^;{\n]+)?"
        )
        for impl_match in impl_pattern.finditer(text):
            header = impl_match.group(1).strip()
            type_name = self._impl_type_name(header)
            if not type_name:
                continue
            block_end = self._find_matching_brace(text, impl_match.end() - 1)
            if block_end == -1:
                continue
            block = text[impl_match.end():block_end]
            for fn_match in fn_pattern.finditer(block):
                name = fn_match.group("name")
                params = self._split_params(fn_match.group("params") or "")
                return_type = self._return_type_from_match(fn_match.group("ret") or "")
                method_refs.setdefault(type_name, []).append(
                    RustSymbolReference(
                        path=rel_path,
                        kind="method",
                        name=name,
                        owner_type=type_name,
                        visibility=self._visibility_from_prefix(fn_match.group("vis") or ""),
                        params=params,
                        return_type=return_type,
                        signature=self._function_signature(name, params, return_type),
                    )
                )
        return method_refs

    def _extract_methods(self, text: str) -> Dict[str, List[str]]:
        references = self._extract_method_references("", text)
        return {
            type_name: _dedupe_keep_order(ref.name for ref in refs)
            for type_name, refs in references.items()
        }

    def _impl_type_name(self, header: str) -> str:
        if " for " in header:
            header = header.rsplit(" for ", 1)[1].strip()
        header = re.sub(r"<.*$", "", header).strip()
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", header)
        return match.group(1) if match else ""

    def _find_matching_brace(self, text: str, open_index: int) -> int:
        depth = 0
        for index in range(open_index, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
        return -1

    def _find_matching_paren(self, text: str, open_index: int) -> int:
        depth = 0
        for index in range(open_index, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        return -1

    def _known_references_by_owner(self, kind: str) -> Dict[str, Dict[str, RustSymbolReference]]:
        known: Dict[str, Dict[str, RustSymbolReference]] = {}
        for symbols in self.files.values():
            for ref in symbols.references:
                if ref.kind == kind and ref.owner_type:
                    known.setdefault(ref.owner_type, {})[ref.name] = ref
        return known

    def _known_type_paths(self) -> Dict[str, str]:
        type_paths = {}
        for symbols in self.files.values():
            for ref in symbols.references:
                if ref.kind == "type":
                    type_paths[ref.name] = ref.path
        return type_paths

    def _normalize_rust_type_name(self, type_text: str) -> str:
        text = _collapse_spaces(type_text)
        text = re.sub(r"^&\s*(?:'[_A-Za-z0-9]+\s+)?(?:mut\s+)?", "", text).strip()
        option_match = re.match(r"Option\s*<\s*(.+)\s*>$", text)
        if option_match:
            return self._normalize_rust_type_name(option_match.group(1))
        box_match = re.match(r"Box\s*<\s*(.+)\s*>$", text)
        if box_match:
            return self._normalize_rust_type_name(box_match.group(1))
        vec_match = re.match(r"Vec\s*<\s*(.+)\s*>$", text)
        if vec_match:
            return self._normalize_rust_type_name(vec_match.group(1))
        match = re.search(r"([A-Z][A-Za-z0-9_]*)\b", text)
        return match.group(1) if match else ""

    def _infer_variable_types(
        self,
        content: str,
        known_fields: Dict[str, Dict[str, RustSymbolReference]],
    ) -> Dict[str, str]:
        variable_types: Dict[str, str] = {}
        function_pattern = re.compile(
            r"(?ms)\bfn\s+[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^>{}]*>)?\s*\((?P<params>.*?)\)"
        )
        for fn_match in function_pattern.finditer(content or ""):
            for param in self._split_params(fn_match.group("params") or ""):
                param_match = re.match(r"(?:mut\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", param)
                if not param_match:
                    continue
                type_name = self._normalize_rust_type_name(param_match.group(2))
                if type_name:
                    variable_types[param_match.group(1)] = type_name

        some_field_pattern = re.compile(
            r"\blet\s+Some\s*\(\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*=\s*"
            r"(?P<owner>[A-Za-z_][A-Za-z0-9_]*)\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\.(?:as_ref|as_mut)\s*\("
        )
        for match in some_field_pattern.finditer(content or ""):
            owner_type = variable_types.get(match.group("owner"))
            if not owner_type:
                continue
            field_ref = known_fields.get(owner_type, {}).get(match.group("field"))
            if not field_ref:
                continue
            type_name = self._normalize_rust_type_name(field_ref.return_type)
            if type_name:
                variable_types[match.group("var")] = type_name
        return variable_types

    def _iter_associated_calls(self, content: str) -> Iterable[Tuple[str, str, List[str]]]:
        pattern = re.compile(r"\b(?P<owner>[A-Z][A-Za-z0-9_]*)::(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
        for match in pattern.finditer(content or ""):
            owner = match.group("owner")
            if owner == "Self":
                continue
            open_index = match.end() - 1
            close_index = self._find_matching_paren(content, open_index)
            if close_index == -1:
                continue
            args = self._split_params(content[open_index + 1:close_index])
            yield owner, match.group("name"), args

    def _reference_member_findings(self, rel_path: str, content: str) -> List[str]:
        findings: List[str] = []
        normalized_path = rel_path.replace("\\", "/")
        known_fields = self._known_references_by_owner("field")
        known_methods = self._known_references_by_owner("method")
        type_paths = self._known_type_paths()

        for owner_type, method_name, args in self._iter_associated_calls(content or ""):
            methods = known_methods.get(owner_type)
            if not methods:
                continue
            method_ref = methods.get(method_name)
            if not method_ref:
                findings.append(f"引用表中不存在方法 `{owner_type}::{method_name}`，请不要发明跨文件方法")
                continue
            if not method_ref.is_public and method_ref.path != normalized_path:
                findings.append(f"跨文件引用了 private 方法 `{owner_type}::{method_name}`，定义于 {method_ref.path}")
                continue
            expected_params = [param for param in method_ref.params if param not in {"self", "&self", "&mut self"}]
            if len(args) != len(expected_params):
                findings.append(
                    f"方法调用参数不匹配 `{owner_type}::{method_name}`："
                    f"引用表签名为 `{method_ref.display_signature()}`，实际传入 {len(args)} 个参数"
                )

        variable_types = self._infer_variable_types(content or "", known_fields)
        field_access_pattern = re.compile(r"\b(?P<var>[a-z_][A-Za-z0-9_]*)\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\b")
        for match in field_access_pattern.finditer(content or ""):
            end = match.end()
            if end < len(content or "") and re.match(r"\s*\(", (content or "")[end:]):
                continue
            var_name = match.group("var")
            owner_type = variable_types.get(var_name)
            if not owner_type or owner_type not in known_fields:
                continue
            field_name = match.group("field")
            field_ref = known_fields[owner_type].get(field_name)
            owner_path = type_paths.get(owner_type, "")
            if not field_ref:
                findings.append(f"引用表中 `{owner_type}` 不存在字段 `{field_name}`，请不要发明跨文件字段")
                continue
            if not field_ref.is_public and owner_path and owner_path != normalized_path:
                findings.append(f"跨文件引用了 private 字段 `{owner_type}::{field_name}`，定义于 {owner_path}")

        return _dedupe_keep_order(findings)

    def duplicate_findings(self, rel_path: str, content: str) -> List[str]:
        candidate = self.extract_symbols(rel_path, content)
        findings = []
        for other_path, other in self.files.items():
            if other_path == candidate.path:
                continue
            for symbol in candidate.types:
                if symbol in other.types:
                    findings.append(f"重复定义类型 `{symbol}`，已存在于 {other_path}")
            for symbol in candidate.functions:
                if symbol in other.functions:
                    findings.append(f"重复定义自由函数 `{symbol}`，已存在于 {other_path}")
            for symbol in candidate.constants:
                if symbol in other.constants:
                    findings.append(f"重复定义常量或静态项 `{symbol}`，已存在于 {other_path}")
        return _dedupe_keep_order(findings)

    def reference_findings(self, rel_path: str, content: str, planned_files: Sequence[str]) -> List[str]:
        planned_modules = {self.module_path_for_file(path).split("::", 1)[0] for path in planned_files if path.endswith(".rs")}
        generated_modules = {self.module_path_for_file(path).split("::", 1)[0] for path in self.files.keys() if path.startswith("src/")}
        findings = []

        referenced = set(re.findall(r"\bcrate::([A-Za-z_][A-Za-z0-9_]*)\b", content or ""))
        referenced.update(re.findall(r"(?m)^\s*use\s+crate::([A-Za-z_][A-Za-z0-9_]*)\b", content or ""))
        for module in sorted(referenced):
            if module in generated_modules:
                continue
            if module in planned_modules:
                findings.append(f"引用了尚未生成的计划模块 `crate::{module}`")
            else:
                findings.append(f"引用了未规划模块 `crate::{module}`")
        findings.extend(self._reference_member_findings(rel_path, content))
        return findings

    def module_path_for_file(self, rel_path: str) -> str:
        normalized = rel_path.replace("\\", "/")
        if not normalized.startswith("src/") or not normalized.endswith(".rs"):
            return ""
        inner = normalized[4:-3]
        if inner == "lib" or inner == "main":
            return ""
        if inner.endswith("/mod"):
            inner = inner[:-4]
        return inner.replace("/", "::")

    def summary(self, max_chars: int = 12000) -> str:
        lines = ["visibility policy: public unless the symbol is definitely private in its defining file"]
        for path in sorted(self.files.keys()):
            symbols = self.files[path]
            module_path = self.module_path_for_file(path)
            lines.append(f"- {path}{' => crate::' + module_path if module_path else ''}")
            if symbols.modules:
                lines.append(f"  modules: {', '.join(symbols.modules)}")
            if symbols.types:
                lines.append(f"  types: {', '.join(symbols.types)}")
            if symbols.functions:
                lines.append(f"  functions: {', '.join(symbols.functions)}")
            if symbols.constants:
                lines.append(f"  constants: {', '.join(symbols.constants)}")
            for type_name, fields in sorted(symbols.fields.items()):
                lines.append(f"  fields {type_name}: {', '.join(fields)}")
            for type_name, methods in sorted(symbols.methods.items()):
                lines.append(f"  impl {type_name}: {', '.join(methods)}")
            if symbols.references:
                lines.append("  references:")
                for ref in symbols.references:
                    lines.append(f"    - {ref.detail_line()}")
        return _clip_text("\n".join(lines) if lines else "(当前还没有已生成 Rust 符号)", max_chars)


class ContextualRustAgent(RustAgent):
    """
    Demand-driven Rust generator.

    Differences from RustAgent/GrowthRustAgent:
    - Static prompt contains a document index, not all documents.
    - File prompts receive only relevant spec/source snippets and a symbol table.
    - The model can request extra spec/source/rust context through <CGR_READ>.
    - Already generated files are represented by a registry instead of full text.
    """

    def __init__(self, config: Config = None):
        super().__init__(config=config)
        self.spec_index = SpecDocumentIndex()
        self.spec_context_agent: Optional[ContextualSpecAgent] = None
        self.registry = RustProjectRegistry()
        self.contextual_plan: List[PlannedFile] = []
        self._plan_by_path: Dict[str, PlannedFile] = {}

    def _set_request_label(self, label: str):
        if hasattr(self.llm, "set_request_label"):
            self.llm.set_request_label(label)

    def _spec_agent(self) -> ContextualSpecAgent:
        if self.spec_context_agent is None:
            self.spec_context_agent = ContextualSpecAgent(config=self.config, enable_c_pipeline=False)
        return self.spec_context_agent

    def _has_spec_context(self) -> bool:
        return self.spec_context_agent is not None

    def _spec_overview(self, max_chars: int = 12000) -> str:
        if not self.spec_context_agent:
            return self.spec_index.overview(max_chars=max_chars)
        return self.spec_context_agent.rust_context_overview(max_chars=max_chars)

    def _spec_context_for_query(self, query: str, max_chars: int = 18000) -> str:
        if not self.spec_context_agent:
            return self.spec_index.select_for_query(query, max_slices=5, max_chars=max_chars)
        return self.spec_context_agent.rust_context_for_query(query, max_chars=max_chars)

    def _spec_build_file_plan(self, allowed_files: Sequence[str]) -> List[object]:
        if not self.spec_context_agent:
            return []
        return self.spec_context_agent.build_rust_file_plan(allowed_files=allowed_files)

    def _spec_infer_candidate_files(self) -> List[str]:
        if not self.spec_context_agent:
            return self.spec_index.infer_candidate_rust_files()
        return self.spec_context_agent.infer_candidate_rust_files()

    def _spec_context_for_file(self, planned: PlannedFile) -> str:
        if not self.spec_context_agent:
            return self.spec_index.select_for_file(
                planned.path,
                owns=planned.owns,
                spec_queries=planned.spec_queries,
            )
        return self.spec_context_agent.rust_context_for_planned_file(planned)

    def _build_static_project_context(self) -> str:
        parts = [
            f"项目名称：{self.project_name}",
            "目标：把 C 项目按 spec 重写为结构化、惯用、可编译的 Rust 项目。",
            "上下文策略：不要默认展开全部文档；需要更多信息时使用 <CGR_READ> 请求。",
            "生成边界：只实现输入 C 项目和 spec 中已有的能力，不主动扩写线程安全、序列化、网络、CLI、恢复机制等无证据功能。",
            "Rust 化迁移契约（最高优先级）：\n" + self._rust_rewrite_contract(),
        ]

        contract_context = self._build_translation_contract_context(max_chars=7000)
        if contract_context:
            parts.append("迁移契约（最高优先级）：\n" + contract_context)
            scope = self._contract_scope_instructions()
            if scope:
                parts.append(scope)

        if self.source_interface_summary:
            parts.append("原始 C 对外接口事实：\n" + _clip_text(self.source_interface_summary, 3000))
        if self.tool_interface_constraints:
            parts.append("工具/CLI 接口保持约束：\n" + _clip_text(self.tool_interface_constraints, 2500))
        if self.spec_index.slices:
            overview = self._spec_overview(max_chars=5000)
            parts.append("可用 spec 文档索引（只有索引，不是全文）：\n" + overview)
        return "\n\n".join(part for part in parts if part).strip()

    def _rust_rewrite_contract(self, planned: Optional[PlannedFile] = None) -> str:
        return self._spec_agent().rust_rewrite_contract(planned)

    def _read_llm(self, messages: List[Dict[str, str]], label: str) -> str:
        self._set_request_label(label)
        response = self.llm.generate(messages)
        if isinstance(response, list):
            return str(response[0]) if response else ""
        return str(response or "")

    def _chat_with_context_requests(
        self,
        system_prompt: str,
        user_prompt: str,
        label: str,
        max_read_rounds: int = 3,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        last_reply = ""
        for round_index in range(1, max_read_rounds + 2):
            last_reply = self._read_llm(messages, f"{label} [round {round_index}]")
            read_requests = self._parse_read_requests(last_reply)
            if not read_requests:
                return last_reply
            materials = self._materialize_read_requests(read_requests)
            messages.append({"role": "assistant", "content": last_reply})
            messages.append(
                {
                    "role": "user",
                    "content": self._spec_agent().rust_read_materials_followup(materials),
                }
            )
        return last_reply

    def _parse_read_requests(self, text: str) -> List[Dict[str, str]]:
        match = re.search(r"(?is)<CGR_READ>\s*(.*?)\s*</CGR_READ>", text or "")
        if not match:
            return []
        payload = match.group(1).strip()
        requests: List[Dict[str, str]] = []

        parsed = None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(payload)
                break
            except Exception:
                parsed = None

        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or item.get("type") or "").strip().lower()
                query = str(item.get("query") or item.get("path") or item.get("symbol") or "").strip()
                if kind:
                    requests.append({"kind": kind, "query": query})
            return requests

        for raw_line in payload.splitlines():
            line = raw_line.strip().strip("-* ")
            if not line or ":" not in line:
                continue
            kind, query = line.split(":", 1)
            kind = kind.strip().lower()
            query = query.strip()
            if kind:
                requests.append({"kind": kind, "query": query})
        return requests

    def _materialize_read_requests(self, read_requests: Sequence[Dict[str, str]], max_chars: int = 18000) -> str:
        blocks = []
        total = 0
        for request in read_requests:
            kind = (request.get("kind") or "").lower()
            query = request.get("query") or ""
            if kind in {"spec", "doc", "docs"}:
                content = self._spec_context_for_query(query, max_chars=8000)
            elif kind in {"source", "c", "c_source"}:
                content = self._read_source_material(query, max_chars=8000)
            elif kind in {"rust", "generated", "file"}:
                content = self._read_generated_rust_material(query, max_chars=8000)
            elif kind in {"registry", "symbols", "symbol"}:
                content = self.registry.summary(max_chars=8000)
            elif kind in {"plan", "project_plan"}:
                content = self._format_plan_summary()
            else:
                content = f"不支持的读取类型：{kind}"

            block = f"\n\n=== READ {kind or 'unknown'}: {query or '(empty)'} ===\n{content}\n"
            if total + len(block) > max_chars:
                remaining = max_chars - total
                if remaining > 0:
                    blocks.append(block[:remaining].rstrip() + "\n...[读取材料预算已满]...")
                break
            blocks.append(block)
            total += len(block)
        return "".join(blocks).strip()

    def _safe_join_existing_file(self, root: str, rel_path: str) -> Optional[str]:
        if not root or not rel_path:
            return None
        root_abs = os.path.abspath(root)
        candidate = os.path.abspath(os.path.join(root_abs, rel_path.replace("\\", os.sep)))
        try:
            if os.path.commonpath([root_abs, candidate]) != root_abs:
                return None
        except ValueError:
            return None
        if os.path.isfile(candidate):
            return candidate
        return None

    def _read_source_material(self, query: str, max_chars: int = 8000) -> str:
        normalized_query = (query or "").replace("\\", "/").strip()
        lowered_query = normalized_query.lower()

        exact_func = [r for r in self.source_records if str(r.get("name", "")).lower() == lowered_query]
        if exact_func:
            return self._format_source_records(exact_func, max_chars)

        file_matches = [
            r for r in self.source_records
            if lowered_query and lowered_query in str(r.get("file", "")).replace("\\", "/").lower()
        ]
        if file_matches:
            return self._format_source_records(file_matches, max_chars)

        direct = self._safe_join_existing_file(self.source_project_path, normalized_query)
        if direct:
            try:
                return _clip_text(Path(direct).read_text(encoding="utf-8", errors="ignore"), max_chars)
            except Exception as exc:
                return f"读取源文件失败：{exc}"

        query_tokens = _tokenize_text(normalized_query)
        scored = []
        for record in self.source_records:
            haystack = f"{record.get('file', '')} {record.get('name', '')} {record.get('func_defid', '')}"
            score = len(query_tokens & _tokenize_text(haystack))
            if normalized_query and normalized_query in str(record.get("file", "")).replace("\\", "/"):
                score += 8
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: -item[0])
        if scored:
            return self._format_source_records([r for _, r in scored[:6]], max_chars)
        return "没有找到匹配的 C 源码材料。可尝试按函数名或文件名请求，例如 {\"kind\":\"source\",\"query\":\"function_name\"}"

    def _format_source_records(self, records: Sequence[Dict], max_chars: int = 8000) -> str:
        parts = []
        total = 0
        for record in records:
            calls = record.get("calls", [])[:4]
            block_lines = [
                f"\n--- {record.get('name', 'unknown')} | {record.get('file', '')} | {record.get('span', '')} "
                f"({record.get('num_lines', '?')} lines) ---"
            ]
            if calls:
                caller_lines = ", ".join(
                    f"{c.get('caller', '?').rsplit(':', 1)[-1]}(): {self._truncate_text(str(c.get('source', '')).strip(), 60)}"
                    for c in calls if c.get("caller")
                )
                if caller_lines:
                    block_lines.append(f"被调用于：{caller_lines}")
            block_lines.append(record.get("source", ""))
            block = "\n".join(block_lines) + "\n"
            if total + len(block) > max_chars and parts:
                remaining_count = len(records) - records.index(record)
                parts.append(f"\n...另有 {remaining_count} 个函数因预算限制未展示，可缩小查询范围重试。")
                break
            parts.append(block)
            total += len(block)
        return "".join(parts).strip()

    def _read_generated_rust_material(self, query: str, max_chars: int = 14000) -> str:
        normalized = (query or "").replace("\\", "/").strip()
        path = self._safe_join_existing_file(self.project_path, normalized)
        if not path:
            return f"生成项目中不存在该 Rust 文件：{normalized}"
        try:
            return _clip_text(Path(path).read_text(encoding="utf-8", errors="ignore"), max_chars)
        except Exception as exc:
            return f"读取 Rust 文件失败：{exc}"

    def _extract_json_payload(self, text: str):
        source = text or ""
        tag_match = re.search(r"(?is)<CGR_PLAN>\s*(.*?)\s*</CGR_PLAN>", source)
        if tag_match:
            source = tag_match.group(1)
        fence_match = re.search(r"(?is)```(?:json)?\s*(.*?)\s*```", source)
        if fence_match:
            source = fence_match.group(1)
        source = source.strip()

        candidates = [source]
        object_match = re.search(r"(?s)\{.*\}", source)
        if object_match:
            candidates.append(object_match.group(0))
        array_match = re.search(r"(?s)\[.*\]", source)
        if array_match:
            candidates.append(array_match.group(0))

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                try:
                    return ast.literal_eval(candidate)
                except Exception:
                    continue
        return None

    def _request_contextual_plan(self) -> List[PlannedFile]:
        if self._has_spec_context():
            fallback_files = self._fallback_file_list()
            file_specs = self._spec_build_file_plan(allowed_files=fallback_files)
            plan = [self._planned_file_from_spec(item) for item in file_specs]
            plan = self._sort_contextual_plan(plan)
            self._plan_by_path = {item.path: item for item in plan}
            return plan

        static_context = self._build_static_project_context()
        fallback_files = self._fallback_file_list()
        prompt = self._spec_agent().rust_project_planning_prompt(fallback_files, static_context)
        response = self._chat_with_context_requests(
            system_prompt=self._spec_agent().rust_project_planning_system_prompt(),
            user_prompt=prompt,
            label="ContextualRustAgent 项目规划",
            max_read_rounds=2,
        )
        payload = self._extract_json_payload(response)
        return self._normalize_plan_payload(payload, fallback_files=fallback_files)

    def _planned_file_from_spec(self, spec) -> PlannedFile:
        return PlannedFile(
            path=spec.path,
            role=spec.role,
            owns=list(spec.owns),
            depends_on=list(spec.depends_on),
            spec_queries=list(spec.spec_queries),
            source_files=list(spec.source_files),
            source_functions=list(spec.source_functions),
        )

    def _fallback_file_list(self) -> List[str]:
        if self.allowed_rust_files:
            return self._sanitize_generation_file_list(self.allowed_rust_files)

        inferred = self._spec_infer_candidate_files()
        if len(inferred) <= 3 and self.source_records:
            inferred = ["Cargo.toml"]
            for record in self.source_records:
                source_file = str(record.get("file", "")).replace("\\", "/")
                if not source_file.endswith(".c"):
                    continue
                stem = os.path.splitext(os.path.basename(source_file))[0]
                if stem and stem != "main":
                    inferred.append(f"src/{stem}.rs")
                elif stem == "main":
                    inferred.append("src/main.rs")
            inferred.extend(["src/lib.rs", "README.md"])
        return self._sanitize_generation_file_list(_dedupe_keep_order(inferred or ["Cargo.toml", "src/lib.rs", "README.md"]))

    def _normalize_plan_payload(self, payload, fallback_files: Sequence[str]) -> List[PlannedFile]:
        raw_files = []
        raw_order = []
        if isinstance(payload, dict):
            raw_files = payload.get("files") or []
            raw_order = payload.get("order") or payload.get("generation_order") or []
        elif isinstance(payload, list):
            raw_files = payload

        by_path: Dict[str, PlannedFile] = {}
        for item in raw_files:
            if isinstance(item, str):
                planned = PlannedFile(path=item)
            elif isinstance(item, dict):
                planned = PlannedFile(
                    path=str(item.get("path") or item.get("file") or "").strip(),
                    role=str(item.get("role") or item.get("description") or "").strip(),
                    owns=[str(value).strip() for value in item.get("owns", []) if str(value).strip()]
                    if isinstance(item.get("owns", []), list)
                    else [],
                    depends_on=[str(value).strip() for value in item.get("depends_on", []) if str(value).strip()]
                    if isinstance(item.get("depends_on", []), list)
                    else [],
                    spec_queries=[str(value).strip() for value in item.get("spec_queries", []) if str(value).strip()]
                    if isinstance(item.get("spec_queries", []), list)
                    else [],
                    source_files=[str(value).strip() for value in item.get("source_files", []) if str(value).strip()]
                    if isinstance(item.get("source_files", []), list)
                    else [],
                    source_functions=[str(value).strip() for value in item.get("source_functions", []) if str(value).strip()]
                    if isinstance(item.get("source_functions", []), list)
                    else [],
                )
            else:
                continue

            cleaned = self._clean_relative_project_path(planned.path, "")
            if not cleaned:
                continue
            planned.path = cleaned
            by_path[planned.path] = planned

        for path in fallback_files:
            cleaned = self._clean_relative_project_path(path, "")
            if cleaned and cleaned not in by_path:
                by_path[cleaned] = self._fallback_planned_file(cleaned)

        requested_order = []
        for item in raw_order:
            cleaned = self._clean_relative_project_path(str(item), "")
            if cleaned:
                requested_order.append(cleaned)
        requested_order.extend(fallback_files)
        requested_order.extend(by_path.keys())

        sanitized_paths = self._sanitize_generation_file_list(_dedupe_keep_order(requested_order))
        if not sanitized_paths:
            sanitized_paths = self._sanitize_generation_file_list(fallback_files)

        plan = [by_path.get(path) or self._fallback_planned_file(path) for path in sanitized_paths]
        plan = self._sort_contextual_plan(plan)
        self._plan_by_path = {item.path: item for item in plan}
        return plan

    def _fallback_planned_file(self, path: str) -> PlannedFile:
        normalized = path.replace("\\", "/")
        stem = os.path.splitext(os.path.basename(normalized))[0]
        role = "Rust 项目文件"
        owns: List[str] = []
        spec_queries = [stem]
        if normalized == "Cargo.toml":
            role = "Cargo package manifest，本地生成最小配置"
        elif normalized == "README.md":
            role = "项目说明文档"
        elif normalized == "src/lib.rs":
            role = "crate 入口，本地根据已生成模块重建"
        elif normalized.endswith(".rs"):
            role = f"实现与 `{stem}` 相关的核心 Rust 类型、函数和算法"
            pascal = _pascal_case(stem)
            if pascal:
                owns = [pascal]
        return PlannedFile(path=normalized, role=role, owns=owns, spec_queries=spec_queries)

    def _sort_contextual_plan(self, plan: Sequence[PlannedFile]) -> List[PlannedFile]:
        by_path = {item.path: item for item in plan}
        ordered_paths = self._sort_files_for_generation([item.path for item in plan])

        def priority(path: str) -> Tuple[int, str]:
            normalized = path.replace("\\", "/").lower()
            base = os.path.basename(normalized)
            if base == "cargo.toml":
                return (0, normalized)
            if normalized.startswith("src/") and base != "lib.rs":
                if any(token in normalized for token in ["type", "data", "error", "const", "bound", "point", "node"]):
                    return (1, normalized)
                return (2, normalized)
            if base == "lib.rs":
                return (3, normalized)
            if base == "readme.md":
                return (4, normalized)
            return (5, normalized)

        ordered_paths = sorted(ordered_paths, key=priority)
        emitted: List[str] = []
        pending = list(ordered_paths)
        while pending:
            progressed = False
            for path in list(pending):
                deps = [
                    dep.replace("\\", "/")
                    for dep in by_path.get(path, PlannedFile(path)).depends_on
                    if dep.replace("\\", "/") in by_path
                ]
                if all(dep in emitted for dep in deps):
                    emitted.append(path)
                    pending.remove(path)
                    progressed = True
            if not progressed:
                emitted.extend(pending)
                break

        return [by_path[path] for path in emitted if path in by_path]

    def _format_plan_summary(self) -> str:
        lines = []
        for index, item in enumerate(self.contextual_plan, start=1):
            lines.append(f"{index}. {item.path}")
            if item.role:
                lines.append(f"   role: {item.role}")
            if item.owns:
                lines.append(f"   owns: {', '.join(item.owns)}")
            if item.depends_on:
                lines.append(f"   depends_on: {', '.join(item.depends_on)}")
            if item.source_files:
                lines.append(f"   source_files: {', '.join(item.source_files)}")
            if item.source_functions:
                lines.append(f"   source_functions: {', '.join(item.source_functions[:16])}")
            if item.spec_queries:
                lines.append(f"   spec_queries: {', '.join(item.spec_queries)}")
        return "\n".join(lines)

    def _build_targeted_plan_summary(self, planned: PlannedFile) -> str:
        """只保留当前文件、直接依赖和直接被依赖的计划条目，减少无关上下文。
        末尾追加轻量全局文件索引，让 LLM 知道全局有哪些模块及其拥有的符号。"""
        current_path = planned.path.replace("\\", "/")
        dep_paths = {dep.replace("\\", "/") for dep in (planned.depends_on or [])}
        reverse_deps = set()
        for item in self.contextual_plan:
            item_deps = {d.replace("\\", "/") for d in (item.depends_on or [])}
            if current_path in item_deps:
                reverse_deps.add(item.path.replace("\\", "/"))
        relevant_paths = {current_path} | dep_paths | reverse_deps

        lines = []
        for index, item in enumerate(self.contextual_plan, start=1):
            item_path = item.path.replace("\\", "/")
            if item_path not in relevant_paths:
                continue
            lines.append(f"{index}. {item.path}")
            if item.role:
                lines.append(f"   role: {item.role}")
            if item.owns:
                lines.append(f"   owns: {', '.join(item.owns)}")
            if item.depends_on:
                lines.append(f"   depends_on: {', '.join(item.depends_on)}")
            if item.source_files:
                lines.append(f"   source_files: {', '.join(item.source_files)}")
            if item.source_functions:
                lines.append(f"   source_functions: {', '.join(item.source_functions[:16])}")
        if not lines:
            return self._format_plan_summary()

        global_index = ["\n--- 全局文件索引（仅路径+符号归属） ---"]
        for item in self.contextual_plan:
            item_path = item.path.replace("\\", "/")
            if item_path in relevant_paths:
                continue
            owns_hint = f" owns: {', '.join(item.owns)}" if item.owns else ""
            global_index.append(f"  - {item.path}{owns_hint}")
        if len(global_index) > 1:
            lines.extend(global_index)
        return "\n".join(lines)

    def _build_targeted_registry_summary(self, planned: PlannedFile) -> str:
        """只保留当前文件依赖的文件的符号，减少无关上下文。
        末尾追加轻量全局类型索引，避免 depends_on 不完整时重复定义符号。"""
        dep_paths = {dep.replace("\\", "/") for dep in (planned.depends_on or [])}
        if not dep_paths:
            return self.registry.summary()

        lines = ["visibility policy: public unless the symbol is definitely private in its defining file"]
        for path in sorted(self.registry.files.keys()):
            normalized = path.replace("\\", "/")
            if normalized not in dep_paths:
                continue
            symbols = self.registry.files[path]
            module_path = self.registry.module_path_for_file(path)
            lines.append(f"- {path}{' => crate::' + module_path if module_path else ''}")
            if symbols.modules:
                lines.append(f"  modules: {', '.join(symbols.modules)}")
            if symbols.types:
                lines.append(f"  types: {', '.join(symbols.types)}")
            if symbols.functions:
                lines.append(f"  functions: {', '.join(symbols.functions)}")
            if symbols.constants:
                lines.append(f"  constants: {', '.join(symbols.constants)}")
            for type_name, fields in sorted(symbols.fields.items()):
                lines.append(f"  fields {type_name}: {', '.join(fields)}")
            for type_name, methods in sorted(symbols.methods.items()):
                lines.append(f"  impl {type_name}: {', '.join(methods)}")
            if symbols.references:
                lines.append("  references:")
                for ref in symbols.references:
                    lines.append(f"    - {ref.detail_line()}")
        if len(lines) <= 1:
            return self.registry.summary()

        global_type_index = ["\n--- 全局已生成类型索引（禁止重复定义） ---"]
        for path in sorted(self.registry.files.keys()):
            normalized = path.replace("\\", "/")
            if normalized in dep_paths:
                continue
            symbols = self.registry.files[path]
            all_types = list(symbols.types) + list(symbols.constants)
            if all_types:
                global_type_index.append(f"  - {path}: {', '.join(all_types)}")
        if len(global_type_index) > 1:
            lines.extend(global_type_index)
        return "\n".join(lines)

    def _build_targeted_source_context(self, planned: PlannedFile) -> str:
        """为目标 Rust 文件生成 C 源码上下文：
        - 轻量索引：所有相关函数的签名 + 文件 + 行数 + 调用关系
        - 内联源码：只对最关键的少量函数内联完整源码
        - 其余源码引导 LLM 通过 <CGR_READ> 按需请求
        """
        if not self.source_records:
            return ""

        target_files = {sf.replace("\\", "/").lower() for sf in (planned.source_files or [])}
        target_funcs = {fn.lower() for fn in (planned.source_functions or [])}
        file_stem = os.path.splitext(os.path.basename(planned.path.replace("\\", "/")))[0].lower()

        scored = []
        for record in self.source_records:
            score = 0
            record_file = str(record.get("file", "")).replace("\\", "/").lower()
            record_name = str(record.get("name", "")).lower()

            if target_funcs and record_name in target_funcs:
                score += 30
            for tf in target_files:
                if tf and (record_file.endswith(tf) or tf in record_file):
                    score += 20
                    break
            if score == 0 and (target_files or target_funcs):
                continue
            if score == 0:
                score = self._score_source_record_for_target_file(planned.path, record)
            if score > 0:
                scored.append((score, record))

        if not scored:
            return ""

        scored.sort(key=lambda item: (-item[0], -int(item[1].get("num_lines", 0)), item[1].get("name", "")))

        MAX_INLINE = 2
        inline_records = []
        index_records = []
        for rank, (score, record) in enumerate(scored):
            if rank < MAX_INLINE and score >= 20:
                inline_records.append(record)
            else:
                index_records.append(record)

        parts = [f"与 Rust 文件 `{planned.path}` 相关的 C 源码："]

        if inline_records:
            parts.append("\n## 关键 C 源码（已内联）")
            for record in inline_records:
                calls = record.get("calls", [])[:3]
                block_lines = [f"### {record['name']} [{record['file']} {record['span']}] ({record.get('num_lines', '?')} lines)"]
                if calls:
                    call_lines = ", ".join(
                        f"{call.get('caller', '?').rsplit(':', 1)[-1]}()"
                        for call in calls
                    )
                    block_lines.append(f"被调用于：{call_lines}")
                snippet = self._truncate_text(record.get("source", "").strip(), 1200)
                block_lines.append(f"```c\n{snippet}\n```")
                block = "\n".join(block_lines)
                parts.append(block)

        if index_records:
            parts.append("\n## C 源码索引（需要详情请用 <CGR_READ> 请求）")
            for record in index_records[:20]:
                sig = self._extract_c_signature(record)
                callers = record.get("calls", [])[:3]
                caller_hint = ""
                if callers:
                    caller_names = [c.get("caller", "").rsplit(":", 1)[-1] for c in callers if c.get("caller")]
                    if caller_names:
                        caller_hint = f" | 被调用于: {', '.join(caller_names)}"
                line = f"- `{record['name']}` [{record['file']}] ({record.get('num_lines', '?')} lines) | {sig}{caller_hint}"
                parts.append(line)

        return "\n".join(parts).strip()

    def _extract_c_signature(self, record: Dict) -> str:
        """从 source 中提取函数签名（到第一个 { 或行末为止）。"""
        source = record.get("source", "") or ""
        brace_pos = source.find("{")
        if brace_pos > 0:
            sig = source[:brace_pos].strip()
        else:
            sig = source.split("\n")[0].strip()
        sig = " ".join(sig.split())
        return self._truncate_text(sig, 120)

    def _build_file_prompt(self, planned: PlannedFile, planned_files: Sequence[str]) -> str:
        spec_context = self._spec_context_for_file(planned)
        source_context = ""
        if planned.path.endswith(".rs"):
            source_context = self._build_targeted_source_context(planned)
        return self._spec_agent().rust_file_generation_prompt(
            planned=planned,
            planned_files=planned_files,
            plan_summary=self._build_targeted_plan_summary(planned),
            registry_summary=self._build_targeted_registry_summary(planned),
            spec_context=spec_context,
            source_context=source_context,
        )

    def _generate_contextual_file(self, planned: PlannedFile, planned_files: Sequence[str]) -> Tuple[bool, str]:
        print(f"ContextualRustAgent 生成文件：{planned.path}")
        self._mark_generation_status(planned.path, "in_progress", "contextual_generation")

        normalized = planned.path.replace("\\", "/")
        if normalized == "Cargo.toml":
            content = self._sanitize_cargo_toml_for_config(self._build_fallback_cargo_toml())
            self._write_file(os.path.join(self.project_path, normalized), content)
            self._mark_generation_status(normalized, "completed", "local_cargo_toml")
            return True, content

        if normalized == "src/lib.rs":
            content = self._build_registry_lib_rs(planned_files)
            self._write_file(os.path.join(self.project_path, normalized), content)
            self.registry.update_file(normalized, content)
            self._update_api_contract_for_file(normalized, content)
            self._mark_generation_status(normalized, "completed", "local_lib_rs")
            return True, content

        response = self._chat_with_context_requests(
            system_prompt=self._spec_agent().rust_file_generation_system_prompt(),
            user_prompt=self._build_file_prompt(planned, planned_files),
            label=f"ContextualRustAgent 代码生成 {planned.path}",
            max_read_rounds=3,
        )
        content, _ = self._extract_done_marker(response)
        code_lang = "" if normalized.lower() == "readme.md" else "rust"
        content = self._extract_generated_content(content, code_lang=code_lang)
        content = self._sanitize_file_content_before_write(normalized, content)

        if not content.strip() and normalized.lower() == "readme.md":
            content = self._build_fallback_readme()
        if not content.strip():
            self._mark_generation_status(normalized, "failed", "empty_contextual_generation")
            return False, ""

        findings = self._lint_contextual_file(normalized, content, planned_files, planned)
        fatal_findings = self._fatal_contextual_findings(findings)
        if fatal_findings:
            repaired = self._repair_contextual_file(planned, planned_files, content, fatal_findings)
            if repaired.strip():
                content = self._sanitize_file_content_before_write(normalized, repaired)
                findings = self._lint_contextual_file(normalized, content, planned_files, planned)
                fatal_findings = self._fatal_contextual_findings(findings)
        force_write = False
        force_write_reason = ""
        if fatal_findings:
            override_content, force_write, force_write_reason = self._request_force_write_decision(
                planned=planned,
                planned_files=planned_files,
                content=content,
                findings=fatal_findings,
            )
            if override_content.strip():
                content = self._sanitize_file_content_before_write(normalized, override_content)
                findings = self._lint_contextual_file(normalized, content, planned_files, planned)
                fatal_findings = self._fatal_contextual_findings(findings)
            if fatal_findings and not force_write:
                print(f"ContextualRustAgent 拒绝写入越界文件：{normalized}")
                for finding in fatal_findings:
                    print(f"  - {finding}")
                self._mark_generation_status(normalized, "failed", "contextual_boundary_violation")
                return False, ""
            if fatal_findings and force_write:
                print(f"ContextualRustAgent 强制写入仍有违规的文件：{normalized}")
                if force_write_reason:
                    print(f"  reason: {force_write_reason}")
                for finding in fatal_findings:
                    print(f"  - {finding}")

        if normalized.lower().endswith(".rs"):
            deps = self._detect_dependencies(content)
            if deps:
                self._update_cargo_toml(deps)

        self._write_file(os.path.join(self.project_path, normalized), content)
        if normalized.lower().endswith(".rs"):
            self.registry.update_file(normalized, content)
            self._update_api_contract_for_file(normalized, content)
        note = "contextual_force_write"
        if force_write_reason:
            note += f": {force_write_reason}"
        self._mark_generation_status(normalized, "completed", note if force_write else "contextual_generation")
        return True, content

    def _lint_contextual_file(
        self,
        rel_path: str,
        content: str,
        planned_files: Sequence[str],
        planned: Optional[PlannedFile] = None,
    ) -> List[str]:
        findings = []
        findings.extend(self._lint_generated_code_against_contract(rel_path, content))
        if rel_path.endswith(".rs"):
            findings.extend(self._lint_rust_style_against_c_leak(rel_path, content, planned))
            findings.extend(self.registry.duplicate_findings(rel_path, content))
            findings.extend(self.registry.reference_findings(rel_path, content, planned_files))
        return _dedupe_keep_order(findings)

    def _lint_rust_style_against_c_leak(
        self,
        rel_path: str,
        content: str,
        planned: Optional[PlannedFile] = None,
    ) -> List[str]:
        if not rel_path.endswith(".rs"):
            return []

        text = self.registry._strip_comments(content or "")
        lowered = text.lower()
        findings: List[str] = []

        forbidden_patterns = [
            (r"\*(?:mut|const)\b", "raw pointer `*mut`/`*const`"),
            (r"\bunsafe\b", "`unsafe`"),
            (r"\bbox::(?:into_raw|from_raw)\b", "`Box::into_raw`/`Box::from_raw`"),
            (r"\b(?:std|core)::ptr\b|\bptr::(?:null|null_mut)\b|\b(?:null|null_mut)\s*\(", "manual null pointer API"),
            (r"\b(?:std|core)::ffi::c_void\b|\bc_void\b", "`c_void`"),
            (r"#\s*\[\s*repr\s*\(\s*c\s*\)\s*\]", "`#[repr(C)]`"),
            (r"extern\s+\"c\"", '`extern "C"`'),
            (r"#\s*\[\s*no_mangle\s*\]", "`#[no_mangle]`"),
            (r"\bnonnull\s*<", "`NonNull`"),
            (r"#\s*\[\s*allow\s*\([^)]*non_camel_case_types", "`#[allow(non_camel_case_types)]`"),
            (r"#\s*\[\s*allow\s*\([^)]*non_snake_case", "`#[allow(non_snake_case)]`"),
        ]
        for pattern, label in forbidden_patterns:
            if re.search(pattern, lowered):
                findings.append(f"Rust 风格违规：{rel_path} 使用了 C 风格/FFI 构造 {label}")

        c_type_defs = re.findall(
            r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*_t)\b",
            text,
        )
        for type_name in c_type_defs:
            findings.append(f"C ABI 泄漏：{rel_path} 定义了 C 风格类型 `{type_name}`，应重构为 CamelCase Rust 类型")

        c_free_functions = re.findall(
            r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*_(?:free|destroy|delete|new|create|init))\s*\(",
            text,
        )
        for function_name in c_free_functions:
            findings.append(f"C ABI 泄漏：{rel_path} 暴露了 C 风格生命周期函数 `{function_name}`，应改为构造器/Drop/所有权")

        source_functions = planned.source_functions if planned else []
        for function_name in source_functions:
            escaped = re.escape(function_name)
            if re.search(rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+{escaped}\s*\(", text):
                findings.append(f"C ABI 泄漏：{rel_path} 照抄了 C 函数名 `{function_name}`，应改为 Rust 类型方法或 Rust 命名自由函数")

        return findings

    def _fatal_contextual_findings(self, findings: Sequence[str]) -> List[str]:
        fatal_markers = [
            "重复定义",
            "未授权",
            "未获证据",
            "未规划模块",
            "FFI",
            "Rust 风格违规",
            "C ABI 泄漏",
            "raw pointer",
            "Cargo.toml 出现未授权依赖",
        ]
        return [item for item in findings if any(marker in item for marker in fatal_markers)]

    def _repair_contextual_file(
        self,
        planned: PlannedFile,
        planned_files: Sequence[str],
        content: str,
        findings: Sequence[str],
    ) -> str:
        prompt = self._spec_agent().rust_repair_prompt(
            planned=planned,
            findings=findings,
            registry_summary=self._build_targeted_registry_summary(planned),
            plan_summary=self._build_targeted_plan_summary(planned),
            current_content=content,
        )
        response = self._chat_with_context_requests(
            system_prompt=self._spec_agent().rust_repair_system_prompt(),
            user_prompt=prompt,
            label=f"ContextualRustAgent 边界修复 {planned.path}",
            max_read_rounds=2,
        )
        repaired, _ = self._extract_done_marker(response)
        return self._extract_generated_content(repaired, code_lang="" if planned.path.lower() == "readme.md" else "rust")

    def _request_force_write_decision(
        self,
        planned: PlannedFile,
        planned_files: Sequence[str],
        content: str,
        findings: Sequence[str],
    ) -> Tuple[str, bool, str]:
        prompt = self._spec_agent().rust_force_write_prompt(
            planned=planned,
            findings=findings,
            registry_summary=self._build_targeted_registry_summary(planned),
            plan_summary=self._build_targeted_plan_summary(planned),
            current_content=content,
        )
        response = self._chat_with_context_requests(
            system_prompt=self._spec_agent().rust_force_write_system_prompt(),
            user_prompt=prompt,
            label=f"ContextualRustAgent 强制写入确认 {planned.path}",
            max_read_rounds=2,
        )
        response, _ = self._extract_done_marker(response)
        response, force_write, reason = self._extract_force_write_marker(response)
        code_lang = "" if planned.path.lower() == "readme.md" else "rust"
        override = self._extract_generated_content(response, code_lang=code_lang)
        if force_write and not override.strip():
            override = content
        return override, force_write, reason

    def _extract_force_write_marker(self, content: str) -> Tuple[str, bool, str]:
        text = content or ""
        reasons: List[str] = []

        def remove_block(match):
            body = (match.group(1) or "").strip()
            if body:
                reasons.append(_clip_text(body, 500))
            return ""

        text, block_count = re.subn(
            r"(?is)<CGR_FORCE_WRITE>\s*(.*?)\s*</CGR_FORCE_WRITE>",
            remove_block,
            text,
        )

        attr_reasons = []
        for match in re.finditer(r'(?is)<CGR_FORCE_WRITE\b([^>]*)/?>', text):
            attrs = match.group(1) or ""
            reason_match = re.search(r'''reason\s*=\s*["']([^"']+)["']''', attrs, re.IGNORECASE)
            if reason_match:
                attr_reasons.append(reason_match.group(1).strip())

        text, inline_count = re.subn(r"(?is)<CGR_FORCE_WRITE\b[^>]*>", "", text)
        text = re.sub(r"(?is)</CGR_FORCE_WRITE>", "", text)
        force_write = (block_count + inline_count) > 0
        reasons.extend(_clip_text(reason, 500) for reason in attr_reasons if reason)
        return text.strip(), force_write, "; ".join(_dedupe_keep_order(reasons))

    def _build_registry_lib_rs(self, planned_files: Sequence[str]) -> str:
        module_paths = []
        nested_modules: Dict[str, List[str]] = {}
        available_files = {
            path.replace("\\", "/")
            for path in self.registry.files.keys()
            if path.replace("\\", "/").startswith("src/") and path.replace("\\", "/").endswith(".rs")
        }
        for path in planned_files:
            normalized = path.replace("\\", "/")
            if not normalized.startswith("src/") or not normalized.endswith(".rs"):
                continue
            if normalized not in available_files:
                continue
            inner = normalized[4:-3]
            if inner in {"lib", "main"}:
                continue
            if "/" not in inner:
                module_paths.append(inner)
            elif inner.endswith("/mod"):
                module_paths.append(inner[:-4])
            else:
                top, rest = inner.split("/", 1)
                nested_modules.setdefault(top, []).append(rest.replace("/", "::"))

        lines = [
            "//! 自动生成的 crate 入口。",
            "//!",
            "//! 该文件由 ContextualRustAgent 根据已规划模块和已生成符号表重建。",
            "",
        ]
        declared = set()
        for module in _dedupe_keep_order(module_paths):
            lines.append(f"pub mod {module};")
            declared.add(module)

        for top, children in sorted(nested_modules.items()):
            if top in declared:
                continue
            lines.append(f"pub mod {top} {{")
            child_heads = _dedupe_keep_order(child.split('::', 1)[0] for child in children)
            for child in child_heads:
                lines.append(f"    pub mod {child};")
            lines.append("}")
            declared.add(top)

        reexports = []
        for path, symbols in sorted(self.registry.files.items()):
            module_path = self.registry.module_path_for_file(path)
            if not module_path:
                continue
            for item in symbols.all_exportable_items():
                reexports.append(f"pub use {module_path}::{item};")
        reexports = _dedupe_keep_order(reexports)
        if reexports:
            lines.append("")
            lines.extend(reexports)
        if len(lines) == 4:
            lines.append("// 当前没有可声明的源码模块。")
        return "\n".join(lines).rstrip() + "\n"

    def _load_existing_registry(self, planned_files: Sequence[str]):
        self.registry = RustProjectRegistry()
        for rel_path in planned_files:
            normalized = rel_path.replace("\\", "/")
            if not normalized.endswith(".rs"):
                continue
            path = os.path.join(self.project_path, normalized)
            if not os.path.isfile(path):
                continue
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if content.strip():
                self.registry.update_file(normalized, content)

    def _update_api_contract_for_file(self, file_path: str, code: str):
        """
        Persist the same structured references that the contextual linker uses.

        The base contract still keeps its public API summary for compatibility.
        `references` is the stricter linker view: every stored item includes
        visibility and callable signatures, so later files can avoid guessing.
        """
        super()._update_api_contract_for_file(file_path, code)
        rel_path = file_path.replace("\\", "/")
        if not rel_path.endswith(".rs"):
            return

        symbols = self.registry.files.get(rel_path)
        if symbols is None:
            symbols = self.registry.extract_symbols(rel_path, code)
        references = [ref.to_dict() for ref in symbols.references]
        file_entry = self.api_contract.setdefault("files", {}).setdefault(rel_path, {})
        file_entry["references"] = references
        file_entry.setdefault("contract", {})["references"] = references
        self._save_api_contract()

    def generate_code(self) -> List[str]:
        print("开始使用 ContextualRustAgent 生成 Rust 代码...")
        self.spec_index = SpecDocumentIndex(self.doc_contents)
        self.spec_context_agent = ContextualSpecAgent(config=self.config, enable_c_pipeline=False)
        self.spec_context_agent.load_rust_generation_context(
            doc_contents=self.doc_contents,
            source_records=self.source_records,
            translation_contract=self.translation_contract,
        )
        self.contextual_plan = self._request_contextual_plan()
        planned_files = [item.path for item in self.contextual_plan]

        plan_summary = self._format_plan_summary()
        self._initialize_generation_plan(
            project_structure="<contextual_plan>\n" + plan_summary + "\n</contextual_plan>",
            implementation_plan="ContextualRustAgent: demand-driven context, registry constrained, bottom-up generation.",
            planned_files=planned_files,
        )
        self._ensure_api_contract_loaded()
        self._load_existing_registry(planned_files)

        for planned in self.contextual_plan:
            state = self.generation_plan.get("files", {}).get(planned.path, {})
            if self.continue_mode and state.get("status") == "completed" and self._is_completed_file_still_valid(planned.path, state):
                print(f"跳过已完成文件：{planned.path}")
                continue
            if self.continue_mode and self._is_nonempty_existing_file(planned.path):
                print(f"检测到已有非空文件，标记为已完成并跳过：{planned.path}")
                self._mark_generation_status(planned.path, "completed", "existing_nonempty_file")
                continue
            self._generate_contextual_file(planned, planned_files)

        if "src/lib.rs" not in planned_files:
            lib_content = self._build_registry_lib_rs(planned_files)
            lib_path = os.path.join(self.project_path, "src", "lib.rs")
            self._write_file(lib_path, lib_content)
            self.registry.update_file("src/lib.rs", lib_content)
            self._update_api_contract_for_file("src/lib.rs", lib_content)

        print(f"ContextualRustAgent 代码生成完成，共生成 {len(self.generated_files)} 个文件")
        return self.generated_files

    def generate_from_docs(
        self,
        project_name: str,
        output_dir: str,
        doc_paths: List[str],
        c_project_path: str = "",
        source_json_path: str = "",
    ) -> bool:
        print("=" * 60)
        print("开始使用 ContextualRustAgent 根据文档生成 Rust 项目")
        print("=" * 60)
        self.create_rust_project(project_name, output_dir)
        self.load_documents(doc_paths)
        self.configure_source_context(c_project_path=c_project_path, source_json_path=source_json_path)
        if self.source_json_path:
            print(f"已加载源码 JSON：{self.source_json_path}")
        self.generate_code()
        print("=" * 60)
        print("ContextualRustAgent Rust 项目生成完成")
        print("=" * 60)
        return True
