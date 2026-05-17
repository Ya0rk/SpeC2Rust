import argparse

import fnmatch

import json

import os

import re

import shutil

import subprocess

import sys

import time

from dataclasses import dataclass

from pathlib import Path

from typing import Dict, List, Optional, Tuple



sys.path.append(str(Path(__file__).parent.parent))



from config.config import Config

from llm.model import Model

try:
    from agent.rust_structural_repair import try_deterministic_repair
except ModuleNotFoundError as exc:
    if exc.name not in {"agent", "agent.rust_structural_repair"}:
        raise
    from rust_structural_repair import try_deterministic_repair





@dataclass

class RepairRunResult:

    run_dir: str

    check_passed: bool

    test_passed: bool

    error_count: int

    output: str

    error_signature: str = ""

    frontier_metrics: Optional[Dict] = None

    round_summary: str = ""

    timed_out: bool = False





class RustRepairAgent:

    """

    独立的 Rust 项目修复 Agent。



    设计目标：

    1. 单独执行，不依赖主生成流程

    2. 每轮先复制项目，再在副本上修复，避免越跑越坏

    3. 先做本地清洗，再把真正的语义错误交给 LLM

    4. 只在结果更好时推进基线

    """



    def __init__(self, config: Optional[Config] = None, max_iterations: int = 15):

        self.config = config or Config()

        self.llm = Model(self.config)

        self.max_iterations = max_iterations

        self.iteration_timeout_seconds = 600

        self.best_result: Optional[RepairRunResult] = None

        self.c_project_path: str = ""

        self.c_docs_path: str = ""



    def _set_request_label(self, label: str):

        if hasattr(self.llm, "set_request_label"):

            self.llm.set_request_label(label)



    def _clone_project_tree(self, project_path: str, runs_root: str, iteration: int) -> str:

        runs_root_path = Path(runs_root)

        runs_root_path.mkdir(parents=True, exist_ok=True)

        run_dir = runs_root_path / f"run-{iteration:03d}"

        if run_dir.exists():

            shutil.rmtree(run_dir)

        shutil.copytree(project_path, run_dir)

        return str(run_dir)



    def _journal_path(self, run_dir: str) -> str:

        return os.path.join(run_dir, "repair_journal.jsonl")



    def _monotonic(self) -> float:

        return time.monotonic()



    def _run_command(self, command: str, cwd: str, timeout_seconds: int = 180) -> Tuple[bool, str]:

        try:

            result = subprocess.run(

                command,

                cwd=cwd,

                shell=True,

                timeout=timeout_seconds,

                stdout=subprocess.PIPE,

                stderr=subprocess.PIPE,

                text=True,

                encoding="utf-8",

                errors="ignore",

            )

        except subprocess.TimeoutExpired:

            return False, "RuntimeError: Timeout"



        output = (result.stdout or "") + (result.stderr or "")

        return result.returncode == 0, output.strip()



    def _cargo_check(self, project_dir: str) -> Tuple[bool, str]:

        return self._run_command("cargo check", project_dir, timeout_seconds=180)



    def _cargo_test(self, project_dir: str) -> Tuple[bool, str]:

        return self._run_command("cargo test", project_dir, timeout_seconds=240)



    def _cargo_build_release(self, project_dir: str) -> Tuple[bool, str]:

        return self._run_command("cargo build --release", project_dir, timeout_seconds=600)



    def _compile_success_result(

        self,

        run_dir: str,

        check_output: str,

        round_summary: str,

    ) -> RepairRunResult:

        build_success, build_output = self._cargo_build_release(run_dir)

        output = build_output if not build_success else (

            check_output + ("\n" + build_output if build_output else "")

        )

        return RepairRunResult(

            run_dir,

            True,

            build_success,

            0 if build_success else self._count_errors(output),

            output,

            self._error_signature(output),

            self._frontier_metrics(output),

            round_summary=round_summary,

            timed_out=False,

        )



    def _extract_code(self, text: str) -> str:

        content = (text or "").strip()

        if not content:

            return ""

        fence_match = re.match(r"^\s*```(?:[A-Za-z0-9_+\-]+)?\s*\n(.*)\n\s*```\s*$", content, re.DOTALL)

        if fence_match:

            return fence_match.group(1).strip()

        fence_search = re.search(r"(?ms)^\s*```(?:[A-Za-z0-9_+\-]+)?[ \t]*\n(.*?)\n\s*```", content)

        if fence_search:

            return fence_search.group(1).strip()

        return content



    def _strip_outer_code_fences(self, content: str, code_lang: str = "") -> str:

        del code_lang

        return self._extract_code(content)



    def _strip_inline_test_modules(self, content: str) -> str:

        result = content or ""

        pattern = re.compile(r"(?m)^[ \t]*#\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*$")

        while True:

            cfg_match = pattern.search(result)

            if not cfg_match:

                break

            mod_match = re.search(r"(?m)^[ \t]*mod\s+tests\s*\{", result[cfg_match.end():])

            if not mod_match:

                break

            block_start = cfg_match.start()

            mod_start = cfg_match.end() + mod_match.start()

            open_brace = result.find("{", mod_start)

            if open_brace == -1:

                break

            depth = 0

            block_end = None

            for index in range(open_brace, len(result)):

                if result[index] == "{":

                    depth += 1

                elif result[index] == "}":

                    depth -= 1

                    if depth == 0:

                        block_end = index + 1

                        break

            if block_end is None:

                break

            result = (result[:block_start].rstrip() + "\n\n" + result[block_end:].lstrip()).strip() + "\n"

        return result



    def _sanitize_file_content_before_write(self, file_path: str, content: str) -> str:

        normalized = file_path.replace("\\", "/").lower()

        sanitized = content or ""



        if normalized.endswith(".rs"):

            sanitized = self._strip_outer_code_fences(sanitized, "rust")

            if sanitized.lstrip().startswith("```"):

                sanitized = self._extract_code(sanitized)

            sanitized = re.sub(r"(?m)^[ \t]*use\s+thiserror::Error;\s*\n?", "", sanitized)

            sanitized = re.sub(r"(?m)^[ \t]*#\[\s*error\([^\]]*\)\s*\]\s*\n?", "", sanitized)

            if not self.config.generate_tests:

                sanitized = self._strip_inline_test_modules(sanitized)

        elif normalized.endswith("cargo.toml"):

            sanitized = self._strip_outer_code_fences(sanitized, "toml")

        elif normalized.endswith("readme.md"):

            sanitized = self._strip_outer_code_fences(sanitized, "")



        return sanitized.strip() + ("\n" if sanitized.strip() else "")



    def _sanitize_project_locally(self, project_dir: str):

        for root, _, files in os.walk(project_dir):

            for name in files:

                full_path = os.path.join(root, name)

                normalized = full_path.replace("\\", "/").lower()

                if not (normalized.endswith(".rs") or normalized.endswith("cargo.toml") or normalized.endswith("readme.md")):

                    continue

                try:

                    with open(full_path, "r", encoding="utf-8") as f:

                        original = f.read()

                    sanitized = self._sanitize_file_content_before_write(full_path, original)

                    if sanitized != original and sanitized.strip():

                        with open(full_path, "w", encoding="utf-8") as f:

                            f.write(sanitized)

                except Exception as e:

                    print(f"本地清洗失败：{full_path}，原因：{e}")

        self._apply_deterministic_bracket_repair(project_dir)

    def _apply_deterministic_bracket_repair(self, project_dir: str) -> None:
        """对项目下所有 .rs 文件运行 Layer 1 确定性括号修复。
        仅在文件不平衡且修复安全时改写文件，否则不做任何动作。"""
        for root, _, files in os.walk(project_dir):
            rel = os.path.relpath(root, project_dir).replace("\\", "/")
            if rel.startswith("target") or "/target/" in f"/{rel}/":
                continue
            for name in files:
                if not name.endswith(".rs"):
                    continue
                full_path = os.path.join(root, name)
                try:
                    outcome = try_deterministic_repair(full_path)
                except Exception as exc:  # noqa: BLE001
                    print(f"结构化修复异常 {full_path}: {exc}")
                    continue
                if outcome.changed:
                    print(
                        f"结构化修复 {os.path.relpath(full_path, project_dir).replace(os.sep, '/')}: "
                        f"{outcome.description} "
                        f"(imbalance {outcome.pre_imbalance} -> {outcome.post_imbalance})"
                    )
                    for detail in outcome.details:
                        print(f"  · {detail}")



    def _extract_public_exportable_items(self, content: str) -> List[str]:

        patterns = [

            re.compile(r"(?m)^\s*pub\s+struct\s+([A-Z][A-Za-z0-9_]*)\b"),

            re.compile(r"(?m)^\s*pub\s+enum\s+([A-Z][A-Za-z0-9_]*)\b"),

            re.compile(r"(?m)^\s*pub\s+trait\s+([A-Z][A-Za-z0-9_]*)\b"),

            re.compile(r"(?m)^\s*pub\s+type\s+([A-Z][A-Za-z0-9_]*)\b"),

        ]

        items: List[str] = []

        for pattern in patterns:

            items.extend(pattern.findall(content or ""))

        return items



    def _rebuild_minimal_lib_rs(self, project_dir: str) -> str:

        src_dir = os.path.join(project_dir, "src")

        module_files = []

        for name in os.listdir(src_dir):

            if not name.endswith(".rs") or name == "lib.rs":

                continue

            module_files.append(name[:-3])

        module_files = sorted(module_files)



        lines = ["//! 自动重建的 crate 入口。", ""]

        export_candidates: Dict[str, List[str]] = {}



        for module in module_files:

            lines.append(f"pub mod {module};")

            module_path = os.path.join(src_dir, f"{module}.rs")

            try:

                with open(module_path, "r", encoding="utf-8") as f:

                    module_content = f.read()

            except Exception:

                module_content = ""

            for item in self._extract_public_exportable_items(module_content):

                export_candidates.setdefault(item, []).append(module)



        lines.append("")

        for item in sorted(export_candidates):

            modules = export_candidates[item]

            if len(modules) == 1:

                lines.append(f"pub use {modules[0]}::{item};")



        return "\n".join(lines).rstrip() + "\n"



    def _maybe_rebuild_lib_rs(self, project_dir: str, error_output: str) -> bool:

        del project_dir, error_output

        # 禁止本地 fallback 修补。crate 入口问题必须交给 LLM 在读取足够上下文后做显式编辑。
        return False



    def _count_errors(self, output: str) -> int:

        text = output or ""

        count = len(re.findall(r"(?m)^error(?:\[[A-Z0-9]+\])?:", text))

        return count if count > 0 else (1 if text else 0)



    def _error_signature(self, output: str) -> str:

        text = (output or "").strip()

        if not text:

            return ""

        lines = []

        for line in text.splitlines():

            if line.startswith("error") or "-->" in line or "could not compile" in line:

                lines.append(line.strip())

        if not lines:

            lines = text.splitlines()[:20]

        normalized = "\n".join(lines[:40])

        return str(abs(hash(normalized)))



    def _error_excerpt(self, output: str, max_lines: int = 40) -> str:

        text = (output or "").strip()

        if not text:

            return ""

        return "\n".join(text.splitlines()[:max_lines])



    def _frontier_metrics(self, output: str) -> Dict:

        text = output or ""

        lowered = text.lower()

        syntax_patterns = [

            "unknown start of token",

            "unclosed delimiter",

            "expected one of",

            "unexpected token",

            "mismatched closing delimiter",

            "this file contains an unclosed delimiter",

        ]

        interface_patterns = [

            "unresolved import",

            "cannot find",

            "no method named",

            "no function or associated item named",

            "private field",

            "attempted to take value of method",

            "no variant or associated item named",

        ]

        syntax_blockers = sum(1 for p in syntax_patterns if p in lowered)

        interface_blockers = sum(1 for p in interface_patterns if p in lowered)

        return {

            "syntax_blockers": syntax_blockers,

            "interface_blockers": interface_blockers,

            "total_errors": self._count_errors(output),

            "signature": self._error_signature(output),

        }



    def _should_accept_result(self, current_best: Optional[RepairRunResult], candidate: RepairRunResult) -> Tuple[bool, str]:

        if current_best is None:

            return True, "首次结果"

        if candidate.check_passed and not current_best.check_passed:

            return True, "编译已通过"

        if (

            candidate.check_passed

            and candidate.test_passed

            and not current_best.test_passed

        ):

            return True, "release 编译已通过"



        current_metrics = current_best.frontier_metrics or self._frontier_metrics(current_best.output)

        candidate_metrics = candidate.frontier_metrics or self._frontier_metrics(candidate.output)



        if candidate_metrics["syntax_blockers"] < current_metrics["syntax_blockers"]:

            return True, "语法阻塞错误减少"

        if current_metrics["syntax_blockers"] > 0 and candidate_metrics["syntax_blockers"] == 0:

            return True, "语法阻塞错误被清空"

        if (

            candidate_metrics["syntax_blockers"] == current_metrics["syntax_blockers"] == 0

            and candidate_metrics["interface_blockers"] < current_metrics["interface_blockers"]

        ):

            return True, "接口阻塞错误减少"

        if (

            candidate_metrics["syntax_blockers"] == 0

            and current_metrics["syntax_blockers"] > 0

        ):

            return True, "暴露出更深层错误，接受为新前沿"

        if (

            candidate_metrics["syntax_blockers"] == current_metrics["syntax_blockers"] == 0

            and candidate_metrics["signature"] != current_metrics["signature"]

            and candidate_metrics["total_errors"] <= current_metrics["total_errors"] + 80

        ):

            return True, "错误签名发生变化，接受为新前沿"

        if candidate.error_count < current_best.error_count:

            return True, "总错误数减少"

        return False, "未推进编译前沿"



    def _group_rust_errors_by_file(self, output: str) -> Dict[str, str]:

        grouped: Dict[str, List[str]] = {}

        current_block: List[str] = []

        current_file: Optional[str] = None



        def flush():

            nonlocal current_block, current_file

            if current_block and current_file:

                grouped.setdefault(current_file, []).append("\n".join(current_block).strip())

            current_block = []

            current_file = None



        for line in (output or "").splitlines():

            if line.startswith("error"):

                flush()

                current_block = [line]

                current_file = None

                continue



            if current_block:

                current_block.append(line)

                location_match = re.search(r"-->\s+([^\s:]+(?:\\|/)[^\s:]+):\d+:\d+", line)

                if location_match:

                    current_file = location_match.group(1).replace("\\", "/")



        flush()

        return {path: "\n\n".join(blocks) for path, blocks in grouped.items()}



    def _choose_target_files(self, grouped_errors: Dict[str, str], limit: int = 2) -> List[str]:

        if not grouped_errors:

            return []



        def score(item: Tuple[str, str]) -> Tuple[int, int, str]:

            path, block = item

            normalized = path.lower()

            priority = 5

            if normalized == "src/lib.rs":

                priority = 0

            elif normalized.endswith("avl_bf.rs"):

                priority = 1

            elif normalized.startswith("src/"):

                priority = 2

            return (priority, -len(block), normalized)



        return [path for path, _ in sorted(grouped_errors.items(), key=score)[:limit]]



    def _read_file(self, path: str) -> str:

        with open(path, "r", encoding="utf-8") as f:

            return f.read()



    def _write_file(self, path: str, content: str):

        sanitized = self._sanitize_file_content_before_write(path, content)

        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:

            f.write(sanitized)



    def _resolve_project_path(self, project_dir: str, rel_path: str) -> Tuple[Optional[str], Optional[str]]:

        normalized = (rel_path or "").strip().strip('"').strip("'").replace("\\", "/")

        normalized = re.sub(r"/+", "/", normalized).lstrip("/")

        if not normalized or normalized in {".", ".."}:

            return None, None

        if re.match(r"^[A-Za-z]:", normalized) or normalized.startswith("../") or "/../" in normalized:

            return None, None

        if normalized.startswith((".git/", "target/")) or "/.git/" in f"/{normalized}/" or "/target/" in f"/{normalized}/":

            return None, None

        root = os.path.abspath(project_dir)

        full_path = os.path.abspath(os.path.join(root, normalized.replace("/", os.sep)))

        try:

            if os.path.commonpath([root, full_path]) != root:

                return None, None

        except ValueError:

            return None, None

        return full_path, normalized



    def configure_context_sources(self, c_project_path: str = "", c_docs_path: str = "") -> None:

        self.c_project_path = str(Path(c_project_path).resolve()) if c_project_path and os.path.isdir(c_project_path) else ""

        self.c_docs_path = str(Path(c_docs_path).resolve()) if c_docs_path and os.path.isdir(c_docs_path) else ""



    def _context_roots(self, project_dir: str) -> Dict[str, str]:

        roots = {"rust": str(Path(project_dir).resolve())}

        if self.c_project_path:

            roots["c"] = self.c_project_path

        if self.c_docs_path:

            roots["spec"] = self.c_docs_path

        return roots



    def _parse_context_ref(self, request: Dict) -> Tuple[str, str]:

        kind = str(request.get("kind") or request.get("scope") or "rust").strip().lower()

        path = str(request.get("path") or "").strip().replace("\\", "/")

        prefix_match = re.match(r"^(rust|c|spec):(.*)$", path, re.IGNORECASE)

        if prefix_match:

            kind = prefix_match.group(1).lower()

            path = prefix_match.group(2).strip()

        if kind not in {"rust", "c", "spec"}:

            kind = "rust"

        return kind, path



    def _resolve_context_path(self, project_dir: str, kind: str, rel_path: str) -> Tuple[Optional[str], Optional[str], str]:

        roots = self._context_roots(project_dir)

        root = roots.get(kind)

        if not root:

            return None, None, kind

        normalized = (rel_path or "").strip().strip('"').strip("'").replace("\\", "/")

        normalized = re.sub(r"/+", "/", normalized).lstrip("/")

        if not normalized or normalized in {".", ".."}:

            return None, None, kind

        if re.match(r"^[A-Za-z]:", normalized) or normalized.startswith("../") or "/../" in normalized:

            return None, None, kind

        if normalized.startswith((".git/", "target/")) or "/.git/" in f"/{normalized}/" or "/target/" in f"/{normalized}/":

            return None, None, kind

        root_abs = os.path.abspath(root)

        full_path = os.path.abspath(os.path.join(root_abs, normalized.replace("/", os.sep)))

        try:

            if os.path.commonpath([root_abs, full_path]) != root_abs:

                return None, None, kind

        except ValueError:

            return None, None, kind

        return full_path, normalized, kind



    def _read_context_file_slice(

        self,

        project_dir: str,

        kind: str,

        rel_path: str,

        start_line: Optional[int] = None,

        end_line: Optional[int] = None,

    ) -> str:

        full_path, _, _ = self._resolve_context_path(project_dir, kind, rel_path)

        if not full_path or not os.path.isfile(full_path):

            return ""

        text = self._read_file(full_path)

        if start_line is None or end_line is None:

            return text

        lines = text.splitlines()

        start = max(1, start_line)

        end = min(len(lines), end_line)

        if end < start:

            return ""

        return "\n".join(lines[start - 1:end]) + ("\n" if end >= start else "")



    def _allowed_context_file(self, rel_path: str) -> bool:

        lowered = (rel_path or "").replace("\\", "/").lower()

        if lowered.startswith((".git/", "target/")) or "/.git/" in f"/{lowered}/" or "/target/" in f"/{lowered}/":

            return False

        return lowered.endswith((".rs", ".toml", ".md", ".json", ".c", ".h", ".sh", ".txt", ".in", ".out"))



    def _is_allowed_created_file(self, rel_path: str) -> bool:

        normalized = (rel_path or "").replace("\\", "/").lower()

        base = os.path.basename(normalized)

        if normalized.startswith((".git/", "target/")) or "/.git/" in f"/{normalized}/" or "/target/" in f"/{normalized}/":

            return False

        if base in {"cargo.toml", "build.rs", "readme.md"}:

            return True

        return normalized.endswith((".rs", ".toml", ".md"))



    def _looks_like_compile_only_stub(self, rel_path: str, content: str) -> bool:

        normalized = (rel_path or "").replace("\\", "/").lower()

        if not normalized.endswith(".rs"):

            return False

        text = content or ""

        stripped = text.strip()

        if not stripped:

            return True

        lowered = stripped.lower()

        if any(marker in lowered for marker in ["todo!(", "unimplemented!(", "panic!(\"todo", "panic!(\"not implemented"]):

            return True

        nonempty_lines = [line.strip() for line in stripped.splitlines() if line.strip()]

        function_count = len(re.findall(r"\b(?:pub\s+)?fn\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", stripped))

        default_return_patterns = [

            r"->\s*i(?:8|16|32|64|size)\s*\{[^{};]*\b0\s*\}",

            r"->\s*u(?:8|16|32|64|size)\s*\{[^{};]*\b0\s*\}",

            r"->\s*bool\s*\{[^{};]*\bfalse\s*\}",

            r"->\s*String\s*\{[^{};]*String::new\(\)\s*\}",

            r"->\s*Vec\s*<[^>]+>\s*\{[^{};]*Vec::new\(\)\s*\}",

            r"->\s*Option\s*<[^>]+>\s*\{[^{};]*None\s*\}",

            r"->\s*Result\s*<[^>]+>\s*\{[^{};]*Ok\s*\([^)]*\)\s*\}",

        ]

        default_return_count = sum(len(re.findall(pattern, stripped, flags=re.DOTALL)) for pattern in default_return_patterns)

        ignored_args = len(re.findall(r"\b_[A-Za-z][A-Za-z0-9_]*\b", stripped))

        if function_count > 0 and default_return_count >= function_count and len(nonempty_lines) <= 20:

            return True

        if ignored_args > 0 and default_return_count > 0 and len(nonempty_lines) <= 24:

            return True

        if re.search(r"pub\s+struct\s+\w+\s*;\s*impl\s+\w+\s*\{[^{}]*pub\s+fn\s+main\s*\([^)]*\)[^{]*\{[^{}]*\b0\s*\}[^{}]*\}", stripped, re.DOTALL):

            return True

        return False



    def _is_destructive_existing_file_edit(self, rel_path: str, before_lines: List[str], after_lines: List[str]) -> Tuple[bool, str]:

        normalized = (rel_path or "").replace("\\", "/").lower()

        if not normalized.endswith((".rs", ".toml", ".md")):

            return False, ""

        before_text = "".join(before_lines)

        after_text = "".join(after_lines)

        before_non_ws = len(re.sub(r"\s+", "", before_text))

        after_non_ws = len(re.sub(r"\s+", "", after_text))

        before_line_count = len([line for line in before_lines if line.strip()])

        after_line_count = len([line for line in after_lines if line.strip()])

        if before_non_ws < 600 and before_line_count < 30:

            return False, ""

        if after_non_ws < 120:

            return True, f"edit reduces non-whitespace content to {after_non_ws} chars"

        if after_non_ws < before_non_ws * 0.35:

            return True, f"edit shrinks file content too much: {before_non_ws} -> {after_non_ws} non-whitespace chars"

        if before_line_count >= 60 and after_line_count < before_line_count * 0.35:

            return True, f"edit removes too many non-empty lines: {before_line_count} -> {after_line_count}"

        if normalized.endswith(".rs") and self._looks_like_compile_only_stub(rel_path, after_text):

            return True, "edit turns existing Rust file into a compile-only stub"

        return False, ""



    def _append_repair_record(self, journal_path: str, record: Dict):

        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)

        with open(journal_path, "a", encoding="utf-8") as f:

            f.write(json.dumps(record, ensure_ascii=False) + "\n")



    def _read_file_slice(self, project_dir: str, rel_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:

        full_path = os.path.join(project_dir, rel_path.replace("/", os.sep))

        if not os.path.exists(full_path):

            return ""

        text = self._read_file(full_path)

        if start_line is None or end_line is None:

            return text

        lines = text.splitlines()

        start = max(1, start_line)

        end = min(len(lines), end_line)

        if end < start:

            return ""

        return "\n".join(lines[start - 1:end]) + ("\n" if end >= start else "")



    def _format_material_with_line_numbers(self, content: str, base_line: Optional[int] = None) -> str:

        lines = (content or "").splitlines()

        if not lines:

            return ""

        start = base_line if isinstance(base_line, int) and base_line > 0 else 1

        end_line = start + len(lines) - 1

        width = max(4, len(str(end_line)))

        return "\n".join(

            f"{line_no:>{width}} | {line}"

            for line_no, line in enumerate(lines, start=start)

        )



    def _build_project_overview(self, project_dir: str, max_files: int = 20) -> str:

        src_dir = os.path.join(project_dir, "src")

        entries = ["Rust 项目文件："]

        if os.path.isdir(src_dir):

            for name in sorted(os.listdir(src_dir)):

                if not name.endswith(".rs"):

                    continue

                full = os.path.join(src_dir, name)

                try:

                    size = os.path.getsize(full)

                    head = self._read_file_slice(project_dir, f"src/{name}", 1, 8).strip().splitlines()

                    headline = head[0].strip() if head else ""

                except Exception:

                    size = 0

                    headline = ""

                entries.append(f"- rust:src/{name} ({size} bytes) {headline}")

        if os.path.exists(os.path.join(project_dir, "Cargo.toml")):

            entries.insert(1, "- rust:Cargo.toml")

        if os.path.exists(os.path.join(project_dir, "src", "lib.rs")):

            entries.insert(2, "- rust:src/lib.rs")

        if self.c_project_path:

            entries.append("")

            entries.append("可读取的 C 源码上下文（kind=c）：")

            for rel_path in self._list_context_files(project_dir, "c", limit=40):

                entries.append(f"- c:{rel_path}")

        if self.c_docs_path:

            entries.append("")

            entries.append("可读取的 spec/c_docs 上下文（kind=spec）：")

            for rel_path in self._list_context_files(project_dir, "spec", limit=60):

                entries.append(f"- spec:{rel_path}")

        return "\n".join(entries).strip()



    def _list_context_files(self, project_dir: str, kind: str, limit: int = 80) -> List[str]:

        root = self._context_roots(project_dir).get(kind)

        if not root or not os.path.isdir(root):

            return []

        rel_paths: List[str] = []

        for current_root, dirs, files in os.walk(root):

            dirs[:] = [

                name for name in dirs

                if name not in {".git", "target", "__pycache__", ".pytest_cache"}

            ]

            for name in files:

                rel_path = os.path.relpath(os.path.join(current_root, name), root).replace("\\", "/")

                if not self._allowed_context_file(rel_path):

                    continue

                rel_paths.append(rel_path)

                if len(rel_paths) >= limit:

                    return sorted(rel_paths)

        return sorted(rel_paths)



    def _extract_json_payload(self, text: str):

        content = self._extract_code(text or "")

        if not content:

            return None

        content = content.strip()

        try:

            return json.loads(content)

        except Exception:

            pass

        object_match = re.search(r"(?s)\{.*\}", content)

        if object_match:

            try:

                return json.loads(object_match.group(0))

            except Exception:

                pass

        array_match = re.search(r"(?s)\[.*\]", content)

        if array_match:

            try:

                return json.loads(array_match.group(0))

            except Exception:

                pass

        return None



    def _build_diagnosis_prompt(self, grouped_errors: Dict[str, str], project_overview: str, handoff_summary: str = "") -> str:

        error_sections = []

        error_items = list(grouped_errors.items())

        shown_items = error_items[:6]

        omitted_count = max(0, len(error_items) - len(shown_items))

        for path, block in shown_items:

            error_sections.append(f"### {path}\n```text\n{block}\n```")

        omission_note = ""

        if omitted_count > 0:

            omission_note = f"\n\n注意：当前只展示了 {len(shown_items)} 个错误块，另外还有 {omitted_count} 个错误块未展开。请先聚焦当前展示的核心错误。"

        joined_errors = "\n\n".join(error_sections) + omission_note

        handoff_block = ""

        if handoff_summary.strip():

            handoff_block = f"""

上一轮经验摘要：

```text

{handoff_summary}

```

"""

        return f"""你在做 Rust 编译修复诊断。先不要直接输出代码。



你只能先产出一个 JSON 诊断计划，告诉程序：

1. 本轮优先修哪些文件

2. 还需要读取哪些文件或文件片段

3. 还需要用哪些关键词做本地搜索

4. 为什么这么读/搜

5. 本轮预期采用哪些局部编辑动作；如果错误来自缺失模块/文件，必须先计划读取 C/spec/Rust 证据，再决定是否创建真实文件



可用读取接口：

- whole_file: 读取整个文件。字段：kind=rust/c/spec，path，mode=whole_file

- line_range: 读取文件的 start_line 到 end_line。字段：kind=rust/c/spec，path，mode=line_range，start_line，end_line



可用搜索接口：

- search_requests: 提供 kind=rust/c/spec/all、query 和可选 path_glob，程序会返回命中片段和文件位置，方便你再决定是否读取更大上下文



关键约束：

- 禁止规划 fallback、最小 stub、空行为实现、默认返回值实现。

- 缺失核心业务模块时，read_requests/search_requests 必须覆盖相关 C 源码或 spec 文档；不能只读 main.rs/Cargo.toml 就创建业务模块。

- 如果不知道对应 C/spec 文件路径，先用 search_requests 在 kind=all 中搜索模块名、函数名、类型名。



项目概览：

```text

{project_overview}

```

{handoff_block}



当前错误：

{joined_errors}



只返回 JSON，对象格式：

{{

  "summary": "一句话诊断",

  "target_files": ["src/a.rs"],

  "read_requests": [

    {{"kind": "rust", "path": "src/a.rs", "mode": "whole_file"}},

    {{"kind": "c", "path": "which.c", "mode": "line_range", "start_line": 1, "end_line": 160}},

    {{"kind": "spec", "path": "docs/rewrite-context/00_repo_manifest.md", "mode": "whole_file"}}

  ],

  "search_requests": [

    {{"kind": "all", "query": "rotate_right", "path_glob": "**/*", "context_lines": 2, "max_results": 6}}

  ],

  "edit_strategy": "replace_range / delete_range / insert_before / insert_after / create_file / create_dir 的总体策略",

  "reasoning": ["简短要点1", "简短要点2"]

}}

"""



    def _request_diagnosis_plan(self, grouped_errors: Dict[str, str], project_overview: str, handoff_summary: str = "") -> Dict:

        prompt = self._build_diagnosis_prompt(grouped_errors, project_overview, handoff_summary)

        self._set_request_label("修复诊断计划")

        response = self.llm.generate([

            {"role": "system", "content": "你是经验丰富的 Rust 编译修复规划助手。"},

            {"role": "user", "content": prompt},

        ])

        parsed = self._extract_json_payload(response[0] if isinstance(response, list) else response)

        if isinstance(parsed, dict):

            parsed.setdefault("search_requests", [])

            return parsed

        return {

            "summary": "诊断计划解析失败；不执行 fallback 修补，仅读取错误文件并要求下一轮重新诊断",

            "target_files": self._choose_target_files(grouped_errors, limit=2),

            "read_requests": [{"kind": "rust", "path": p, "mode": "whole_file"} for p in self._choose_target_files(grouped_errors, limit=2)],

            "search_requests": [],

            "edit_strategy": "no fallback edits; collect context only",

            "reasoning": ["LLM 诊断计划解析失败，不生成修补代码，先收集错误文件上下文"],

        }



    def _materialize_read_requests(self, project_dir: str, read_requests: List[Dict], max_chars: int = 24000) -> List[Dict]:

        materials: List[Dict] = []

        total = 0

        seen = set()

        for request in read_requests:

            kind, path = self._parse_context_ref(request)

            if not path:

                continue

            mode = request.get("mode") or "whole_file"

            start_line = request.get("start_line")

            end_line = request.get("end_line")

            key = (kind, path, mode, start_line, end_line)

            if key in seen:

                continue

            seen.add(key)

            if mode == "line_range" and isinstance(start_line, int) and isinstance(end_line, int):

                content = self._read_context_file_slice(project_dir, kind, path, start_line, end_line)

            else:

                content = self._read_context_file_slice(project_dir, kind, path)

                start_line = None

                end_line = None

                mode = "whole_file"

            if not content:

                continue

            remain = max_chars - total

            if remain <= 0:

                break

            if len(content) > remain:

                content = content[:remain]

            materials.append({

                "path": path,

                "kind": kind,

                "mode": mode,

                "start_line": start_line,

                "end_line": end_line,

                "content": content,

            })

            total += len(content)

        return materials



    def _refresh_materials_for_edited_files(self, project_dir: str, materials: List[Dict], edited_paths: set) -> None:

        """编辑应用后，刷新 materials 中被修改文件的内容，避免 LLM 看到过时的代码。

        line_range 条目会被升级为 whole_file，因为行号在编辑后可能已偏移。"""

        if not edited_paths:

            return

        refreshed_paths = set()

        for i, mat in enumerate(materials):

            path = mat.get("path", "")

            if mat.get("kind", "rust") != "rust" or path not in edited_paths:

                continue

            if mat.get("mode") == "search_results":

                continue

            if path in refreshed_paths:

                materials[i]["content"] = ""

                continue

            new_content = self._read_context_file_slice(project_dir, "rust", path)

            if new_content:

                materials[i]["content"] = new_content

                materials[i]["mode"] = "whole_file"

                materials[i]["start_line"] = None

                materials[i]["end_line"] = None

                refreshed_paths.add(path)

        materials[:] = [m for m in materials if m.get("content")]



    def _path_matches_glob(self, rel_path: str, path_glob: str) -> bool:

        if not path_glob:

            return True

        normalized = rel_path.replace("\\", "/")

        glob = path_glob.replace("\\", "/")

        candidates = [glob]

        if glob.startswith("**/"):

            candidates.append(glob[3:])

        return any(fnmatch.fnmatch(normalized, item) or Path(normalized).match(item) for item in candidates)



    def _iter_searchable_files(self, project_dir: str, path_glob: str = "", kind: str = "rust") -> List[str]:

        candidates: List[str] = []

        normalized_glob = (path_glob or "").replace("\\", "/").strip()

        root_dir = self._context_roots(project_dir).get(kind)

        if not root_dir or not os.path.isdir(root_dir):

            return []

        for root, dirs, files in os.walk(root_dir):

            dirs[:] = [

                name for name in dirs

                if name not in {".git", "target", "__pycache__", ".pytest_cache"}

            ]

            for name in files:

                rel_path = os.path.relpath(os.path.join(root, name), root_dir).replace("\\", "/")

                lowered = rel_path.lower()

                if kind == "rust" and lowered.startswith(".cgr_"):

                    continue

                if not self._allowed_context_file(lowered):

                    continue

                if normalized_glob and not self._path_matches_glob(rel_path, normalized_glob):

                    continue

                candidates.append(rel_path)

        return sorted(candidates)



    def _materialize_search_requests(self, project_dir: str, search_requests: List[Dict], max_chars: int = 12000) -> List[Dict]:

        materials: List[Dict] = []

        total = 0

        seen = set()

        for request in search_requests or []:

            query = (request.get("query") or "").strip()

            if not query:

                continue

            kind = str(request.get("kind") or request.get("scope") or "rust").strip().lower()

            if kind not in {"rust", "c", "spec", "all"}:

                kind = "rust"

            path_glob = (request.get("path_glob") or "").replace("\\", "/").strip()

            try:

                context_lines = int(request.get("context_lines", 2))

            except Exception:

                context_lines = 2

            try:

                max_results = int(request.get("max_results", 8))

            except Exception:

                max_results = 8

            context_lines = max(0, min(context_lines, 10))

            max_results = max(1, min(max_results, 20))

            key = (kind, query, path_glob, context_lines, max_results)

            if key in seen:

                continue

            seen.add(key)



            hits: List[str] = []

            query_lower = query.lower()

            search_kinds = ["rust", "c", "spec"] if kind == "all" else [kind]

            for search_kind in search_kinds:

                kind_hits = 0

                for rel_path in self._iter_searchable_files(project_dir, path_glob, kind=search_kind):

                    content = self._read_context_file_slice(project_dir, search_kind, rel_path)

                    if not content:

                        continue

                    lines = content.splitlines()

                    for index, line in enumerate(lines, start=1):

                        if query_lower not in line.lower():

                            continue

                        start = max(1, index - context_lines)

                        end = min(len(lines), index + context_lines)

                        excerpt_lines = []

                        for line_no in range(start, end + 1):

                            prefix = ">" if line_no == index else " "

                            excerpt_lines.append(f"{prefix}{search_kind}:{rel_path}:{line_no}: {lines[line_no - 1]}")

                        hits.append("\n".join(excerpt_lines))

                        kind_hits += 1

                        if kind_hits >= max_results:

                            break

                    if kind_hits >= max_results:

                        break

            if not hits:

                continue



            block = f"# search query: {query}\n"

            if path_glob:

                block += f"# path glob: {path_glob}\n"

            block += f"# kind: {kind}\n"

            block += "\n" + "\n\n".join(hits) + "\n"

            remain = max_chars - total

            if remain <= 0:

                break

            if len(block) > remain:

                block = block[:remain]

            materials.append({

                "path": f"{kind}:{path_glob or '<search>'}",

                "kind": kind,

                "mode": "search_results",

                "start_line": None,

                "end_line": None,

                "content": block,

                "query": query,

            })

            total += len(block)

        return materials



    def _build_repair_tool_protocol(self, project_dir: str) -> str:

        roots = self._context_roots(project_dir)

        lines = [

            "可用上下文与工具协议：",

            "- more_read_requests：读取文件全文或行范围。字段：kind=rust/c/spec，path，mode=whole_file/line_range，start_line，end_line。",

            "- search_requests：在文件中搜索关键词。字段：kind=rust/c/spec/all，query，path_glob，context_lines，max_results。",

            "- edits：只在证据足够时修改 Rust 项目。mode 可为 replace_range/delete_range/insert_before/insert_after/create_file/create_dir。",

            "- kind=rust 表示当前 Rust 项目，可读写；kind=c 表示原始 C 项目，只读；kind=spec 表示 c_docs/spec 文档，只读。",

            "- 允许读取的上下文源：" + ", ".join(f"{kind}={path}" for kind, path in roots.items()),

            "- 如果错误涉及缺失业务模块、空文件、行为未知、接口未知，必须先读取 c/spec/rust 证据；不要直接生成最小可编译实现。",

            "- 如果当前已读材料不足以完成真实修复，返回空 edits，并通过 more_read_requests/search_requests 继续取证。",

            "",

            "读取请求示例：",

            '{"kind":"rust","path":"src/main.rs","mode":"whole_file"}',

            '{"kind":"c","path":"which.c","mode":"line_range","start_line":1,"end_line":160}',

            '{"kind":"spec","path":"docs/rewrite-context/00_repo_manifest.md","mode":"whole_file"}',

            "",

            "搜索请求示例：",

            '{"kind":"rust","query":"struct Which","path_glob":"src/*.rs","context_lines":2,"max_results":10}',

            '{"kind":"c","query":"main","path_glob":"**/*.c","context_lines":4,"max_results":10}',

            '{"kind":"spec","query":"Which","path_glob":"**/*.md","context_lines":3,"max_results":10}',

        ]

        return "\n".join(lines)



    def _format_material_inventory(self, materials: List[Dict]) -> str:

        if not materials:

            return "- 当前没有可用材料；必须先通过 more_read_requests/search_requests 获取上下文。"

        lines = []

        for material in materials:

            kind = material.get("kind", "rust")

            path = material.get("path", "")

            mode = material.get("mode", "")

            suffix = ""

            if mode == "line_range":

                suffix = f":{material.get('start_line')}-{material.get('end_line')}"

            if mode == "search_results":

                suffix = f" search={material.get('query', '')}"

            lines.append(f"- {kind}:{path}{suffix} ({len(material.get('content', ''))} chars)")

        return "\n".join(lines)



    def _build_edit_prompt(self, project_dir: str, diagnosis_plan: Dict, grouped_errors: Dict[str, str], materials: List[Dict], cycle_index: int, current_summary: str = "", handoff_summary: str = "") -> str:

        material_blocks = []

        for material in materials:

            kind = material.get("kind", "rust")

            location = f"{kind}:{material['path']}"

            if material["mode"] == "line_range":

                location += f":{material['start_line']}-{material['end_line']}"

            if material["mode"] == "search_results":

                search_info = f"### {location}"

                if material.get("query"):

                    search_info += f" (search: {material['query']})"

                material_blocks.append(f"{search_info}\n```text\n{material['content']}\n```")

            else:

                numbered = self._format_material_with_line_numbers(

                    material.get("content", ""),

                    material.get("start_line"),

                )

                material_blocks.append(

                    f"### {location}\n"

                    "下面代码块左侧 `NNNN |` 是真实文件行号；edit 的 start_line/end_line 必须使用这些行号，"

                    "不要把行号前缀写进 content。\n"

                    f"```text\n{numbered}\n```"

                )

        error_sections = []

        shown_paths = []

        for path in diagnosis_plan.get("target_files", []):

            if path in grouped_errors and path not in shown_paths:

                shown_paths.append(path)

        for path in grouped_errors:

            if path not in shown_paths:

                shown_paths.append(path)

        shown_paths = shown_paths[:6]

        omitted_count = max(0, len(grouped_errors) - len(shown_paths))

        for path in shown_paths:

            error_sections.append(f"### {path}\n```text\n{grouped_errors[path]}\n```")

        error_note = ""

        if omitted_count > 0:

            error_note = f"\n注意：当前只展示了 {len(shown_paths)} 个错误块，另外还有 {omitted_count} 个错误块未展开。"



        severely_damaged_files = []

        for path, err_text in grouped_errors.items():

            delimiter_errors = sum(1 for phrase in ["unclosed delimiter", "mismatched closing delimiter", "unexpected closing delimiter"] if phrase in err_text)

            if delimiter_errors >= 2:

                severely_damaged_files.append(path)

        if severely_damaged_files:

            damaged_list = ", ".join(severely_damaged_files)

            error_note += f"\n\n重要提示：以下文件存在严重的结构性括号损坏（多个 unclosed/mismatched delimiter），局部小范围编辑可能无法修复。对这些文件，你可以使用一次性的大范围 replace_range 覆盖整个函数/impl 块甚至整个文件（无行数上限），以彻底重建其结构：{damaged_list}"

        summary_block = ""

        if current_summary.strip():

            summary_block += f"""

当前轮已知摘要：

```text

{current_summary}

```

"""

        if handoff_summary.strip():

            summary_block += f"""

跨轮交接摘要：

```text

{handoff_summary}

```

"""

        tool_protocol = self._build_repair_tool_protocol(project_dir)

        material_inventory = self._format_material_inventory(materials)

        return f"""你现在开始真正生成修复方案。



要求：

1. 只返回 JSON，不要解释。

2. 允许的编辑 mode：replace_range / delete_range / insert_before / insert_after / create_file / create_dir。

3. 不允许 replace_file。只有创建新文件时才允许在 create_file.content 中返回完整文件内容；create_file 默认不覆盖已有文件，确需覆盖时必须显式设置 "overwrite": true。

4. 不要修改未读取的已有文件；但如果编译错误明确说明缺失模块/文件，可以使用 create_file/create_dir 创建新路径。

5. 返回前请确保行号是基于已读取文件的真实行号。

   已读取材料使用 `NNNN | code` 展示，`NNNN` 就是应填写到 edit 中的真实行号。

6. 如果当前材料不足以安全修复，可以不产出 edits，改为返回 more_read_requests 或 search_requests 继续读取更多上下文。

7. 这是本轮修复中的第 {cycle_index} 次动作。你已经看到当前这一时刻的最新编译结果。

8. 只有在你基于当前编译结果判断“本轮不需要再继续读/改”时，才返回 complete=true。

9. 如果本次响应包含 edits，程序会先应用 edits、重新编译，再决定是否继续本轮；不要把“改完后应该继续观察编译结果”的情况标为 complete。

10. 如果 cargo 报 private field / private method，禁止继续在外部模块访问 private 成员；应改用已有 public API，或在拥有该类型的模块内部增加必要 public 方法。

11. 给已有 impl 增加方法时，必须插入到该 impl 的 closing brace 之前；不要插入到后面的 `impl Default`、trait impl 或其它无关 impl 块里。

12. create_file/create_dir 只能创建项目内路径，例如 `src/foo.rs`、`src/foo/mod.rs`、`tests/foo.rs`；禁止创建 `target/`、`.git/` 或项目外路径。

13. 如果 create_file 创建了新模块文件，通常还需要同时编辑已有 `src/main.rs` 或 `src/lib.rs` 添加 `mod xxx;`，否则 Rust 不会编译该文件。

14. 禁止为了通过编译创建空行为 stub 或 fallback，例如 `fn main(_args) -> i32 {{ 0 }}`、默认返回空 Vec/None/Ok、忽略所有参数的占位实现。

15. 如果缺失的是核心业务模块，不要创建最小空实现；必须先通过 more_read_requests/search_requests 读取对应 C 源码、spec 或已有 Rust 相关模块，再做真实修复。

16. 禁止用 delete_range/replace_range 把已有有效实现大幅删短。修复应保留原功能，只改导致当前错误的最小范围。

17. create_file 只能用于“根据已读证据创建真实实现”或“创建纯模块声明文件”；不能创建返回默认值的占位业务模块。

18. 如果当前错误是 `could not find module/type/function`，优先搜索和读取已有 Rust/C/spec 证据，判断是模块声明缺失、文件路径错误、命名不一致，还是代码生成缺失。

{tool_protocol}



诊断计划：

```json

{json.dumps(diagnosis_plan, ensure_ascii=False, indent=2)}

```

{summary_block}



已读材料清单：

```text

{material_inventory}

```



相关错误：

{chr(10).join(error_sections)}{error_note}



已读取材料：

{chr(10).join(material_blocks)}



返回 JSON：

{{

  "summary": "本轮修复摘要",

  "edits": [

    {{

      "path": "src/file.rs",

      "mode": "replace_range",

      "start_line": 10,

      "end_line": 30,

      "content": "替换后的完整片段，保持合法 Rust"

    }}

  ],

  "more_read_requests": [

    {{"kind": "rust", "path": "src/file.rs", "mode": "line_range", "start_line": 40, "end_line": 120}},

    {{"kind": "c", "path": "which.c", "mode": "whole_file"}},

    {{"kind": "spec", "path": "docs/rewrite-context/00_repo_manifest.md", "mode": "whole_file"}}

  ],

  "search_requests": [

    {{"kind": "all", "query": "rotate_right", "path_glob": "**/*", "context_lines": 2, "max_results": 6}}

  ],

  "complete": false,

  "updated_summary": "基于本次读取、编辑和当前编译结果更新后的摘要"

}}

"""



    def _request_structured_edits(self, project_dir: str, diagnosis_plan: Dict, grouped_errors: Dict[str, str], materials: List[Dict], cycle_index: int, current_summary: str = "", handoff_summary: str = "") -> Dict:

        prompt = self._build_edit_prompt(project_dir, diagnosis_plan, grouped_errors, materials, cycle_index, current_summary, handoff_summary)

        self._set_request_label("结构化修复编辑")

        response = self.llm.generate([

            {"role": "system", "content": "你是经验丰富的 Rust 编译修复助手，擅长给出最小编辑。"},

            {"role": "user", "content": prompt},

        ])

        parsed = self._extract_json_payload(response[0] if isinstance(response, list) else response)

        if isinstance(parsed, dict):

            parsed.setdefault("edits", [])

            parsed.setdefault("more_read_requests", [])

            parsed.setdefault("search_requests", [])

            parsed.setdefault("complete", False)

            parsed.setdefault("updated_summary", parsed.get("summary", ""))

            return parsed

        return {"summary": "LLM 结构化编辑解析失败", "edits": [], "more_read_requests": [], "search_requests": [], "complete": False, "updated_summary": ""}



    def _build_handoff_summary_prompt(

        self,

        previous_handoff: str,

        baseline_output: str,

        candidate_output: str,

        candidate_summary: str,

        accepted_as_best: bool,

    ) -> str:

        return f"""你在为下一轮 Rust 修复模型编写交接摘要。



要求：

1. 只输出纯文本摘要，不要 markdown 围栏。

2. 摘要要短，但要明确说明：

   - 本轮做了什么

   - 哪些修改有效/无效

   - 下一轮应避免什么

   - 下一轮应优先关注什么

3. 如果本轮结果未被接受，明确说明基线没有推进，下一轮会继续从旧项目副本开始。



上一轮交接摘要：

```text

{previous_handoff}

```



本轮 AI 自己维护的摘要：

```text

{candidate_summary}

```



基线编译结果摘要：

```text

{self._error_excerpt(baseline_output, max_lines=20)}

```



本轮结束时编译结果摘要：

```text

{self._error_excerpt(candidate_output, max_lines=20)}

```



本轮结果是否被接受为新基线：{"是" if accepted_as_best else "否"}

"""



    def _request_handoff_summary(

        self,

        previous_handoff: str,

        baseline_output: str,

        candidate_output: str,

        candidate_summary: str,

        accepted_as_best: bool,

    ) -> str:

        prompt = self._build_handoff_summary_prompt(

            previous_handoff,

            baseline_output,

            candidate_output,

            candidate_summary,

            accepted_as_best,

        )

        self._set_request_label("修复交接摘要")

        response = self.llm.generate([

            {"role": "system", "content": "你是经验丰富的 Rust 修复交接助手。"},

            {"role": "user", "content": prompt},

        ])

        text = response[0] if isinstance(response, list) else response

        summary = self._extract_code(text or "").strip()

        if summary:

            return summary

        return candidate_summary or previous_handoff



    def _apply_single_edit_to_lines(self, lines: List[str], edit: Dict) -> Tuple[List[str], int, Dict]:

        mode = edit.get("mode") or "replace_range"

        content = edit.get("content") or ""

        record = {"mode": mode}



        if mode == "replace_range":

            start_line = int(edit.get("start_line") or 1)

            end_line = int(edit.get("end_line") or start_line)

            actual_start_line = max(1, start_line)

            actual_end_line = max(actual_start_line, end_line)

            start = actual_start_line - 1

            end = min(len(lines), actual_end_line)

            replacement = content

            if replacement and not replacement.endswith("\n"):

                replacement += "\n"

            before_text = "".join(lines[start:end])

            new_segments = replacement.splitlines(keepends=True)

            delta = len(new_segments) - (end - start)

            new_lines = lines[:start] + new_segments + lines[end:]

            record.update({

                "start_line": start_line,

                "end_line": end_line,

                "actual_start_line": actual_start_line,

                "actual_end_line": actual_end_line,

                "before": before_text[:1200],

                "after": replacement[:1200],

            })

            return new_lines, delta, record



        if mode == "delete_range":

            start_line = int(edit.get("start_line") or 1)

            end_line = int(edit.get("end_line") or start_line)

            actual_start_line = max(1, start_line)

            actual_end_line = max(actual_start_line, end_line)

            start = actual_start_line - 1

            end = min(len(lines), actual_end_line)

            before_text = "".join(lines[start:end])

            delta = -(end - start)

            new_lines = lines[:start] + lines[end:]

            record.update({

                "start_line": start_line,

                "end_line": end_line,

                "actual_start_line": actual_start_line,

                "actual_end_line": actual_end_line,

                "before": before_text[:1200],

                "after": "",

            })

            return new_lines, delta, record



        if mode == "insert_before":

            before_line = int(edit.get("before_line") or edit.get("start_line") or 1)

            actual_before_line = max(1, before_line)

            insert_at = max(0, min(len(lines), actual_before_line - 1))

            insertion = content

            if insertion and not insertion.endswith("\n"):

                insertion += "\n"

            insertion_lines = insertion.splitlines(keepends=True)

            anchor_before = "".join(lines[max(0, insert_at - 2):min(len(lines), insert_at + 2)])

            new_lines = lines[:insert_at] + insertion_lines + lines[insert_at:]

            record.update({

                "before_line": before_line,

                "actual_before_line": actual_before_line,

                "before": anchor_before[:1200],

                "after": insertion[:1200],

            })

            return new_lines, len(insertion_lines), record



        if mode == "insert_after":

            after_line = int(edit.get("after_line") or edit.get("end_line") or edit.get("start_line") or 0)

            actual_after_line = max(0, after_line)

            insert_at = max(0, min(len(lines), actual_after_line))

            insertion = content

            if insertion and not insertion.endswith("\n"):

                insertion += "\n"

            insertion_lines = insertion.splitlines(keepends=True)

            anchor_before = "".join(lines[max(0, insert_at - 2):min(len(lines), insert_at + 2)])

            new_lines = lines[:insert_at] + insertion_lines + lines[insert_at:]

            record.update({

                "after_line": after_line,

                "actual_after_line": actual_after_line,

                "before": anchor_before[:1200],

                "after": insertion[:1200],

            })

            return new_lines, len(insertion_lines), record



        raise ValueError(f"unsupported edit mode: {mode}")



    def _shift_edit_line(self, edit: Dict, key: str, pivot_start: int, pivot_end: int, delta: int, clamp_mode: str):

        if key not in edit:

            return

        try:

            value = int(edit.get(key))

        except Exception:

            return

        if value > pivot_end:

            edit[key] = value + delta

        elif pivot_start <= value <= pivot_end:

            if clamp_mode == "before":

                edit[key] = pivot_start

            elif clamp_mode == "after":

                edit[key] = max(0, pivot_start - 1)



    def _update_remaining_edits_after_apply(self, remaining_edits: List[Dict], applied_edit: Dict, delta: int):

        mode = applied_edit.get("mode") or "replace_range"

        if mode in {"replace_range", "delete_range"}:

            pivot_start = int(applied_edit.get("actual_start_line") or applied_edit.get("start_line") or 1)

            pivot_end = int(applied_edit.get("actual_end_line") or applied_edit.get("end_line") or pivot_start)

            for edit in remaining_edits:

                self._shift_edit_line(edit, "start_line", pivot_start, pivot_end, delta, "before")

                self._shift_edit_line(edit, "end_line", pivot_start, pivot_end, delta, "before")

                self._shift_edit_line(edit, "before_line", pivot_start, pivot_end, delta, "before")

                self._shift_edit_line(edit, "after_line", pivot_start, pivot_end, delta, "after")

            return



        if mode == "insert_before":

            pivot_line = int(applied_edit.get("actual_before_line") or applied_edit.get("before_line") or 1)

            for edit in remaining_edits:

                for key in ("start_line", "end_line", "before_line", "after_line"):

                    if key not in edit:

                        continue

                    try:

                        value = int(edit.get(key))

                    except Exception:

                        continue

                    if value >= pivot_line:

                        edit[key] = value + delta

            return



        if mode == "insert_after":

            pivot_line = int(applied_edit.get("actual_after_line") or applied_edit.get("after_line") or 0)

            for edit in remaining_edits:

                for key in ("start_line", "end_line", "before_line", "after_line"):

                    if key not in edit:

                        continue

                    try:

                        value = int(edit.get(key))

                    except Exception:

                        continue

                    if value > pivot_line:

                        edit[key] = value + delta



    @staticmethod

    def _brace_imbalance(lines: List[str]) -> int:

        """返回文件的花括号不平衡度：abs(open - close)。0 表示完全平衡。"""

        text = "".join(lines)

        opens = text.count('{') + text.count('(') + text.count('[')

        closes = text.count('}') + text.count(')') + text.count(']')

        return abs(opens - closes)



    def _apply_structured_edits(self, project_dir: str, edits: List[Dict]) -> bool:

        applied, _ = self._apply_structured_edits_with_audit(project_dir, edits)

        return applied



    def _apply_structured_edits_with_audit(self, project_dir: str, edits: List[Dict]) -> Tuple[bool, List[Dict]]:

        audit_records: List[Dict] = []

        applied_any = False

        edits_by_file: Dict[str, List[Dict]] = {}



        for edit in edits:

            rel_path = (edit.get("path") or "").replace("\\", "/")

            if not rel_path:

                continue

            mode = edit.get("mode") or "replace_range"

            if mode == "create_dir":

                full_path, normalized_path = self._resolve_project_path(project_dir, rel_path)

                if not full_path or not normalized_path:

                    audit_records.append({"path": rel_path, "mode": mode, "skipped": True, "reason": "invalid project path"})

                    continue

                os.makedirs(full_path, exist_ok=True)

                audit_records.append({"path": normalized_path, "mode": mode, "created": True})

                applied_any = True

                continue

            if mode == "create_file":

                full_path, normalized_path = self._resolve_project_path(project_dir, rel_path)

                if not full_path or not normalized_path:

                    audit_records.append({"path": rel_path, "mode": mode, "skipped": True, "reason": "invalid project path"})

                    continue

                if not self._is_allowed_created_file(normalized_path):

                    audit_records.append({"path": normalized_path, "mode": mode, "skipped": True, "reason": "unsupported created file type"})

                    continue

                overwrite = bool(edit.get("overwrite", False))

                if os.path.exists(full_path) and not overwrite:

                    audit_records.append({"path": normalized_path, "mode": mode, "skipped": True, "reason": "file already exists; use line edits or explicit overwrite"})

                    continue

                content = edit.get("content") or ""

                if not str(content).strip():

                    audit_records.append({"path": normalized_path, "mode": mode, "skipped": True, "reason": "empty create_file content"})

                    continue

                if self._looks_like_compile_only_stub(normalized_path, str(content)):

                    audit_records.append({"path": normalized_path, "mode": mode, "skipped": True, "reason": "compile-only stub content is forbidden"})

                    print(f"  跳过空行为 stub 文件创建：{normalized_path}")

                    continue

                self._write_file(full_path, content)

                audit_records.append({

                    "path": normalized_path,

                    "mode": mode,

                    "created": True,

                    "overwrite": overwrite,

                    "after": str(content)[:1200],

                })

                applied_any = True

                continue

            edits_by_file.setdefault(rel_path, []).append(edit)



        for rel_path, file_edits in edits_by_file.items():

            full_path, normalized_rel_path = self._resolve_project_path(project_dir, rel_path)

            if not full_path or not normalized_rel_path:

                audit_records.append({"path": rel_path, "skipped": True, "reason": "invalid project path"})

                continue

            if not os.path.exists(full_path):

                audit_records.append({"path": rel_path, "skipped": True, "reason": "file does not exist; use create_file for new files"})

                continue

            original_lines = self._read_file(full_path).splitlines(keepends=True)

            lines = list(original_lines)

            file_changed = False

            pending_edits = [dict(edit) for edit in file_edits]



            for index, edit in enumerate(pending_edits):

                mode = edit.get("mode") or "replace_range"

                if mode not in {"replace_range", "delete_range", "insert_before", "insert_after"}:

                    audit_records.append({"path": normalized_rel_path, "mode": mode, "skipped": True, "reason": "unsupported edit mode"})

                    continue

                start = int(edit.get("start_line") or 0)

                end = int(edit.get("end_line") or start)

                span = end - start + 1 if start and end else 0

                file_imbalance = self._brace_imbalance(lines)

                if file_imbalance >= 4:

                    max_replace_span = len(lines) + 10

                elif file_imbalance >= 2:

                    max_replace_span = 300

                else:

                    max_replace_span = 120

                if mode == "replace_range" and span > max_replace_span:

                    print(f"  跳过过大的 replace_range ({span} 行, 上限 {max_replace_span})：{rel_path}:{start}-{end}")

                    audit_records.append({"path": rel_path, "skipped": True, "reason": f"replace_range too large: {span} > {max_replace_span}", "start": start, "end": end})

                    continue

                if mode == "delete_range" and span > 60:

                    print(f"  跳过过大的 delete_range ({span} 行)：{rel_path}:{start}-{end}")

                    continue

                try:

                    imbalance_before = self._brace_imbalance(lines)

                    new_lines, delta, record = self._apply_single_edit_to_lines(lines, edit)

                    imbalance_after = self._brace_imbalance(new_lines)

                    if imbalance_after > imbalance_before + 2:

                        print(f"  跳过使括号平衡恶化的编辑：{rel_path}:{start}-{end} (imbalance {imbalance_before}->{imbalance_after})")

                        continue

                    lines = new_lines

                except Exception:

                    continue

                record["path"] = normalized_rel_path

                audit_records.append(record)

                self._update_remaining_edits_after_apply(pending_edits[index + 1:], record, delta)

                file_changed = True

                applied_any = True



            if file_changed:

                destructive, reason = self._is_destructive_existing_file_edit(normalized_rel_path, original_lines, lines)

                if destructive:

                    audit_records.append({"path": normalized_rel_path, "skipped": True, "reason": reason})

                    print(f"  跳过破坏性编辑：{normalized_rel_path}，原因：{reason}")

                    continue

                self._write_file(full_path, "".join(lines))



        return applied_any, audit_records



    def _collect_related_context(self, project_dir: str, target_rel_path: str, max_chars: int = 16000) -> str:

        parts = []

        total = 0



        def add_block(label: str, text: str):

            nonlocal total

            block = f"\n\n=== {label} ===\n{text}\n"

            if total + len(block) > max_chars:

                remain = max_chars - total

                if remain > 0:

                    parts.append(block[:remain])

                total = max_chars

                return

            parts.append(block)

            total += len(block)



        cargo_path = os.path.join(project_dir, "Cargo.toml")

        if os.path.exists(cargo_path):

            add_block("Cargo.toml", self._read_file(cargo_path))



        lib_path = os.path.join(project_dir, "src", "lib.rs")

        if os.path.exists(lib_path) and target_rel_path != "src/lib.rs":

            add_block("src/lib.rs", self._read_file(lib_path))



        src_dir = os.path.join(project_dir, "src")

        if os.path.isdir(src_dir):

            for name in sorted(os.listdir(src_dir)):

                if not name.endswith(".rs"):

                    continue

                rel = f"src/{name}"

                if rel == target_rel_path or rel == "src/lib.rs":

                    continue

                full_path = os.path.join(src_dir, name)

                try:

                    content = self._read_file(full_path)

                except Exception:

                    continue

                add_block(rel, content[:3000])

                if total >= max_chars:

                    break



        return "".join(parts).strip()



    def _build_fix_prompt(self, target_rel_path: str, error_block: str, file_content: str, related_context: str) -> str:

        return f"""你在修复一个已经生成好的 Rust 项目中的单个文件。



目标文件：{target_rel_path}



要求：

1. 只返回修复后的完整文件内容，不要解释。

2. 不要输出 markdown 围栏。

3. 优先修复导致当前编译失败的根因，不要顺手大改架构。

4. 尽量保持与现有其它文件兼容。

5. 如果文件当前被 markdown 围栏污染、被截断或大括号不平衡，先修复这些问题。



当前编译错误：

```text

{error_block}

```



当前文件内容：

```rust

{file_content}

```



相关上下文：

```text

{related_context}

```

"""



    def _llm_fix_file(self, project_dir: str, rel_path: str, error_block: str, max_continuation_rounds: int = 4) -> bool:

        full_path = os.path.join(project_dir, rel_path.replace("/", os.sep))

        if not os.path.exists(full_path):

            return False



        file_content = self._read_file(full_path)

        related_context = self._collect_related_context(project_dir, rel_path)

        prompt = self._build_fix_prompt(rel_path, error_block, file_content, related_context)

        system_prompt = "你是经验丰富的 Rust 编译修复助手。"

        self._set_request_label(f"独立修复 {os.path.basename(rel_path)}")

        response = self.llm.generate([

            {"role": "system", "content": system_prompt},

            {"role": "user", "content": prompt},

        ])

        fixed_code = self._extract_code(response[0] if isinstance(response, list) else response)

        if not fixed_code.strip():

            return False



        for cont_round in range(1, max_continuation_rounds + 1):

            opens = fixed_code.count("{")

            closes = fixed_code.count("}")

            if opens - closes < 3:

                break

            print(f"    独立修复续写 {rel_path} [continuation {cont_round}]")

            continuation_user = (

                f"你上一次输出在 max_tokens 处被截断了。"

                f"下面是你已经输出的全部代码：\n"

                f"```rust\n{fixed_code}\n```\n\n"

                f"请从截断处继续输出（不要重复已有代码），直到文件完整结束。\n"

                f"只输出代码续写部分，不需要解释。"

            )

            self._set_request_label(f"独立修复续写 {os.path.basename(rel_path)} [cont {cont_round}]")

            reply = self.llm.generate([

                {"role": "system", "content": system_prompt},

                {"role": "user", "content": prompt},

                {"role": "assistant", "content": f"```rust\n{fixed_code}\n```"},

                {"role": "user", "content": continuation_user},

            ])

            chunk = self._extract_code(reply[0] if isinstance(reply, list) else reply)

            chunk = self._strip_outer_code_fences(chunk)

            if chunk.strip():

                if not fixed_code.endswith("\n"):

                    fixed_code += "\n"

                fixed_code += chunk.strip()

            else:

                break



        self._write_file(full_path, fixed_code)

        return True



    def _run_single_iteration(

        self,

        baseline_dir: str,

        runs_root: str,

        iteration: int,

        handoff_summary: str = "",

        in_place: bool = False,

    ) -> RepairRunResult:

        run_dir = str(Path(baseline_dir).resolve()) if in_place else self._clone_project_tree(baseline_dir, runs_root, iteration)

        journal_path = self._journal_path(run_dir)

        self._append_repair_record(journal_path, {

            "iteration": iteration,

            "stage": "in_place_start" if in_place else "clone",

            "baseline_dir": baseline_dir,

            "run_dir": run_dir,

            "in_place": in_place,

        })

        self._sanitize_project_locally(run_dir)

        self._append_repair_record(journal_path, {

            "iteration": iteration,

            "stage": "local_sanitize",

        })



        check_success, check_output = self._cargo_check(run_dir)

        if check_success:

            compile_result = self._compile_success_result(run_dir, check_output, handoff_summary)
            if compile_result.test_passed:
                return compile_result
            check_output = compile_result.output



        self._append_repair_record(journal_path, {

            "iteration": iteration,

            "stage": "pre_llm_no_fallback",

            "error_count": self._count_errors(check_output),

            "error_signature": self._error_signature(check_output),

            "error_excerpt": self._error_excerpt(check_output),

        })

        check_success, check_output = self._cargo_check(run_dir)

        if check_success:

            compile_result = self._compile_success_result(run_dir, check_output, handoff_summary)
            if compile_result.test_passed:
                return compile_result
            check_output = compile_result.output



        grouped_errors = self._group_rust_errors_by_file(check_output)

        project_overview = self._build_project_overview(run_dir)

        diagnosis_plan = self._request_diagnosis_plan(grouped_errors, project_overview, handoff_summary)

        materials = self._materialize_read_requests(run_dir, diagnosis_plan.get("read_requests", []))

        diagnosis_search_materials = self._materialize_search_requests(run_dir, diagnosis_plan.get("search_requests", []))

        materials.extend(diagnosis_search_materials)

        if diagnosis_search_materials:

            self._append_repair_record(journal_path, {

                "iteration": iteration,

                "stage": "diagnosis_search_context",

                "search_requests": diagnosis_plan.get("search_requests", []),

                "materials_now": [

                    {

                        "kind": material.get("kind", "rust"),

                        "path": material["path"],

                        "mode": material["mode"],

                        "query": material.get("query"),

                        "content_chars": len(material["content"]),

                    }

                    for material in diagnosis_search_materials

                ],

            })

        current_check_success = check_success

        current_check_output = check_output

        current_summary = (diagnosis_plan.get("summary") or "").strip() or handoff_summary

        round_start = self._monotonic()

        cycle_index = 0

        timed_out = False

        recent_signatures: List[str] = []

        recent_error_counts: List[int] = []

        max_stall_repeats = 3



        while True:

            if self._monotonic() - round_start >= self.iteration_timeout_seconds:

                timed_out = True

                self._append_repair_record(journal_path, {

                    "iteration": iteration,

                    "stage": "round_timeout",

                    "cycle_index": cycle_index,

                    "elapsed_seconds": self._monotonic() - round_start,

                    "error_count": self._count_errors(current_check_output),

                    "error_signature": self._error_signature(current_check_output),

                    "error_excerpt": self._error_excerpt(current_check_output),

                    "current_summary": current_summary,

                })

                break



            cycle_index += 1

            grouped_errors = self._group_rust_errors_by_file(current_check_output)

            structured = self._request_structured_edits(run_dir, diagnosis_plan, grouped_errors, materials, cycle_index, current_summary, handoff_summary)

            more_reads = structured.get("more_read_requests", []) or []

            search_requests = structured.get("search_requests", []) or []

            updated_summary = (structured.get("updated_summary") or structured.get("summary") or current_summary).strip()

            if updated_summary:

                current_summary = updated_summary



            if more_reads:

                new_materials = self._materialize_read_requests(run_dir, more_reads, max_chars=12000)

                existing_keys = {

                    (m.get("kind", "rust"), m["path"], m["mode"], m["start_line"], m["end_line"])

                    for m in materials

                }

                for material in new_materials:

                    key = (material.get("kind", "rust"), material["path"], material["mode"], material["start_line"], material["end_line"])

                    if key not in existing_keys:

                        materials.append(material)

                        existing_keys.add(key)

                self._append_repair_record(journal_path, {

                    "iteration": iteration,

                    "stage": "llm_more_context",

                    "cycle_index": cycle_index,

                    "more_read_requests": more_reads,

                    "updated_summary": current_summary,

                    "materials_now": [

                        {

                            "kind": material.get("kind", "rust"),

                            "path": material["path"],

                            "mode": material["mode"],

                            "start_line": material["start_line"],

                            "end_line": material["end_line"],

                            "content_chars": len(material["content"]),

                        }

                        for material in materials

                    ],

                })



            if search_requests:

                new_search_materials = self._materialize_search_requests(run_dir, search_requests, max_chars=12000)

                existing_keys = {

                    (m.get("kind", "rust"), m.get("query"), m["path"], m["mode"], m["start_line"], m["end_line"])

                    for m in materials

                }

                added_search_materials = []

                for material in new_search_materials:

                    key = (material.get("kind", "rust"), material.get("query"), material["path"], material["mode"], material["start_line"], material["end_line"])

                    if key not in existing_keys:

                        materials.append(material)

                        added_search_materials.append(material)

                        existing_keys.add(key)

                self._append_repair_record(journal_path, {

                    "iteration": iteration,

                    "stage": "llm_search_context",

                    "cycle_index": cycle_index,

                    "search_requests": search_requests,

                    "updated_summary": current_summary,

                    "materials_now": [

                        {

                            "kind": material.get("kind", "rust"),

                            "path": material["path"],

                            "mode": material["mode"],

                            "query": material.get("query"),

                            "content_chars": len(material["content"]),

                        }

                        for material in added_search_materials

                    ],

                })



            if structured.get("edits"):

                applied, applied_records = self._apply_structured_edits_with_audit(run_dir, structured.get("edits", []))

                self._append_repair_record(journal_path, {

                    "iteration": iteration,

                    "stage": "llm_repair",

                    "cycle_index": cycle_index,

                    "error_count_before": self._count_errors(current_check_output),

                    "error_signature_before": self._error_signature(current_check_output),

                    "diagnosis_plan": diagnosis_plan,

                    "updated_summary": current_summary,

                    "materials": [

                        {

                            "kind": material.get("kind", "rust"),

                            "path": material["path"],

                            "mode": material["mode"],

                            "start_line": material["start_line"],

                            "end_line": material["end_line"],

                            "content_chars": len(material["content"]),

                        }

                        for material in materials

                    ],

                    "structured_summary": structured.get("summary", ""),

                    "edits": structured.get("edits", []),

                    "search_requests": search_requests,

                    "applied": applied,

                    "applied_records": applied_records,

                })



                self._sanitize_project_locally(run_dir)



                edited_paths = {

                    (e.get("path") or "").replace("\\", "/")

                    for e in structured.get("edits", [])

                    if e.get("path")

                }

                self._refresh_materials_for_edited_files(run_dir, materials, edited_paths)



                current_check_success, current_check_output = self._cargo_check(run_dir)

                self._append_repair_record(journal_path, {

                    "iteration": iteration,

                    "stage": "post_check",

                    "cycle_index": cycle_index,

                    "check_passed": current_check_success,

                    "error_count_after": 0 if current_check_success else self._count_errors(current_check_output),

                    "error_signature_after": self._error_signature(current_check_output),

                    "error_excerpt_after": "" if current_check_success else self._error_excerpt(current_check_output),

                    "current_summary": current_summary,

                })

                if current_check_success:

                    compile_result = self._compile_success_result(run_dir, current_check_output, current_summary)
                    if compile_result.test_passed:
                        return compile_result
                    current_check_output = compile_result.output



                post_sig = self._error_signature(current_check_output)

                post_err_count = self._count_errors(current_check_output)

                recent_signatures.append(post_sig)

                recent_error_counts.append(post_err_count)



                stalled = False

                if len(recent_signatures) >= max_stall_repeats:

                    tail = recent_signatures[-max_stall_repeats:]

                    if all(s == tail[0] for s in tail):

                        stalled = True

                if not stalled and len(recent_error_counts) >= max_stall_repeats + 1:

                    window = recent_error_counts[-(max_stall_repeats + 1):]

                    if all(window[i] >= window[0] for i in range(1, len(window))):

                        stalled = True



                if stalled:

                    self._append_repair_record(journal_path, {

                        "iteration": iteration,

                        "stage": "error_signature_stall",

                        "cycle_index": cycle_index,

                        "repeated_signature": post_sig,

                        "recent_error_counts": recent_error_counts[-max_stall_repeats:],

                        "repeats": max_stall_repeats,

                        "current_summary": current_summary,

                    })

                    print(f"  错误签名/数量连续 {max_stall_repeats} 次未改善，判定为停滞，退出本轮修复循环。")

                    break

                continue



            if structured.get("complete"):

                self._append_repair_record(journal_path, {

                    "iteration": iteration,

                    "stage": "llm_cycle_complete",

                    "cycle_index": cycle_index,

                    "summary": structured.get("summary", ""),

                    "updated_summary": current_summary,

                    "error_count": self._count_errors(current_check_output),

                    "error_signature": self._error_signature(current_check_output),

                })

                break



            if more_reads or search_requests:

                continue



            self._append_repair_record(journal_path, {

                "iteration": iteration,

                "stage": "llm_cycle_stalled",

                "cycle_index": cycle_index,

                "summary": structured.get("summary", ""),

                "updated_summary": current_summary,

                "error_count": self._count_errors(current_check_output),

                "error_signature": self._error_signature(current_check_output),

            })

            break



        return RepairRunResult(

            run_dir,

            False,

            False,

            self._count_errors(current_check_output),

            current_check_output,

            self._error_signature(current_check_output),

            self._frontier_metrics(current_check_output),

            round_summary=current_summary,

            timed_out=timed_out,

        )



    def repair_project(

        self,

        project_path: str,

        runs_root: Optional[str] = None,

        apply_best: bool = False,

        in_place: bool = True,

    ) -> RepairRunResult:

        project_path = str(Path(project_path).resolve())

        runs_root = runs_root or os.path.join(os.path.dirname(project_path), "repair_runs")

        baseline_dir = project_path

        handoff_summary = ""



        baseline_success, baseline_output = self._cargo_check(project_path)

        baseline_result = RepairRunResult(

            run_dir=project_path,

            check_passed=baseline_success,

            test_passed=False,

            error_count=0 if baseline_success else self._count_errors(baseline_output),

            output=baseline_output,

            error_signature=self._error_signature(baseline_output),

            frontier_metrics=self._frontier_metrics(baseline_output),

        )

        self.best_result = baseline_result



        for iteration in range(1, self.max_iterations + 1):

            print(f"\n=== Repair Iteration {iteration}/{self.max_iterations} ===")

            previous_best_error_count = self.best_result.error_count if self.best_result else None

            previous_best_signature = self.best_result.error_signature if self.best_result else ""

            previous_best_metrics = self.best_result.frontier_metrics if self.best_result else {}

            previous_best_output = self.best_result.output if self.best_result else ""

            result = self._run_single_iteration(

                baseline_dir,

                runs_root,

                iteration,

                handoff_summary=handoff_summary,

                in_place=in_place,

            )

            print(f"run_dir: {result.run_dir}")

            print(f"check_passed: {result.check_passed}")

            print(f"build_release_passed: {result.test_passed}")

            print(f"error_count: {result.error_count}")



            accepted_as_best, accept_reason = self._should_accept_result(self.best_result, result)

            if accepted_as_best or in_place:

                self.best_result = result

                baseline_dir = result.run_dir



            handoff_summary = self._request_handoff_summary(

                handoff_summary,

                previous_best_output,

                result.output,

                result.round_summary,

                accepted_as_best,

            )



            self._append_repair_record(self._journal_path(result.run_dir), {

                "iteration": iteration,

                "stage": "iteration_result",

                "accepted_as_best": accepted_as_best,

                "accept_reason": accept_reason,

                "timed_out": result.timed_out,

                "round_summary": result.round_summary,

                "handoff_summary_for_next_iteration": handoff_summary,

                "previous_best_error_count": previous_best_error_count,

                "previous_best_signature": previous_best_signature,

                "previous_best_metrics": previous_best_metrics,

                "result_error_count": result.error_count,

                "result_signature": result.error_signature,

                "result_metrics": result.frontier_metrics,

                "baseline_dir_after": baseline_dir,

            })



            if result.check_passed and result.test_passed:

                break



        if not in_place and apply_best and self.best_result and self.best_result.run_dir != project_path:

            if os.path.exists(project_path):

                shutil.rmtree(project_path)

            shutil.copytree(self.best_result.run_dir, project_path)



        return self.best_result





def main():

    parser = argparse.ArgumentParser(description="独立 Rust 修复 Agent")

    parser.add_argument("--project_path", required=True, help="待修复的 Rust 项目路径")

    parser.add_argument("--config-file", default=str(Path(__file__).parent.parent.parent / "local_config.json"))

    parser.add_argument("--max-iterations", type=int, default=15)

    parser.add_argument("--runs-root", default="")

    parser.add_argument("--c-project-path", default="", help="原始 C 项目路径，供修复时按需读取")

    parser.add_argument("--c-docs-path", default="", help="c_docs/spec 文档路径，供修复时按需读取")

    parser.add_argument("--copy-runs", action="store_true", help="使用旧模式：每轮复制项目到 runs 目录中修复")

    parser.add_argument("--apply-best", action="store_true", help="仅在 --copy-runs 模式下，把最佳结果回写到原项目目录")

    args = parser.parse_args()



    config = Config(config_path=args.config_file)

    agent = RustRepairAgent(config=config, max_iterations=args.max_iterations)

    agent.configure_context_sources(

        c_project_path=args.c_project_path,

        c_docs_path=args.c_docs_path,

    )

    result = agent.repair_project(

        project_path=args.project_path,

        runs_root=args.runs_root or None,

        apply_best=args.apply_best,

        in_place=not args.copy_runs,

    )



    print("\n=== Repair Summary ===")

    print(f"best_run_dir: {result.run_dir}")

    print(f"check_passed: {result.check_passed}")

    print(f"build_release_passed: {result.test_passed}")

    print(f"error_count: {result.error_count}")





if __name__ == "__main__":

    main()

