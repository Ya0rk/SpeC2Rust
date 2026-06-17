import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from agent.rust_agent import RustAgent
from agent.alternatives.contextual_spec_agent import ContextualSpecAgent
from agent.alternatives.rust_generation_spec_agent import (
    RustGenerationSpecAgent,
    RustGenerationSpecPrompts,
)
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


def _clip_text(text: str, max_chars: int = 0) -> str:
    return text or ""


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
        if in_specs and normalized.endswith("/pointer.md"):
            return "pointer"
        if in_specs and normalized.endswith("/macro.md"):
            return "macro"
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
        return "\n".join(lines)

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
                findings.append(f"The reference table does not contain method `{owner_type}::{method_name}`; do not invent cross-file methods")
                continue
            if not method_ref.is_public and method_ref.path != normalized_path:
                findings.append(f"Cross-file reference to private method `{owner_type}::{method_name}`, defined in {method_ref.path}")
                continue
            expected_params = [param for param in method_ref.params if param not in {"self", "&self", "&mut self"}]
            if len(args) != len(expected_params):
                findings.append(
                    f"Method call argument mismatch for `{owner_type}::{method_name}`: "
                    f"the reference-table signature is `{method_ref.display_signature()}`, but {len(args)} arguments were passed"
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
                findings.append(f"The reference table does not contain field `{owner_type}::{field_name}`; do not invent cross-file fields")
                continue
            if not field_ref.is_public and owner_path and owner_path != normalized_path:
                findings.append(f"Cross-file reference to private field `{owner_type}::{field_name}`, defined in {owner_path}")

        return _dedupe_keep_order(findings)

    def duplicate_findings(self, rel_path: str, content: str) -> List[str]:
        candidate = self.extract_symbols(rel_path, content)
        findings = []
        for other_path, other in self.files.items():
            if other_path == candidate.path:
                continue
            for symbol in candidate.types:
                if symbol in other.types:
                    findings.append(f"Duplicate type definition `{symbol}`; it already exists in {other_path}")
            for symbol in candidate.functions:
                if symbol in other.functions:
                    findings.append(f"Duplicate free function definition `{symbol}`; it already exists in {other_path}")
            for symbol in candidate.constants:
                if symbol in other.constants:
                    findings.append(f"Duplicate const or static item `{symbol}`; it already exists in {other_path}")
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
                findings.append(f"Reference to planned module `crate::{module}` before it has been generated")
            else:
                findings.append(f"Reference to unplanned module `crate::{module}`")
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
        return "\n".join(lines) if lines else "(no generated Rust symbols yet)"


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
        self.rust_context_agent: Optional[RustGenerationSpecAgent] = None
        self.spec_ablation_enabled = True
        self.registry = RustProjectRegistry()
        self.contextual_plan: List[PlannedFile] = []
        self._plan_by_path: Dict[str, PlannedFile] = {}
        self._cfile_to_module: Dict[str, str] = {}
        self._module_spec_docs: Dict[str, Dict[str, str]] = {}
        self.entry_kind = "auto"
        self.use_pointer_agent = False
        self.use_macro_agent = False

    def configure_optional_evidence(
        self,
        use_pointer_agent: bool = False,
        use_macro_agent: bool = False,
    ) -> None:
        self.use_pointer_agent = bool(use_pointer_agent)
        self.use_macro_agent = bool(use_macro_agent)

    def _filter_optional_evidence_documents(self) -> None:
        """Keep stale optional evidence out of prompts unless its switch is enabled."""
        retained: Dict[str, str] = {}
        for path, content in self.doc_contents.items():
            normalized = path.replace("\\", "/").lower()
            basename = os.path.basename(normalized)
            is_pointer = basename in {"pointer.md", "pointer_guidance.md"}
            is_macro = basename in {"macro.md", "macro_guidance.md"}
            is_combined_summary = normalized.endswith(
                "/04_gaps_and_risks/001_pointer_macro_summary.md"
            )

            if is_pointer and not self.use_pointer_agent:
                continue
            if is_macro and not self.use_macro_agent:
                continue
            if is_combined_summary and not (self.use_pointer_agent and self.use_macro_agent):
                continue
            retained[path] = content
        self.doc_contents = retained

    def _set_request_label(self, label: str):
        if hasattr(self.llm, "set_request_label"):
            self.llm.set_request_label(label)

    def configure_source_context(self, c_project_path: str = "", source_json_path: str = ""):
        super().configure_source_context(c_project_path=c_project_path, source_json_path=source_json_path)
        self.translation_contract = {}
        self.allowed_rust_files = []
        self.allowed_dependencies = set()
        self.dependency_policy = ""
        if not self.source_records and self.source_project_path:
            self.source_records = self._scan_c_project_source_records(self.source_project_path)
            self.source_context_summary = self._build_source_context_summary()
            self.tool_interface_constraints = self._build_tool_interface_constraints()
            self.source_interface_summary = self._build_source_interface_summary()
            if self.source_records:
                print(f"已直接扫描 C 源码：{len(self.source_records)} 条源码记录")

    def _scan_c_project_source_records(self, project_path: str) -> List[Dict]:
        root = Path(project_path)
        if not root.is_dir():
            return []

        records: List[Dict] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".c", ".h"}:
                continue
            rel_path = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            records.extend(self._extract_c_function_records(rel_path, text))
            if not any(record.get("file") == rel_path for record in records):
                records.append(
                    {
                        "name": os.path.splitext(os.path.basename(rel_path))[0],
                        "file": rel_path,
                        "span": f"{rel_path}:1:1:{max(1, len(text.splitlines()))}:1",
                        "source": text,
                        "num_lines": len(text.splitlines()),
                        "calls": [],
                        "func_defid": f"{rel_path}:{os.path.splitext(os.path.basename(rel_path))[0]}",
                    }
                )
        return records

    def _extract_c_function_records(self, rel_path: str, text: str) -> List[Dict]:
        records: List[Dict] = []
        pattern = re.compile(
            r"(?m)^[A-Za-z_][A-Za-z0-9_\s\*\(\),]*?\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{"
        )
        for match in pattern.finditer(text or ""):
            name = match.group("name")
            if name in {"if", "for", "while", "switch"}:
                continue
            open_index = text.find("{", match.start(), match.end())
            close_index = self._find_matching_c_brace(text, open_index)
            if close_index == -1:
                continue
            start = self._function_start_index(text, match.start())
            source = text[start: close_index + 1].strip()
            start_line = text.count("\n", 0, start) + 1
            end_line = text.count("\n", 0, close_index) + 1
            records.append(
                {
                    "name": name,
                    "file": rel_path,
                    "span": f"{rel_path}:{start_line}:1:{end_line}:1",
                    "source": source,
                    "num_lines": max(1, end_line - start_line + 1),
                    "calls": [],
                    "func_defid": f"{rel_path}:{name}",
                }
            )
        return records

    @staticmethod
    def _function_start_index(text: str, match_start: int) -> int:
        previous_blank = text.rfind("\n\n", 0, match_start)
        if previous_blank == -1:
            return max(0, text.rfind("\n", 0, match_start) + 1)
        return previous_blank + 2

    @staticmethod
    def _find_matching_c_brace(text: str, open_index: int) -> int:
        if open_index < 0:
            return -1
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

    def _context_provider(self) -> RustGenerationSpecAgent:
        if self.rust_context_agent is None:
            self.rust_context_agent = RustGenerationSpecAgent(
                doc_contents={},
                source_records=self.source_records,
                translation_contract=self.translation_contract,
                config=self.config,
            )
        return self.rust_context_agent

    def _spec_agent(self) -> ContextualSpecAgent:
        if self.spec_context_agent is None:
            self.spec_context_agent = ContextualSpecAgent(config=self.config, enable_c_pipeline=False)
        return self.spec_context_agent

    def _ablation_prompt_agent(self) -> RustGenerationSpecPrompts:
        return RustGenerationSpecPrompts

    def _has_spec_context(self) -> bool:
        if self.spec_ablation_enabled:
            return False
        return self.spec_context_agent is not None

    def _spec_overview(self, max_chars: int = 12000) -> str:
        if self.spec_ablation_enabled:
            return self._context_provider().overview(max_chars=max_chars)
        if not self.spec_context_agent:
            return self.spec_index.overview(max_chars=max_chars)
        return self.spec_context_agent.rust_context_overview(max_chars=max_chars)

    def _spec_context_for_query(self, query: str, max_chars: int = 18000) -> str:
        if self.spec_ablation_enabled:
            return self._context_provider().context_for_query(query, max_chars=max_chars)
        if not self.spec_context_agent:
            return self.spec_index.select_for_query(query, max_slices=5, max_chars=max_chars)
        return self.spec_context_agent.rust_context_for_query(query, max_chars=max_chars)

    def _spec_build_file_plan(self, allowed_files: Sequence[str]) -> List[object]:
        if self.spec_ablation_enabled:
            return self._context_provider().build_file_plan(allowed_files=allowed_files)
        if not self.spec_context_agent:
            return []
        return self.spec_context_agent.build_rust_file_plan(allowed_files=allowed_files)

    def _spec_infer_candidate_files(self) -> List[str]:
        if self.spec_ablation_enabled:
            return self._context_provider().infer_candidate_files()
        if not self.spec_context_agent:
            return self.spec_index.infer_candidate_rust_files()
        return self.spec_context_agent.infer_candidate_rust_files()

    def _spec_context_for_file(self, planned: PlannedFile) -> str:
        if self.spec_ablation_enabled:
            return self._context_provider().context_for_file(planned)
        if not self.spec_context_agent:
            return self.spec_index.select_for_file(
                planned.path,
                owns=planned.owns,
                spec_queries=planned.spec_queries,
            )
        return self.spec_context_agent.rust_context_for_planned_file(planned)

    def _requested_entry_kind(self) -> str:
        value = str(getattr(self, "entry_kind", "auto") or "auto").strip().lower()
        return value if value in {"auto", "main", "lib"} else "auto"

    def _contract_project_kind(self) -> str:
        project = (self.translation_contract or {}).get("project", {})
        return str(project.get("kind", "") or "").strip().lower()

    def _source_file_looks_like_test(self, path: str) -> bool:
        normalized = (path or "").replace("\\", "/").lower()
        base = os.path.basename(normalized)
        if normalized.startswith(("tests/", "test/", "examples/", "example/", "bench/", "benches/")):
            return True
        return bool(re.search(r"(^|/)(test|tests|testmain|benchmark|bench)[._/-]", normalized)) or base in {
            "test.c",
            "tests.c",
            "testmain.c",
            "benchmark.c",
        }

    def _has_production_main_source(self) -> bool:
        for item in self.source_records or []:
            if str(item.get("name", "")).strip() != "main":
                continue
            if self._source_file_looks_like_test(str(item.get("file", ""))):
                continue
            return True
        functions = (self.translation_contract or {}).get("functions", [])
        for item in (functions if isinstance(functions, list) else []):
            if str(item.get("name", "")).strip() != "main":
                continue
            if str(item.get("role", "")).strip().lower() in {"test_runner", "test_case", "test_helper", "example_entry"}:
                continue
            if self._source_file_looks_like_test(str(item.get("source", ""))):
                continue
            return True
        return False

    def _effective_entry_kind(self) -> str:
        requested = self._requested_entry_kind()
        if requested in {"main", "lib"}:
            return requested
        project_kind = self._contract_project_kind()
        if project_kind in {"cli", "mixed", "executable", "binary", "bin"}:
            return "main"
        if project_kind in {"library", "lib"}:
            return "lib"
        return "main" if self._has_production_main_source() else "lib"

    def _entry_kind_context(self) -> str:
        entry_kind = self._effective_entry_kind()
        requested = self._requested_entry_kind()
        if entry_kind == "main":
            return (
                "Rust crate entry strategy: generate an executable project.\n"
                f"- User choice: {requested}\n"
                "- `src/main.rs` is the crate entry point and must carry the CLI/main flow.\n"
                "- Do not plan, generate, or depend on `src/lib.rs`; do not treat `main.rs` as a library module and re-export it from lib."
            )
        return (
            "Rust crate entry strategy: generate a library project.\n"
            f"- User choice: {requested}\n"
            "- `src/lib.rs` is the crate entry point and is responsible for declaring modules and necessary re-exports.\n"
            "- Do not plan, generate, or depend on `src/main.rs` unless the user explicitly switches to main."
        )

    def _filter_entry_files(self, files: Sequence[str]) -> List[str]:
        cleaned = self._sanitize_generation_file_list(list(files or []))
        entry_kind = self._effective_entry_kind()
        entry_file = "src/main.rs" if entry_kind == "main" else "src/lib.rs"
        forbidden_entry = "src/lib.rs" if entry_kind == "main" else "src/main.rs"

        filtered: List[str] = []
        for item in cleaned:
            normalized = item.replace("\\", "/")
            if normalized == forbidden_entry:
                continue
            filtered.append(normalized)

        if "Cargo.toml" not in filtered:
            filtered.insert(0, "Cargo.toml")

        if entry_file not in filtered:
            insert_at = 1 if filtered and filtered[0] == "Cargo.toml" else 0
            filtered.insert(insert_at, entry_file)

        if "README.md" not in filtered:
            filtered.append("README.md")

        return _dedupe_keep_order(filtered)

    def _build_translation_contract_context(self, max_chars: int = 0) -> str:
        if not self.translation_contract:
            return ""
        boundary = self.translation_contract.get("generation_boundary", {})
        functions = self.translation_contract.get("functions", [])
        types = self.translation_contract.get("types", [])
        parts = [
            "Migration contract (highest priority):",
            f"- Project type: {self.translation_contract.get('project', {}).get('kind', 'unknown')}",
            f"- Allowed generated files: {', '.join(self.allowed_rust_files) if self.allowed_rust_files else 'unrestricted'}",
            f"- Dependency policy: {boundary.get('dependency_policy', 'unspecified')}",
            f"- Test files allowed: {bool(boundary.get('allow_tests', False))}",
            f"- Example files allowed: {bool(boundary.get('allow_examples', False))}",
            f"- Benchmarks allowed: {bool(boundary.get('allow_benches', False))}",
            f"- FFI allowed: {bool(boundary.get('allow_ffi', False))}",
            "",
            "Function role summary:",
        ]
        for item in functions:
            parts.append(
                f"- {item.get('id', '')} {item.get('name', 'unknown')} "
                f"[{item.get('role', 'unknown')}] {item.get('source', '')}"
        )
        if types:
            parts.append("")
            parts.append("Type fact summary:")
            for item in types:
                field_names = ", ".join(field.get("name", "") for field in item.get("fields", []) if field.get("name"))
                parts.append(f"- {item.get('id', '')} {item.get('name', 'unknown')} {item.get('source', '')}: {field_names or 'fields require source lookup'}")
        forbidden = self.translation_contract.get("forbidden_without_evidence", [])
        if forbidden:
            parts.append("")
            parts.append(f"Forbidden without evidence: {', '.join(str(item) for item in forbidden)}")
        return "\n".join(parts).strip()

    def _build_static_project_context(self) -> str:
        parts = [
            f"Project name: {self.project_name}",
            "Goal: rewrite the C project according to the spec into a structured, idiomatic, compilable Rust project.",
            "Context strategy: do not expand all documents by default; use `<CGR_READ>` to request more information when needed.",
            "Generation boundary: implement only the capabilities already present in the input C project and spec; do not proactively expand into unsupported threading, serialization, networking, CLI, recovery mechanisms, or similar features.",
            "Rust migration contract (highest priority):\n" + self._rust_rewrite_contract(),
        ]

        contract_context = self._build_translation_contract_context()
        if contract_context:
            parts.append("Migration contract (highest priority):\n" + contract_context)
            scope = self._contract_scope_instructions()
            if scope:
                parts.append(scope)
        parts.append(self._entry_kind_context())

        if self.source_interface_summary:
            parts.append("Original C external interface facts:\n" + self.source_interface_summary)
        if self.tool_interface_constraints:
            parts.append("Tool/CLI interface preservation constraints:\n" + self.tool_interface_constraints)
        if self.spec_ablation_enabled and self.source_context_summary:
            parts.append("Original C source summary:\n" + self.source_context_summary)
        if self.spec_ablation_enabled and self.rust_context_agent:
            overview = self._spec_overview()
            parts.append("Available direct C source index (index only, not full text):\n" + overview)
        if self.spec_index.slices:
            overview = self._spec_overview()
            parts.append("Available spec document index (index only, not full text):\n" + overview)
        return "\n\n".join(part for part in parts if part).strip()

    def _rust_rewrite_contract(self, planned: Optional[PlannedFile] = None) -> str:
        if self.spec_ablation_enabled:
            del planned
            return (
                "Ablation rewrite guidance: translate the observed C source into Rust as directly as possible. "
                "Use the C source as the main evidence; no spec-derived migration contract is available."
            )
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
            if self.spec_ablation_enabled:
                messages.append(
                    {
                        "role": "user",
                        "content": self._ablation_prompt_agent().read_materials_followup(materials),
                    }
                )
                continue
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

    def _materialize_read_requests(self, read_requests: Sequence[Dict[str, str]], max_chars: int = 40000) -> str:
        blocks = []
        total = 0
        per_request_budget = 12000
        for request in read_requests:
            kind = (request.get("kind") or "").lower()
            query = request.get("query") or ""
            if self.spec_ablation_enabled and kind in {"spec", "doc", "docs"}:
                content = "Spec/doc context is disabled in this ablation run. Request C source instead."
            elif kind in {"spec", "doc", "docs"}:
                content = self._spec_context_for_query(query, max_chars=per_request_budget)
            elif kind in {"source", "c", "c_source"}:
                content = self._read_source_material(query, max_chars=per_request_budget)
            elif kind in {"rust", "generated", "file"}:
                content = self._read_generated_rust_material(query, max_chars=per_request_budget)
            elif kind in {"registry", "symbols", "symbol"}:
                content = self.registry.summary(max_chars=per_request_budget)
            elif kind in {"plan", "project_plan"}:
                content = self._format_plan_summary()
            else:
                content = f"Unsupported read type: {kind}"

            block = f"\n\n=== READ {kind or 'unknown'}: {query or '(empty)'} ===\n{content}\n"
            if max_chars and total + len(block) > max_chars:
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
        normalized_query = (query or "").replace("\\", "/").strip().lstrip("/")
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
                return Path(direct).read_text(encoding="utf-8", errors="ignore")
            except Exception as exc:
                return f"Failed to read source file: {exc}"

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
        return "No matching C source material was found. Try requesting by function name or file name, for example {\"kind\":\"source\",\"query\":\"function_name\"}"

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
                    f"{c.get('caller', '?').rsplit(':', 1)[-1]}(): {str(c.get('source', '')).strip()}"
                    for c in calls if c.get("caller")
                )
                if caller_lines:
                    block_lines.append(f"Called by: {caller_lines}")
            block_lines.append(record.get("source", ""))
            block = "\n".join(block_lines) + "\n"
            if max_chars and total + len(block) > max_chars and parts:
                break
            parts.append(block)
            total += len(block)
        return "".join(parts).strip()

    def _read_generated_rust_material(self, query: str, max_chars: int = 14000) -> str:
        normalized = (query or "").replace("\\", "/").strip()
        path = self._safe_join_existing_file(self.project_path, normalized)
        if not path:
            return f"The generated project does not contain this Rust file: {normalized}"
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return f"Failed to read Rust file: {exc}"

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
        if self.spec_ablation_enabled:
            fallback_files = self._fallback_file_list()
            plan = [self._fallback_planned_file(path) for path in fallback_files]
            plan = self._apply_entry_policy_to_plan(plan)
            self._plan_by_path = {item.path: item for item in plan}
            return plan

        if self._has_spec_context():
            fallback_files = self._fallback_file_list()
            file_specs = self._spec_build_file_plan(allowed_files=fallback_files)
            plan = [self._planned_file_from_spec(item) for item in file_specs]
            plan = self._apply_entry_policy_to_plan(plan)
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
            return self._filter_entry_files(self.allowed_rust_files)

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
        return self._filter_entry_files(_dedupe_keep_order(inferred or ["Cargo.toml", "src/lib.rs", "README.md"]))

    def _apply_entry_policy_to_plan(self, plan: Sequence[PlannedFile]) -> List[PlannedFile]:
        allowed_paths = self._filter_entry_files([item.path for item in plan])
        by_path = {item.path.replace("\\", "/"): item for item in plan}
        filtered: List[PlannedFile] = []
        for path in allowed_paths:
            item = by_path.get(path) or self._fallback_planned_file(path)
            item.path = path
            item.depends_on = [
                dep.replace("\\", "/")
                for dep in item.depends_on
                if dep.replace("\\", "/") in allowed_paths
            ]
            filtered.append(item)
        return self._sort_contextual_plan(filtered)

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

        sanitized_paths = self._filter_entry_files(_dedupe_keep_order(requested_order))
        if not sanitized_paths:
            sanitized_paths = self._filter_entry_files(fallback_files)

        plan = [by_path.get(path) or self._fallback_planned_file(path) for path in sanitized_paths]
        plan = self._apply_entry_policy_to_plan(plan)
        self._plan_by_path = {item.path: item for item in plan}
        return plan

    def _fallback_planned_file(self, path: str) -> PlannedFile:
        normalized = path.replace("\\", "/")
        stem = os.path.splitext(os.path.basename(normalized))[0]
        if self.spec_ablation_enabled:
            role = "Rust project file"
            owns: List[str] = []
            spec_queries: List[str] = []
            source_files: List[str] = []
            source_functions: List[str] = []
            if normalized == "Cargo.toml":
                role = "Cargo package manifest; locally generate the minimal configuration"
            elif normalized == "README.md":
                role = "Project README"
            elif normalized == "src/main.rs":
                role = "Executable crate entry point responsible for the CLI/main flow and for calling internal modules; do not re-export it from lib.rs as a library module"
            elif normalized == "src/lib.rs":
                role = "Crate entry point, locally rebuilt from generated modules"
            elif normalized.endswith(".rs"):
                role = f"Direct translation target for C source file `{stem}.c`"
                for record in self.source_records:
                    source_file = str(record.get("file", "")).replace("\\", "/")
                    if os.path.splitext(os.path.basename(source_file))[0] != stem:
                        continue
                    if source_file not in source_files:
                        source_files.append(source_file)
                    name = str(record.get("name", "")).strip()
                    if name and name not in source_functions:
                        source_functions.append(name)
            return PlannedFile(
                path=normalized,
                role=role,
                owns=owns,
                spec_queries=spec_queries,
                source_files=source_files,
                source_functions=source_functions,
            )

        role = "Rust project file"
        owns: List[str] = []
        spec_queries = [stem]
        if normalized == "Cargo.toml":
            role = "Cargo package manifest; locally generate the minimal configuration"
        elif normalized == "README.md":
            role = "Project README"
        elif normalized == "src/main.rs":
            role = "Executable crate entry point responsible for the CLI/main flow and for calling internal modules; do not re-export it from lib.rs as a library module"
        elif normalized == "src/lib.rs":
            role = "Crate entry point, locally rebuilt from generated modules"
        elif normalized.endswith(".rs"):
            role = f"Implement the core Rust types, functions, and algorithms related to `{stem}`"
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
            if normalized.startswith("src/") and base not in {"lib.rs", "main.rs"}:
                if any(token in normalized for token in ["type", "data", "error", "const", "bound", "point", "node"]):
                    return (1, normalized)
                return (2, normalized)
            if base == "lib.rs":
                return (3, normalized)
            if base == "main.rs":
                return (4, normalized)
            if base == "readme.md":
                return (5, normalized)
            return (6, normalized)

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
                lines.append(f"   source_functions: {', '.join(item.source_functions)}")
            if item.spec_queries:
                lines.append(f"   spec_queries: {', '.join(item.spec_queries)}")
        return "\n".join(lines)

    def _generate_contextual_project_structure(self) -> str:
        if self.spec_ablation_enabled:
            print("消融模式：跳过 LLM 项目结构设计")
            files = "\n".join(f"- {item.path}" for item in self.contextual_plan)
            return f"Direct C-source file mapping only:\n{files}"

        print("生成项目结构设计...")
        plan_summary = self._format_plan_summary()
        static_context = self._build_static_project_context()
        spec_overview = self._spec_overview(max_chars=8000)

        prompt = self._spec_agent().rust_project_structure_prompt(
            project_name=self.project_name,
            plan_summary=plan_summary,
            static_context=static_context,
            spec_overview=spec_overview,
        )
        response = self._chat_with_context_requests(
            system_prompt=self._spec_agent().rust_project_structure_system_prompt(),
            user_prompt=prompt,
            label="ContextualRustAgent 项目结构设计",
            max_read_rounds=3,
        )
        structure, _ = self._extract_done_marker(response)
        print("项目结构设计完成")
        return structure

    def _generate_contextual_implementation_plan(self, project_structure: str) -> str:
        if self.spec_ablation_enabled:
            del project_structure
            print("消融模式：跳过 LLM 实现计划")
            return "Generate files in the direct C-source mapping order without spec-derived planning."

        print("生成实现计划...")
        plan_summary = self._format_plan_summary()
        planned_files = [item.path for item in self.contextual_plan]

        prompt = self._spec_agent().rust_implementation_plan_prompt(
            project_structure=project_structure,
            plan_summary=plan_summary,
            files_list=planned_files,
        )
        response = self._chat_with_context_requests(
            system_prompt=self._spec_agent().rust_implementation_plan_system_prompt(),
            user_prompt=prompt,
            label="ContextualRustAgent 实现计划",
            max_read_rounds=3,
        )
        plan_text, _ = self._extract_done_marker(response)

        if '<implementation_plan>' in plan_text:
            parts = plan_text.split('<implementation_plan>')
            plan_text = parts[1].split('</implementation_plan>')[0].strip()

        if '<new_files_to_generate>' in plan_text and '</new_files_to_generate>' in plan_text:
            try:
                tag_content = plan_text.split('<new_files_to_generate>')[1].split('</new_files_to_generate>')[0].strip()
                new_order = self._parse_reorder_tag(tag_content, planned_files)
                if new_order:
                    print(f"从实现计划中提取新文件顺序：{new_order}")
                    self._reorder_contextual_plan(new_order)
            except Exception as e:
                print(f"解析新文件顺序失败：{e}，保留原始顺序")

        print("实现计划制定完成")
        return plan_text

    def _parse_reorder_tag(self, tag_content: str, current_files: List[str]) -> List[str]:
        text = tag_content.strip()
        allowed = {f.replace("\\", "/") for f in current_files}
        candidates: List[str] = []
        for line in text.splitlines():
            cleaned = line.strip().strip("-").strip("*").strip()
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned).strip()
            cleaned = cleaned.strip('"').strip("'").strip("`").strip()
            normalized = cleaned.replace("\\", "/")
            if normalized in allowed:
                candidates.append(normalized)
        return _dedupe_keep_order(candidates) if candidates else []

    def _reorder_contextual_plan(self, new_order: List[str]):
        by_path = {item.path.replace("\\", "/"): item for item in self.contextual_plan}
        reordered: List[PlannedFile] = []
        seen: set = set()
        for path in new_order:
            if path in by_path and path not in seen:
                reordered.append(by_path[path])
                seen.add(path)
        for item in self.contextual_plan:
            normalized = item.path.replace("\\", "/")
            if normalized not in seen:
                reordered.append(item)
                seen.add(normalized)
        self.contextual_plan = reordered
        self.contextual_plan = self._apply_entry_policy_to_plan(self.contextual_plan)
        self._plan_by_path = {item.path: item for item in self.contextual_plan}

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
        if self.spec_ablation_enabled:
            relevant_paths = {current_path}

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
                lines.append(f"   source_functions: {', '.join(item.source_functions)}")
        if not lines:
            return self._format_plan_summary()

        global_index = ["\n--- Global File Index (paths and symbol ownership only) ---"]
        for item in self.contextual_plan:
            item_path = item.path.replace("\\", "/")
            if item_path in relevant_paths:
                continue
            owns_hint = f" owns: {', '.join(item.owns)}" if item.owns else ""
            if self.spec_ablation_enabled:
                owns_hint = ""
            global_index.append(f"  - {item.path}{owns_hint}")
        if len(global_index) > 1:
            lines.extend(global_index)

        module_ctx = self._module_context_for_file(planned)
        if module_ctx:
            lines.append(module_ctx)

        return "\n".join(lines)

    def _build_module_index(self):
        """从 translation_contract 和 doc_contents 构建 C 文件→模块 和 模块→spec文档 的索引。"""
        self._cfile_to_module = {}
        self._module_spec_docs = {}
        if self.spec_ablation_enabled:
            return

        module_units = (self.translation_contract or {}).get("module_units", [])
        module_dir_by_name: Dict[str, str] = {}
        for i, unit in enumerate(module_units, 1):
            module_name = unit.get("name", "unknown")
            feature_name = module_name.replace("-", "_").replace(" ", "_")
            dir_prefix = f"{i:03d}-{feature_name}-rust-port"
            module_dir_by_name[module_name] = dir_prefix
            for c_file in unit.get("files", []):
                normalized = c_file.replace("\\", "/")
                stem = os.path.splitext(os.path.basename(normalized))[0].lower()
                self._cfile_to_module[normalized] = module_name
                self._cfile_to_module[stem] = module_name

        doc_keys = ["tasks.md", "plan.md", "spec.md"]
        for module_name, dir_prefix in module_dir_by_name.items():
            docs: Dict[str, str] = {}
            for doc_key in doc_keys:
                for path, content in (self.doc_contents or {}).items():
                    normalized = path.replace("\\", "/")
                    if f"specs/{dir_prefix}/{doc_key}" in normalized or \
                       f"specs\\{dir_prefix}\\{doc_key}" in normalized.replace("/", "\\"):
                        docs[doc_key] = (content or "").strip()
                        break
            if docs:
                self._module_spec_docs[module_name] = docs

        if self._module_spec_docs:
            print(f"  模块上下文索引: {len(self._module_spec_docs)} 个模块, "
                  f"{len(self._cfile_to_module)} 个 C 文件映射")

    def _module_context_for_file(self, planned: PlannedFile) -> str:
        """返回该文件所属模块的 tasks.md / plan.md / spec.md 拼接内容。"""
        if not self._module_spec_docs:
            return ""

        modules = set()
        for source in (planned.source_files or []):
            normalized = source.replace("\\", "/")
            stem = os.path.splitext(os.path.basename(normalized))[0].lower()
            if normalized in self._cfile_to_module:
                modules.add(self._cfile_to_module[normalized])
            elif stem in self._cfile_to_module:
                modules.add(self._cfile_to_module[stem])

        if not modules:
            target_stem = os.path.splitext(os.path.basename(planned.path))[0].lower()
            if target_stem in self._cfile_to_module:
                modules.add(self._cfile_to_module[target_stem])

        if not modules:
            return ""

        parts = []
        for module_name in sorted(modules):
            docs = self._module_spec_docs.get(module_name)
            if not docs:
                continue
            parts.append(f"\n--- Owning Module: {module_name} ---")
            for doc_key in ["tasks.md", "plan.md", "spec.md"]:
                content = docs.get(doc_key, "")
                if not content:
                    continue
                parts.append(f"=== {doc_key} ===")
                parts.append(content)

        return "\n".join(parts) if parts else ""

    def _build_targeted_registry_summary(self, planned: PlannedFile) -> str:
        """只保留当前文件依赖的文件的符号，减少无关上下文。
        末尾追加轻量全局类型索引，避免 depends_on 不完整时重复定义符号。"""
        if self.spec_ablation_enabled:
            del planned
            if not self.registry.files:
                return "(no generated Rust files yet)"
            return "\n".join(f"- {path}" for path in sorted(self.registry.files.keys()))

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

        global_type_index = ["\n--- Global Generated Type Index (duplicate definitions forbidden) ---"]
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

        target_files = {sf.replace("\\", "/").lstrip("/").lower() for sf in (planned.source_files or [])}
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

        # 排序：高分优先；同分时短函数（真正实现）优先于长函数（test/main）
        scored.sort(key=lambda item: (-item[0], int(item[1].get("num_lines", 0)), item[1].get("name", "")))

        # 跳过 test/main 等测试入口，不浪费内联 token
        _skip_inline = {"sdstest", "main", "test_cond", "test_report"}

        MAX_INLINE = 10
        MAX_INLINE_LINES = 80  # 超过此行数的函数只放索引
        inline_records = []
        index_records = []
        for score, record in scored:
            name_lower = str(record.get("name", "")).lower()
            num_lines = int(record.get("num_lines", 0))
            if (
                len(inline_records) < MAX_INLINE
                and score >= 20
                and name_lower not in _skip_inline
                and num_lines <= MAX_INLINE_LINES
            ):
                inline_records.append(record)
            else:
                index_records.append(record)

        parts = [f"C source related to Rust file `{planned.path}`:"]

        if inline_records:
            parts.append("\n## Key C Source (Inlined)")
            for record in inline_records:
                calls = record.get("calls", [])[:3]
                block_lines = [f"### {record['name']} [{record['file']} {record['span']}] ({record.get('num_lines', '?')} lines)"]
                if calls:
                    call_lines = ", ".join(
                        f"{call.get('caller', '?').rsplit(':', 1)[-1]}()"
                        for call in calls
                    )
                    block_lines.append(f"Called by: {call_lines}")
                snippet = record.get("source", "").strip()
                block_lines.append(f"```c\n{snippet}\n```")
                block = "\n".join(block_lines)
                parts.append(block)

        if index_records:
            parts.append("\n## C Source Index (Request details with <CGR_READ>)")
            for record in index_records:
                sig = self._extract_c_signature(record)
                callers = record.get("calls", [])[:3]
                caller_hint = ""
                if callers:
                    caller_names = [c.get("caller", "").rsplit(":", 1)[-1] for c in callers if c.get("caller")]
                    if caller_names:
                        caller_hint = f" | called by: {', '.join(caller_names)}"
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
        return sig

    def _build_file_prompt(self, planned: PlannedFile, planned_files: Sequence[str]) -> str:
        spec_context = self._spec_context_for_file(planned)
        source_context = ""
        if planned.path.endswith(".rs"):
            source_context = self._build_targeted_source_context(planned)
        if self.spec_ablation_enabled:
            return f"""Generate the final content of `{planned.path}` from the C source evidence below.

Ablation constraints:
- The spec module, migration contract, structured owns/dependencies, and reference-table guardrails are disabled.
- Follow the C source directly and keep this file self-contained when practical.
- Output only the file content and append `<CGR_DONE>` when complete.
- If required C source is missing, request it with `<CGR_READ>`.

All planned files:
{', '.join(planned_files)}

Current file mapping:
- target: {planned.path}
- role: {planned.role}
- source_files: {', '.join(planned.source_files) or '(none)'}
- source_functions: {', '.join(planned.source_functions) or '(none)'}

Already generated Rust files:
{self._build_targeted_registry_summary(planned)}

Relevant C source:
{source_context or '(no matching source found; use <CGR_READ> to request source)'}
"""
        return self._spec_agent().rust_file_generation_prompt(
            planned=planned,
            planned_files=planned_files,
            plan_summary=self._build_targeted_plan_summary(planned),
            registry_summary=self._build_targeted_registry_summary(planned),
            spec_context=spec_context,
            source_context=source_context,
        )

    def _generate_file_with_continuation(
        self,
        system_prompt: str,
        user_prompt: str,
        label: str,
        code_lang: str = "rust",
        max_read_rounds: int = 5,
        max_continuation_rounds: int = 4,
    ) -> str:
        """先走 _chat_with_context_requests 完成 read 循环，
        然后检测是否因 max_tokens 截断（无 <CGR_DONE>），
        如果截断则自动续写，最终返回拼接后的完整代码。"""

        response = self._chat_with_context_requests(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            label=label,
            max_read_rounds=max_read_rounds,
        )
        content, done = self._extract_done_marker(response)
        content = self._extract_generated_content(content, code_lang=code_lang)

        if done or not code_lang:
            return content

        accumulated = content
        for cont_round in range(1, max_continuation_rounds + 1):
            continuation_user = (
                f"Your previous output was truncated at max_tokens."
                f"Here is all code you have already output:\n"
                f"```{code_lang}\n{accumulated}\n```\n\n"
                f"Continue from the truncation point without repeating existing code, and append `<CGR_DONE>` at the end when finished.\n"
                f"Only output the code continuation and `<CGR_DONE>`; do not explain."
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": f"```{code_lang}\n{accumulated}\n```"},
                {"role": "user", "content": continuation_user},
            ]
            print(f"  续写 {label} [continuation {cont_round}]")
            reply = self._read_llm(messages, f"{label} [continuation {cont_round}]")
            chunk, chunk_done = self._extract_done_marker(reply)
            chunk = self._extract_generated_content(chunk, code_lang=code_lang)
            chunk = self._strip_outer_code_fences(chunk, code_lang)

            if chunk.strip():
                if not accumulated.endswith("\n"):
                    accumulated += "\n"
                accumulated += chunk.strip()

            if chunk_done:
                print(f"  续写完成 ({cont_round} round(s))")
                break
        else:
            print(f"  续写达到上限 ({max_continuation_rounds} rounds)，使用当前累积内容")
        return accumulated

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

        code_lang = "" if normalized.lower() == "readme.md" else "rust"
        file_generation_system_prompt = self._spec_agent().rust_file_generation_system_prompt()
        if self.spec_ablation_enabled:
            file_generation_system_prompt = self._ablation_prompt_agent().file_generation_system_prompt()
        content = self._generate_file_with_continuation(
            system_prompt=file_generation_system_prompt,
            user_prompt=self._build_file_prompt(planned, planned_files),
            label=f"ContextualRustAgent 代码生成 {planned.path}",
            code_lang=code_lang,
            max_read_rounds=5,
            max_continuation_rounds=4,
        )
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
        if self.spec_ablation_enabled:
            del rel_path, content, planned_files, planned
            return _dedupe_keep_order(findings)
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
                findings.append(f"Rust style violation: {rel_path} uses C-style/FFI construct {label}")

        c_type_defs = re.findall(
            r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*_t)\b",
            text,
        )
        for type_name in c_type_defs:
            findings.append(f"C ABI leak: {rel_path} defines C-style type `{type_name}`; refactor it into a CamelCase Rust type")

        c_free_functions = re.findall(
            r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*_(?:free|destroy|delete|new|create|init))\s*\(",
            text,
        )
        for function_name in c_free_functions:
            findings.append(f"C ABI leak: {rel_path} exposes C-style lifecycle function `{function_name}`; convert it to a constructor, Drop, or ownership semantics")

        source_functions = planned.source_functions if planned else []
        _skip_names = {
            "main", "s_malloc", "s_realloc", "s_free", "s_trymalloc",
            "test_cond", "test_report", "main_root",
        }
        for function_name in source_functions:
            if function_name in _skip_names:
                continue
            if re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", function_name) and "_" in function_name:
                prefix = function_name.split("_")[0]
                source_stems = {
                    os.path.splitext(os.path.basename(sf))[0].lower()
                    for sf in (planned.source_files or [])
                }
                if prefix not in source_stems:
                    continue
            escaped = re.escape(function_name)
            if not re.search(rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+{escaped}\s*\(", text):
                continue
            if "_" not in function_name and re.search(
                rf"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+{escaped}\s*\(\s*&(?:mut\s+)?self",
                text,
            ):
                continue
            findings.append(f"C ABI leak: {rel_path} copied C function name `{function_name}`; convert it to a Rust type method or Rust-named free function")

        return findings

    def _fatal_contextual_findings(self, findings: Sequence[str]) -> List[str]:
        fatal_markers = [
            "Duplicate",
            "unauthorized",
            "not authorized",
            "unsupported",
            "without evidence",
            "unplanned module",
            "FFI",
            "Rust style violation",
            "C ABI leak",
            "raw pointer",
            "Cargo.toml contains unauthorized dependency",
            "重复定义",
            "未授权",
            "未获证据",
            "未规划模块",
            "Rust 风格违规",
            "C ABI 泄漏",
        ]
        return [item for item in findings if any(marker in item for marker in fatal_markers)]

    def _repair_contextual_file(
        self,
        planned: PlannedFile,
        planned_files: Sequence[str],
        content: str,
        findings: Sequence[str],
    ) -> str:
        result = self._repair_contextual_file_partial(planned, content, findings)
        if result and result.strip():
            return result
        return self._repair_contextual_file_full(planned, planned_files, content, findings)

    def _repair_contextual_file_full(
        self,
        planned: PlannedFile,
        planned_files: Sequence[str],
        content: str,
        findings: Sequence[str],
    ) -> str:
        if self.spec_ablation_enabled:
            prompt = self._ablation_prompt_agent().repair_prompt(
                planned=planned,
                findings=findings,
                registry_summary=self._build_targeted_registry_summary(planned),
                plan_summary=self._build_targeted_plan_summary(planned),
                current_content=content,
            )
            response = self._chat_with_context_requests(
                system_prompt=self._ablation_prompt_agent().repair_system_prompt(),
                user_prompt=prompt,
                label=f"ContextualRustAgent 边界修复(整文件) {planned.path}",
                max_read_rounds=2,
            )
            repaired, _ = self._extract_done_marker(response)
            return self._extract_generated_content(repaired, code_lang="" if planned.path.lower() == "readme.md" else "rust")

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
            label=f"ContextualRustAgent 边界修复(整文件) {planned.path}",
            max_read_rounds=2,
        )
        repaired, _ = self._extract_done_marker(response)
        return self._extract_generated_content(repaired, code_lang="" if planned.path.lower() == "readme.md" else "rust")

    # ------------------------------------------------------------------
    # Partial repair: structured line-level edits
    # ------------------------------------------------------------------

    def _repair_contextual_file_partial(
        self,
        planned: PlannedFile,
        content: str,
        findings: Sequence[str],
    ) -> str:
        if not content.strip() or not findings:
            return ""
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        numbered_content = self._numbered_slice(lines, 1, total_lines, context_tag="full")

        findings_text = "\n".join(f"- {f}" for f in findings)
        path = getattr(planned, "path", "unknown")
        prompt = f"""You are fixing violations in the generated `{path}`. **Do not rewrite the entire file**; make only minimal local edits.

Violations:
{findings_text}

Current file (with line numbers):
```rust
{numbered_content}
```

Requirements:
1. Return only JSON; do not explain.
2. Only local edit modes are allowed: replace_range / delete_range / insert_before / insert_after.
3. **Do not** return the entire file. Each edit must modify only the line range that needs changing.
4. Line numbers must be based on the numbered file content above.
5. If a violation only requires deleting one line or changing a function name, edit only those lines.

Return JSON:
{{
  "summary": "One-sentence repair summary",
  "edits": [
    {{
      "mode": "replace_range",
      "start_line": 10,
      "end_line": 12,
      "content": "Replacement code snippet without line-number prefixes"
    }},
    {{
      "mode": "delete_range",
      "start_line": 50,
      "end_line": 52
    }},
    {{
      "mode": "insert_before",
      "before_line": 5,
      "content": "New code to insert"
    }}
  ]
}}
"""
        system_prompt = (
            "You are a strict local Rust file repair assistant. Make only minimal edits to correct violations; do not rewrite the entire file."
            "Return only structured edit instructions in JSON format."
        )
        response = self._read_llm(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            label=f"ContextualRustAgent 局部修复 {path}",
        )

        payload = self._extract_json_payload(response)
        if not isinstance(payload, dict):
            return ""
        edits = payload.get("edits", [])
        if not edits or not isinstance(edits, list):
            return ""

        try:
            result_lines = self._apply_partial_edits(lines, edits)
        except Exception as exc:
            print(f"局部修复编辑应用失败：{exc}，回退到整文件修复")
            return ""

        result_text = "".join(result_lines)
        opens = result_text.count("{")
        closes = result_text.count("}")
        if opens - closes >= 3 or closes - opens >= 3:
            print(f"局部修复导致大括号不平衡 ({{ {opens} vs }} {closes})，拒绝局部修复")
            return ""
        if len(result_lines) < len(lines) * 0.8:
            print(f"局部修复删除过多内容 ({len(lines)} -> {len(result_lines)} 行)，拒绝局部修复")
            return ""
        return result_text

    @staticmethod
    def _numbered_slice(lines: Sequence[str], start: int, end: int, context_tag: str = "") -> str:
        parts = []
        for i in range(max(0, start - 1), min(len(lines), end)):
            line_content = lines[i].rstrip("\n").rstrip("\r")
            parts.append(f"{i + 1:>5}\t{line_content}")
        return "\n".join(parts)

    def _apply_partial_edits(self, lines: List[str], edits: List[dict]) -> List[str]:
        result = list(lines)
        pending = [dict(e) for e in edits]
        for idx, edit in enumerate(pending):
            mode = (edit.get("mode") or "replace_range").strip()
            if mode not in {"replace_range", "delete_range", "insert_before", "insert_after"}:
                continue
            new_result, delta, record = self._apply_single_edit_to_lines(result, edit)
            result = new_result
            self._update_remaining_edits_after_apply(pending[idx + 1:], record, delta)
        return result

    @staticmethod
    def _apply_single_edit_to_lines(lines: List[str], edit: dict):
        mode = edit.get("mode") or "replace_range"
        content = edit.get("content") or ""
        record = {"mode": mode}

        if mode == "replace_range":
            start_line = int(edit.get("start_line") or 1)
            end_line = int(edit.get("end_line") or start_line)
            actual_start = max(1, start_line)
            actual_end = max(actual_start, end_line)
            s = actual_start - 1
            e = min(len(lines), actual_end)
            replacement = content
            if replacement and not replacement.endswith("\n"):
                replacement += "\n"
            new_segments = replacement.splitlines(keepends=True)
            delta = len(new_segments) - (e - s)
            new_lines = lines[:s] + new_segments + lines[e:]
            record.update({"actual_start_line": actual_start, "actual_end_line": actual_end})
            return new_lines, delta, record

        if mode == "delete_range":
            start_line = int(edit.get("start_line") or 1)
            end_line = int(edit.get("end_line") or start_line)
            actual_start = max(1, start_line)
            actual_end = max(actual_start, end_line)
            s = actual_start - 1
            e = min(len(lines), actual_end)
            delta = -(e - s)
            new_lines = lines[:s] + lines[e:]
            record.update({"actual_start_line": actual_start, "actual_end_line": actual_end})
            return new_lines, delta, record

        if mode == "insert_before":
            before_line = int(edit.get("before_line") or edit.get("start_line") or 1)
            actual_before = max(1, before_line)
            insert_at = max(0, min(len(lines), actual_before - 1))
            insertion = content
            if insertion and not insertion.endswith("\n"):
                insertion += "\n"
            insertion_lines = insertion.splitlines(keepends=True)
            new_lines = lines[:insert_at] + insertion_lines + lines[insert_at:]
            record.update({"actual_before_line": actual_before})
            return new_lines, len(insertion_lines), record

        if mode == "insert_after":
            after_line = int(edit.get("after_line") or edit.get("end_line") or edit.get("start_line") or 0)
            actual_after = max(0, after_line)
            insert_at = max(0, min(len(lines), actual_after))
            insertion = content
            if insertion and not insertion.endswith("\n"):
                insertion += "\n"
            insertion_lines = insertion.splitlines(keepends=True)
            new_lines = lines[:insert_at] + insertion_lines + lines[insert_at:]
            record.update({"actual_after_line": actual_after})
            return new_lines, len(insertion_lines), record

        raise ValueError(f"unsupported edit mode: {mode}")

    @staticmethod
    def _update_remaining_edits_after_apply(remaining_edits: List[dict], applied_record: dict, delta: int):
        mode = applied_record.get("mode") or "replace_range"
        if mode in {"replace_range", "delete_range"}:
            pivot_start = int(applied_record.get("actual_start_line", 1))
            pivot_end = int(applied_record.get("actual_end_line", pivot_start))
            for edit in remaining_edits:
                for key in ("start_line", "end_line", "before_line", "after_line"):
                    if key not in edit:
                        continue
                    try:
                        value = int(edit[key])
                    except (ValueError, TypeError):
                        continue
                    if value > pivot_end:
                        edit[key] = value + delta
                    elif pivot_start <= value <= pivot_end:
                        edit[key] = pivot_start
        elif mode == "insert_before":
            pivot = int(applied_record.get("actual_before_line", 1))
            for edit in remaining_edits:
                for key in ("start_line", "end_line", "before_line", "after_line"):
                    if key not in edit:
                        continue
                    try:
                        value = int(edit[key])
                    except (ValueError, TypeError):
                        continue
                    if value >= pivot:
                        edit[key] = value + delta
        elif mode == "insert_after":
            pivot = int(applied_record.get("actual_after_line", 0))
            for edit in remaining_edits:
                for key in ("start_line", "end_line", "before_line", "after_line"):
                    if key not in edit:
                        continue
                    try:
                        value = int(edit[key])
                    except (ValueError, TypeError):
                        continue
                    if value > pivot:
                        edit[key] = value + delta

    def _request_force_write_decision(
        self,
        planned: PlannedFile,
        planned_files: Sequence[str],
        content: str,
        findings: Sequence[str],
    ) -> Tuple[str, bool, str]:
        if self.spec_ablation_enabled:
            prompt = self._ablation_prompt_agent().force_write_prompt(
                planned=planned,
                findings=findings,
                registry_summary=self._build_targeted_registry_summary(planned),
                plan_summary=self._build_targeted_plan_summary(planned),
                current_content=content,
            )
            response = self._chat_with_context_requests(
                system_prompt=self._ablation_prompt_agent().force_write_system_prompt(),
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

    def _remove_unplanned_entry_file(self, planned_files: Sequence[str]):
        if self.continue_mode:
            return
        entry_kind = self._effective_entry_kind()
        forbidden = "src/lib.rs" if entry_kind == "main" else "src/main.rs"
        normalized_planned = {path.replace("\\", "/") for path in planned_files}
        if forbidden in normalized_planned:
            return
        file_path = os.path.join(self.project_path, *forbidden.split("/"))
        if not os.path.isfile(file_path):
            return
        try:
            os.remove(file_path)
            print(f"删除未规划入口文件：{forbidden}")
        except OSError as exc:
            print(f"删除未规划入口文件失败：{forbidden}: {exc}")

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
        if self.spec_ablation_enabled:
            self.spec_index = SpecDocumentIndex({})
            self.spec_context_agent = None
            self.rust_context_agent = RustGenerationSpecAgent(
                doc_contents={},
                source_records=self.source_records,
                translation_contract=self.translation_contract,
                config=self.config,
            )
        self._build_module_index()

        # 1. 程序化推导初始文件计划
        self.contextual_plan = self._request_contextual_plan()
        planned_files = [item.path for item in self.contextual_plan]
        self._remove_unplanned_entry_file(planned_files)

        # 2. LLM 项目结构设计
        self.project_structure = self._generate_contextual_project_structure()
        print(f"\n项目结构:\n{self.project_structure}")

        # 3. LLM 实现计划（可能会重排 contextual_plan）
        self.implementation_plan = self._generate_contextual_implementation_plan(self.project_structure)
        print(f"\n实现计划:\n{self.implementation_plan}")

        # 重排后更新 planned_files
        planned_files = [item.path for item in self.contextual_plan]
        self._remove_unplanned_entry_file(planned_files)

        self._initialize_generation_plan(
            project_structure=self.project_structure,
            implementation_plan=self.implementation_plan,
            planned_files=planned_files,
        )
        self._ensure_api_contract_loaded()
        self._load_existing_registry(planned_files)

        # 4. 逐个生成文件
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

        if self._effective_entry_kind() == "lib" and "src/lib.rs" not in planned_files:
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
        print("开始使用 ContextualRustAgent 消融路径根据 C 源码生成 Rust 项目")
        print("=" * 60)
        self.create_rust_project(project_name, output_dir)
        self.load_documents(doc_paths)
        self._filter_optional_evidence_documents()
        self.configure_source_context(c_project_path=c_project_path, source_json_path=source_json_path)
        if self.source_json_path:
            print(f"已加载源码 JSON：{self.source_json_path}")
        self.generate_code()
        print("=" * 60)
        print("ContextualRustAgent 消融路径 Rust 项目生成完成")
        print("=" * 60)
        return True
