import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config
from llm.model import Model
from utils.cmd import run


class StableRustAgent:
    """
    更薄、更可控的 Rust 生成 Agent。

    设计原则：
    1. 让大模型决定文件计划和各个源码文件内容
    2. 把 Cargo.toml / lib.rs / 内联测试清理这类高风险边界收回本地处理
    3. 默认全量重建；只有 continue_mode=true 时才跳过已完成文件
    """

    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.llm = Model(self.config)

        self.project_name: str = ""
        self.project_path: str = ""
        self.doc_paths: List[str] = []
        self.doc_contents: Dict[str, str] = {}
        self.generated_files: List[str] = []
        self.continue_mode: bool = False
        self.generation_plan: Dict = {}

    def _set_request_label(self, label: str):
        if hasattr(self.llm, "set_request_label"):
            self.llm.set_request_label(label)

    def _run_command(self, command: str):
        return run(command)

    def _generation_plan_path(self) -> str:
        return os.path.join(self.project_path, ".cgr_generation_plan.json")

    def _clip_document_content(self, doc_path: str, content: str) -> str:
        normalized = doc_path.replace("\\", "/").lower()
        max_chars = 16000
        if normalized.endswith("spec_context.json"):
            max_chars = 20000
        elif normalized.endswith("001_pointer_macro_summary.md"):
            max_chars = 10000
        elif normalized.endswith(".md"):
            max_chars = 16000
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "\n\n[Document truncated]\n"

    def load_documents(self, doc_paths: List[str]):
        self.doc_paths = doc_paths
        self.doc_contents = {}

        for doc_path in doc_paths:
            if os.path.isfile(doc_path):
                try:
                    with open(doc_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    self.doc_contents[doc_path] = self._clip_document_content(doc_path, content)
                    print(f"加载文件：{doc_path} ({len(content)} 字符)")
                except Exception as e:
                    print(f"读取文档失败：{doc_path}，原因：{e}")
            elif os.path.isdir(doc_path):
                for root, _, files in os.walk(doc_path):
                    for name in files:
                        if not (name.endswith(".md") or name.endswith(".json")):
                            continue
                        full_path = os.path.join(root, name)
                        try:
                            with open(full_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            self.doc_contents[full_path] = self._clip_document_content(full_path, content)
                            print(f"加载文件：{full_path} ({len(content)} 字符)")
                        except Exception as e:
                            print(f"读取文档失败：{full_path}，原因：{e}")

    def _collect_context(self, max_chars: int = 60000) -> str:
        parts = []
        total = 0
        for path, content in self.doc_contents.items():
            block = f"\n\n=== Document: {os.path.basename(path)} ===\n{content}\n"
            if total + len(block) > max_chars:
                remain = max_chars - total
                if remain > 0:
                    parts.append(block[:remain])
                break
            parts.append(block)
            total += len(block)
        return "".join(parts).strip()

    def _extract_done_marker(self, content: str) -> tuple[str, bool]:
        text = content or ""
        done = "<CGR_DONE>" in text
        return text.replace("<CGR_DONE>", "").strip(), done

    def _extract_generated_content(self, content: str, code_lang: str = "") -> str:
        text = (content or "").strip()
        if not text:
            return ""

        fence_match = re.match(r"^\s*```[^\n]*\n(.*)\n\s*```\s*$", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        fence_search = re.search(r"(?ms)^\s*```[^\n]*\n(.*?)\n\s*```", text)
        if fence_search:
            return fence_search.group(1).strip()

        return text

    def _strip_outer_code_fences(self, content: str, code_lang: str) -> str:
        content = (content or "").strip()
        fenced = self._extract_generated_content(content, code_lang=code_lang)
        if fenced != content:
            return fenced.strip()

        lines = content.splitlines()
        if not lines:
            return content

        start = 0
        end = len(lines)
        while start < end and not lines[start].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1

        if start < end and lines[start].strip().startswith("```"):
            start += 1
        if end > start and lines[end - 1].strip().startswith("```"):
            end -= 1

        return "\n".join(lines[start:end]).strip()

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
                char = result[index]
                if char == "{":
                    depth += 1
                elif char == "}":
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
                sanitized = self._extract_generated_content(sanitized, code_lang="rust")
            sanitized = re.sub(r",?\s*thiserror::Error", "", sanitized)
            sanitized = re.sub(r"(?m)^[ \t]*use\s+thiserror::Error;\s*\n?", "", sanitized)
            sanitized = re.sub(r"(?m)^[ \t]*#\[\s*error\([^\]]*\)\s*\]\s*\n?", "", sanitized)
            if not getattr(self.config, "generate_tests", False):
                sanitized = self._strip_inline_test_modules(sanitized)
            if normalized.endswith("/tree.rs"):
                sanitized = re.sub(
                    r"(^\s*)root\s*:\s*",
                    r"\1pub(crate) root: ",
                    sanitized,
                    count=1,
                    flags=re.MULTILINE,
                )
        elif normalized.endswith("cargo.toml"):
            sanitized = self._strip_outer_code_fences(sanitized, "toml")
            if sanitized.lstrip().startswith("```"):
                sanitized = self._extract_generated_content(sanitized, code_lang="toml")
        elif normalized.endswith("readme.md"):
            sanitized = self._strip_outer_code_fences(sanitized, "")

        return sanitized.strip() + ("\n" if sanitized.strip() else "")

    def _hash_file_content(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load_generation_plan(self) -> Dict:
        plan_path = self._generation_plan_path()
        if os.path.exists(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载生成计划失败：{e}")
        return {
            "project_name": self.project_name,
            "files": {},
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _save_generation_plan(self):
        if not self.project_path:
            return
        self.generation_plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            with open(self._generation_plan_path(), "w", encoding="utf-8") as f:
                json.dump(self.generation_plan, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存生成计划失败：{e}")

    def _set_plan_file_state(self, rel_path: str, status: str, content: str = "", note: str = ""):
        self.generation_plan.setdefault("files", {})
        entry = self.generation_plan["files"].setdefault(rel_path.replace("\\", "/"), {})
        entry["status"] = status
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if content:
            entry["size"] = len(content)
            entry["sha256"] = self._hash_file_content(content)
        if note:
            entry["note"] = note
        self._save_generation_plan()

    def _is_already_completed(self, rel_path: str) -> bool:
        if not self.continue_mode:
            return False
        rel_path = rel_path.replace("\\", "/")
        entry = self.generation_plan.get("files", {}).get(rel_path)
        if not entry or entry.get("status") != "completed":
            return False
        full_path = os.path.join(self.project_path, rel_path)
        if not os.path.exists(full_path):
            return False
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return False
        if not content.strip():
            return False
        return True

    def create_rust_project(self, project_name: str, output_dir: str) -> str:
        project_path = os.path.join(output_dir, project_name)
        if os.path.exists(project_path) and not self.continue_mode:
            shutil.rmtree(project_path)

        if not os.path.exists(project_path):
            parent = os.path.dirname(project_path)
            os.makedirs(parent, exist_ok=True)
            result = self._run_command(f'cd "{parent}" && cargo new --lib "{project_name}"')
            if result is not None:
                raise RuntimeError(f"创建 Rust 项目失败：{result}")

        self.project_path = project_path
        self.generation_plan = self._load_generation_plan() if self.continue_mode else {
            "project_name": project_name,
            "files": {},
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_generation_plan()
        return project_path

    def _supported_generation_file(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/").strip("/")
        lowered = normalized.lower()
        if not normalized:
            return False
        if lowered == "cargo.toml" or lowered == "readme.md":
            return True
        if lowered.startswith("src/") and lowered.endswith(".rs"):
            return True
        if lowered.startswith("tests/") and lowered.endswith(".rs"):
            return bool(getattr(self.config, "generate_tests", False))
        if lowered.startswith("examples/") and lowered.endswith(".rs"):
            return bool(getattr(self.config, "generate_examples", False))
        if lowered.startswith("benches/") and lowered.endswith(".rs"):
            return bool(getattr(self.config, "generate_benches", False))
        return False

    def _sanitize_generation_file_list(self, files: List[str]) -> List[str]:
        sanitized = []
        seen = set()
        for item in files:
            candidate = (item or "").strip().strip('"').strip("'").replace("\\", "/")
            candidate = re.sub(r"^\d+\.\s*", "", candidate)
            candidate = candidate.strip("/").strip()
            if not candidate or "\n" in candidate or "\r" in candidate:
                continue
            if ".." in candidate.split("/"):
                continue
            if not self._supported_generation_file(candidate):
                continue
            if candidate.lower() == "src/lib.rs":
                continue
            if candidate.lower() == "cargo.toml":
                candidate = "Cargo.toml"
            if candidate.lower() == "readme.md":
                candidate = "README.md"
            if candidate not in seen:
                seen.add(candidate)
                sanitized.append(candidate)
        return sanitized

    def _sort_files_for_generation(self, files: List[str]) -> List[str]:
        def score(path: str):
            lowered = path.replace("\\", "/").lower()
            if lowered == "cargo.toml":
                return (0, lowered)
            if lowered.startswith("src/"):
                return (1, lowered)
            if lowered == "readme.md":
                return (2, lowered)
            return (3, lowered)
        return sorted(files, key=score)

    def _parse_file_list(self, raw_text: str) -> List[str]:
        text = (raw_text or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return self._sanitize_generation_file_list([str(item) for item in data])
        except Exception:
            pass
        return self._sanitize_generation_file_list(text.splitlines())

    def _parse_new_files_to_generate(self, raw_text: str, fallback: Optional[List[str]] = None) -> List[str]:
        parsed = self._parse_file_list(raw_text)
        return parsed or (fallback or [])

    def _infer_fallback_file_list_from_docs(self) -> List[str]:
        """
        从文档中尽量恢复原始 C 项目的核心文件名，避免 fallback 固定成某个项目的结构。
        """
        module_names: List[str] = []
        seen = set()
        skip_names = {
            "test",
            "tests",
            "benchmark",
            "bench",
            "benches",
            "example",
            "examples",
            "main",
            "readme",
        }
        pattern = re.compile(r"(?i)\b(?:src/|include/)?([A-Za-z0-9_-]+)\.(c|h)\b")

        for content in self.doc_contents.values():
            for match in pattern.finditer(content or ""):
                base_name = match.group(1).lower()
                if base_name in skip_names:
                    continue
                if base_name not in seen:
                    seen.add(base_name)
                    module_names.append(base_name)

        files = ["Cargo.toml"]
        files.extend([f"src/{name}.rs" for name in module_names])
        files.append("README.md")
        return self._sort_files_for_generation(self._sanitize_generation_file_list(files))

    def _generate_with_continuation(self, system_prompt: str, user_prompt: str, code_lang: str, label: str, max_rounds: int = 4) -> str:
        accumulated = ""
        prompt = (
            user_prompt
            + "\n\nAdditional requirements:\n"
            + "1. If you cannot finish in one response, continue writing, and append <CGR_DONE> only when it is truly complete.\n"
            + "2. Do not repeat earlier content during continuation.\n"
            + "3. Do not output explanations beyond the body and <CGR_DONE>.\n"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        for round_index in range(1, max_rounds + 1):
            self._set_request_label(f"{label} [round {round_index}]")
            response = self.llm.generate(messages)[0]
            response, done = self._extract_done_marker(response)
            chunk = self._extract_generated_content(response, code_lang=code_lang)
            if code_lang:
                chunk = self._strip_outer_code_fences(chunk, code_lang)
            if chunk:
                if accumulated and not accumulated.endswith("\n"):
                    accumulated += "\n"
                accumulated += chunk.strip()
            if done:
                break
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        user_prompt
                        + "\n\nYour previous output already reached this point. Continue from the last position and do not repeat:\n"
                        + f"```{code_lang or 'text'}\n{accumulated[-6000:]}\n```"
                        + "\n\nAppend <CGR_DONE> at the end when complete."
                    ),
                },
            ]
        return accumulated.strip()

    def _generate_file_plan(self) -> List[str]:
        context = self._collect_context()
        prompt = f"""Rewrite a C project into Rust. First output only a JSON array representing the file paths that need to be created.

Requirements:
1. Output only a JSON array; do not explain.
2. Paths must be relative.
3. By default, generate only core files and do not proactively expand into a large project structure.
4. Must include Cargo.toml, README.md, and the core src/*.rs files.
5. Do not output lib.rs; lib.rs will be rebuilt automatically by the local program.
6. Unless the documentation explicitly requires it, do not generate tests/examples/benches.
7. Keep the project scope restrained and prioritize the core functionality described in the input documents.

Document context:
{context}
"""
        response = self._generate_with_continuation(
            system_prompt="You are a Rust project structure planning assistant. Output only a JSON array.",
            user_prompt=prompt,
            code_lang="json",
            label="项目文件规划",
            max_rounds=2,
        )
        files = self._parse_file_list(response)
        if files:
            return self._sort_files_for_generation(files)
        inferred = self._infer_fallback_file_list_from_docs()
        if inferred:
            return inferred
        return ["Cargo.toml", "README.md"]

    def _build_minimal_cargo_toml(self) -> str:
        crate_name = self.project_name or "rust_implementation"
        lines = [
            "[package]",
            f'name = "{crate_name}"',
            'version = "0.1.0"',
            'edition = "2021"',
            "",
            "[dependencies]",
            "",
        ]
        return "\n".join(lines)

    def _detect_dependencies(self, code: str) -> Dict[str, str]:
        deps = {}
        text = code or ""
        if "thiserror::" in text:
            deps["thiserror"] = "1.0"
        if "serde::" in text or "#[derive(Serialize" in text or "#[derive(Deserialize" in text:
            deps["serde"] = '{ version = "1.0", features = ["derive"] }'
        return deps

    def _update_cargo_toml(self, dependencies: Dict[str, str]):
        if not dependencies:
            return
        cargo_path = os.path.join(self.project_path, "Cargo.toml")
        try:
            with open(cargo_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = self._build_minimal_cargo_toml()

        if "[dependencies]" not in content:
            content = content.rstrip() + "\n\n[dependencies]\n"

        lines = content.splitlines()
        dep_start = next((i for i, line in enumerate(lines) if line.strip() == "[dependencies]"), None)
        if dep_start is None:
            lines.extend(["", "[dependencies]"])
            dep_start = len(lines) - 1

        existing = set()
        i = dep_start + 1
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("[") and stripped != "[dependencies]":
                break
            match = re.match(r"([A-Za-z0-9_-]+)\s*=", stripped)
            if match:
                existing.add(match.group(1))
            i += 1

        inserts = []
        for name, spec in dependencies.items():
            if name in existing:
                continue
            if spec.startswith("{"):
                inserts.append(f'{name} = {spec}')
            else:
                inserts.append(f'{name} = "{spec}"')

        if inserts:
            lines[dep_start + 1:dep_start + 1] = inserts
            updated = "\n".join(lines).rstrip() + "\n"
            with open(cargo_path, "w", encoding="utf-8") as f:
                f.write(updated)
            self._set_plan_file_state("Cargo.toml", "completed", updated)

    def _extract_public_items(self, content: str) -> List[str]:
        items = []
        for pattern in [
            r"pub\s+struct\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"pub\s+enum\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"pub\s+trait\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"pub\s+type\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]:
            items.extend(re.findall(pattern, content))
        seen = set()
        ordered = []
        for item in items:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _rebuild_lib_rs(self):
        src_dir = os.path.join(self.project_path, "src")
        if not os.path.isdir(src_dir):
            return
        modules = []
        export_candidates: Dict[str, List[str]] = {}
        for name in sorted(os.listdir(src_dir)):
            if not name.endswith(".rs") or name == "lib.rs":
                continue
            module_name = name[:-3]
            modules.append(module_name)
            try:
                with open(os.path.join(src_dir, name), "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = ""
            for item in self._extract_public_items(content):
                export_candidates.setdefault(item, []).append(module_name)

        lines = ["//! 自动重建的 crate 入口。", ""]
        for module_name in modules:
            lines.append(f"pub mod {module_name};")

        unique_reexports = []
        for item, owners in sorted(export_candidates.items()):
            if len(owners) == 1:
                unique_reexports.append(f"pub use {owners[0]}::{item};")

        if unique_reexports:
            lines.append("")
            lines.extend(unique_reexports)
        content = "\n".join(lines).rstrip() + "\n"
        self._write_file(os.path.join(src_dir, "lib.rs"), content, rel_path="src/lib.rs")

    def _build_readme_fallback(self) -> str:
        return f"# {self.project_name}\n\n自动生成的 Rust 项目。\n"

    def _build_file_prompt(self, rel_path: str, context: str, generated_context: str) -> str:
        extra = ""
        if rel_path.startswith("src/"):
            extra = (
                "\nAdditional requirements:\n"
                "- Implement only the core functionality; do not expand into large-project capabilities.\n"
                "- Do not use thiserror.\n"
                "- Do not depend on crate::error or a custom prelude in the root module.\n"
                "- Keep module interfaces as simple, stable, and Rust-style as possible.\n"
                "- If tests are disabled by configuration, do not generate #[cfg(test)] modules.\n"
            )
        elif rel_path.lower() == "readme.md":
            extra = (
                "\nAdditional requirements:\n"
                "- This is a Markdown document, not Rust source code.\n"
                "- Do not paste full implementation source.\n"
                "- Keep only the introduction, build instructions, a minimal example, and the current status.\n"
            )
        return f"""Generate the final content for only the file below.

Target file:
{rel_path}

Summary of already generated files:
{generated_context}

Input document context:
{context}
{extra}

Output only the final content for this file; do not explain.
"""

    def _generate_regular_file(self, rel_path: str, context: str, generated_context: str) -> str:
        code_lang = "rust" if rel_path.endswith(".rs") else ""
        if rel_path.lower() == "readme.md":
            code_lang = ""
        return self._generate_with_continuation(
            system_prompt="You are a Rust rewrite assistant responsible for generating final content file by file. Output only the target file content.",
            user_prompt=self._build_file_prompt(rel_path, context, generated_context),
            code_lang=code_lang,
            label=f"代码生成 {rel_path}",
            max_rounds=4,
        )

    def _write_file(self, full_path: str, content: str, rel_path: Optional[str] = None):
        rel = rel_path or os.path.relpath(full_path, self.project_path).replace("\\", "/")
        sanitized = self._sanitize_file_content_before_write(rel, content)
        if not sanitized.strip():
            print(f"跳过空内容文件：{rel}")
            self._set_plan_file_state(rel, "failed", note="empty-content")
            return
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(sanitized)
        print(f"写入文件：{full_path}")
        if rel not in self.generated_files:
            self.generated_files.append(rel)
        self._set_plan_file_state(rel, "completed", sanitized)

    def check_project(self) -> bool:
        result = self._run_command(f'cd "{self.project_path}" && cargo check')
        success = result is None
        if success:
            print("代码检查通过")
        else:
            print(f"代码检查失败：{result}")
        return success

    def generate_code(self) -> List[str]:
        context = self._collect_context()
        files = self._generate_file_plan()
        files = self._sort_files_for_generation(files)
        print(f"最终生成顺序: {files}")

        cargo_toml = self._build_minimal_cargo_toml()
        self._write_file(os.path.join(self.project_path, "Cargo.toml"), cargo_toml, rel_path="Cargo.toml")

        generated_context = ""
        for rel_path in files:
            if rel_path == "Cargo.toml":
                continue
            if self._is_already_completed(rel_path):
                print(f"跳过已完成文件：{rel_path}")
                continue

            full_path = os.path.join(self.project_path, rel_path)
            print(f"生成文件：{rel_path}")
            content = self._generate_regular_file(rel_path, context, generated_context)
            if rel_path.lower() == "readme.md" and not content.strip():
                content = self._build_readme_fallback()
            self._write_file(full_path, content, rel_path=rel_path)

            if rel_path.endswith(".rs"):
                deps = self._detect_dependencies(content)
                if deps:
                    self._update_cargo_toml(deps)
            if rel_path.lower() != "readme.md":
                generated_context += f"\n\n=== {rel_path} ===\n{(content or '')[:5000]}\n"

        self._rebuild_lib_rs()
        return self.generated_files

    def generate_from_docs(self, project_name: str, output_dir: str, doc_paths: List[str]) -> bool:
        self.project_name = project_name
        self.load_documents(doc_paths)
        self.create_rust_project(project_name, output_dir)
        self.generated_files = []
        try:
            self.generate_code()
            return True
        except Exception as e:
            print(f"Rust 代码生成失败：{e}")
            return False
