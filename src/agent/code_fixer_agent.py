import os
import re
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple


sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config
from utils.cmd import run
from config.prompt import prompt_manager
from llm.model import Model
from agent.alternatives.contextual_rust_agent import RustProjectRegistry


MAX_REPAIR_ITERATIONS = 20


class Fixer:
    """代码修复父类 - 提供通用的修复功能"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 10, error_organizer_agent=None):
        """
        初始化修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        self.config = config
        self.llm = Model(config)

        self.project_path = project_path
        self.max_iterations = max(1, min(int(max_iterations or MAX_REPAIR_ITERATIONS), MAX_REPAIR_ITERATIONS))
        self.fix_history = []
        self.error_organizer_agent = error_organizer_agent
    
    def _run_command(self, cmd: str) -> Tuple[bool, str]:
        """
        运行命令并返回结果
        
        Args:
            cmd: 命令字符串
            
        Returns:
            (是否成功，输出/错误信息)
        """
        result = run(cmd)
        success = result is None
        output = result if result is None else result.strip()
        return success, output

    def _generate_with_label(self, messages, label: str):
        """
        为流式输出附加请求标签，避免终端里出现 unnamed request。
        """
        if hasattr(self.llm, "set_request_label"):
            self.llm.set_request_label(label)
        return self.llm.generate(messages)
    
    def _extract_code(self, code: str) -> str:
        """
        从 LLM 响应中提取代码（去除 markdown 标记）
        
        Args:
            code: 包含 markdown 标记的代码字符串
            
        Returns:
            纯代码字符串
        """
        code = (code or "").strip()
        fence_match = re.match(r'^\s*```(?:[A-Za-z0-9_+-]+)?\s*\n?(.*)\n```\s*$', code, re.DOTALL)
        if fence_match:
            code = fence_match.group(1).strip()
            return code
        fence_search = re.search(r'(?ms)^\s*```(?:[A-Za-z0-9_+-]+)?[ \t]*\n(.*?)\n\s*```', code)
        if fence_search:
            return fence_search.group(1).strip()
        return code

    def _generation_plan_path(self) -> str:
        """
        RustAgent 生成计划文件路径。
        """
        return os.path.join(self.project_path, ".cgr_generation_plan.json")

    def _api_contract_path(self) -> str:
        """
        RustAgent 接口契约文件路径。
        """
        return os.path.join(self.project_path, ".cgr_api_contract.json")

    def _build_live_reference_summary(self, max_chars: int = 50000) -> str:
        """
        从当前 src/*.rs 实时重建引用表。

        旧项目的 .cgr_api_contract.json 可能没有 references 字段；repair 阶段
        不能因此丢失函数参数、字段和可见性信息。
        """
        src_dir = Path(self.project_path) / "src"
        if not src_dir.is_dir():
            return ""

        registry = RustProjectRegistry()
        for path in sorted(src_dir.rglob("*.rs")):
            try:
                rel_path = path.relative_to(self.project_path).as_posix()
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            registry.update_file(rel_path, content)
        if not registry.files:
            return ""
        return registry.summary(max_chars=max_chars)

    def _format_reference_summary(self, references: List[Dict], default_path: str = "") -> List[str]:
        lines = []
        for ref in references:
            kind = ref.get("kind", "")
            visibility = ref.get("visibility", "public")
            is_public = ref.get("public", visibility != "private")
            path = ref.get("path") or default_path
            owner = ref.get("owner_type", "")
            name = ref.get("name", "")
            signature = ref.get("signature", "")
            params = ref.get("params", []) or []
            if not isinstance(params, list):
                params = [str(params)]
            return_type = ref.get("return_type", "")
            display_name = f"{owner}::{name}" if owner else name

            if not signature:
                if kind in {"function", "method"}:
                    signature = f"{display_name}({', '.join(params)})"
                    if return_type:
                        signature += f" -> {return_type}"
                elif kind == "field":
                    signature = f"{display_name}: {return_type}" if return_type else display_name
                else:
                    signature = display_name
            params_text = "[" + ", ".join(str(item) for item in params) + "]"
            lines.append(
                "- reference "
                f"kind={kind}; "
                f"visibility={visibility}; "
                f"public={str(bool(is_public)).lower()}; "
                f"path={path or '(unknown)'}; "
                f"owner_type={owner or '(none)'}; "
                f"name={name}; "
                f"params={params_text}; "
                f"return_type={return_type or '(none)'}; "
                f"signature={signature}"
            )
        return lines

    def _load_api_contract_summary(self, max_chars: int = 50000) -> str:
        """
        读取精简接口契约摘要，供跨文件接口修复时参考。
        """
        contract_path = self._api_contract_path()
        contract = {}
        if os.path.exists(contract_path):
            try:
                with open(contract_path, 'r', encoding='utf-8') as f:
                    contract = json.load(f)
            except Exception as e:
                print(f"读取接口契约失败：{e}")

        parts = ["Current Rust interface contract summary:"]
        for rel_path, info in contract.get("files", {}).items():
            file_contract = info.get("contract", {})
            if not file_contract:
                continue
            parts.append(f"\n### {rel_path}")
            for struct in file_contract.get("public_structs", []):
                fields = ", ".join(
                    f"{f['name']}({'pub' if f['public'] else 'private'}:{f.get('type', '?')})"
                    for f in struct.get("fields", [])
                )
                parts.append(f"- struct {struct['name']}: {fields or 'no field information'}")
            for enum in file_contract.get("public_enums", []):
                parts.append(f"- enum {enum['name']}: {', '.join(enum.get('variants', []))}")
            if file_contract.get("constructors"):
                parts.append(f"- constructors: {', '.join(file_contract['constructors'])}")
            if file_contract.get("accessors"):
                parts.append(f"- accessors: {', '.join(file_contract['accessors'])}")
            if file_contract.get("public_functions"):
                parts.append(f"- public_functions: {', '.join(file_contract['public_functions'])}")
            references = file_contract.get("references") or info.get("references") or []
            if references:
                parts.append("- references (complete reference table; calls and field accesses must follow this):")
                parts.extend(self._format_reference_summary(references, default_path=rel_path))

        live_reference_summary = self._build_live_reference_summary(max_chars=max_chars)
        if live_reference_summary:
            parts.append("\n### Current live reference table for src/*.rs")
            parts.append(live_reference_summary)

        text = "\n".join(parts).strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[Interface contract summary truncated]"
        return text

    def _is_secondary_rustc_location(self, text: str, match_start: int) -> bool:
        prefix = text[:match_start]
        previous_lines = [line.strip().lower() for line in prefix.splitlines()[-4:] if line.strip()]
        if not previous_lines:
            return False
        marker_lines = previous_lines[-2:]
        return any(
            line.startswith(("note:", "help:", "warning:", "= note:", "= help:"))
            for line in marker_lines
        )

    def _looks_like_cross_file_interface_error(self, error_message: str) -> bool:
        """
        判断当前错误是否更像跨文件接口漂移问题。
        """
        normalized = (error_message or "").lower()
        markers = [
            "private field",
            "attempted to take value of method",
            "no function or associated item named",
            "no method named",
            "no variant or associated item named",
            "cannot find type",
            "cannot find struct",
            "cannot find enum",
            "cannot find trait",
            "function or associated item not found",
            "mismatched types",
            "this function takes",
            "arguments were supplied",
            "unexpected argument",
        ]
        return any(marker in normalized for marker in markers)

    def _mark_plan_file_failed(self, file_path: str, note: str = ""):
        """
        如果某个文件在 fmt/check/test 中再次命中错误，就把它从生成计划中降级为 failed，
        避免下次运行时仍被当成 completed 跳过。
        """
        plan_path = self._generation_plan_path()
        if not os.path.exists(plan_path):
            return

        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                plan = json.load(f)
        except Exception as e:
            print(f"加载生成计划失败，无法降级文件状态：{e}")
            return

        rel_path = file_path
        if os.path.isabs(file_path):
            try:
                rel_path = os.path.relpath(file_path, self.project_path).replace("\\", "/")
            except Exception:
                rel_path = file_path.replace("\\", "/")
        else:
            rel_path = file_path.replace("\\", "/")

        files_state = plan.setdefault("files", {})
        file_state = files_state.setdefault(rel_path, {})
        file_state["status"] = "failed"
        file_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if note:
            file_state["note"] = note
        file_state.pop("sha256", None)
        file_state.pop("size", None)
        plan["updated_at"] = datetime.now().isoformat(timespec="seconds")

        try:
            with open(plan_path, 'w', encoding='utf-8') as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            print(f"已将生成计划中的文件状态降级为 failed：{rel_path}")
        except Exception as e:
            print(f"保存生成计划失败，无法降级文件状态：{e}")
    
    def _fix_file(self, file_path: str, error_type: str, error_message: str, prefer_local: bool = True) -> bool:
        """
        修复单个文件
        
        Args:
            file_path: 文件路径
            error_type: 错误类型
            error_message: 错误信息
            
        Returns:
            是否成功修复
        """
        # 对 Rust 文件优先尝试局部修复；如果无法定位局部问题，再回退到整文件修复。
        if prefer_local and file_path.endswith(".rs"):
            if self._fix_rust_function(file_path, error_type, error_message):
                return True

        return self._fix_entire_file(file_path, error_type, error_message)

    def _fix_entire_file(self, file_path: str, error_type: str, error_message: str) -> bool:
        """
        整文件修复。

        用于：
        1. 局部修复失败后的兜底
        2. 后几轮主动切换到全局修复
        """
        if os.path.basename(file_path).lower() == "lib.rs":
            rebuilt = self._rebuild_minimal_lib_rs()
            if rebuilt:
                print("应用规则修复：本地重建最小 lib.rs 入口，避免整文件自由改写 crate 边界")
                return self._write_file_content(file_path, rebuilt)

        file_content = self._read_file_content(file_path)
        if file_content is None:
            return False
        
        prompt = self._generate_fix_prompt(error_type, error_message, file_content)
        api_contract_summary = self._load_api_contract_summary()
        if api_contract_summary:
            prompt += (
                "\n\nInterface contract summary (for cross-file interfaces, follow this first; do not invent another set of fields, constructors, or enum variants):\n"
                f"```text\n{api_contract_summary}\n```"
            )
        
        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self._generate_with_label(messages, f"整文件修复 {os.path.basename(file_path)}")
        fixed_code = response[0]
        
        fixed_code = self._extract_code(fixed_code)
        
        return self._write_file_content(file_path, fixed_code)

    def _rebuild_minimal_lib_rs(self) -> str:
        """
        基于当前 src 目录重建一个最小、稳定的 lib.rs。
        不在这里内联测试、README 文本或复杂 prelude，避免 crate 边界继续漂移。
        """
        src_dir = os.path.join(self.project_path, "src")
        if not os.path.isdir(src_dir):
            return ""

        module_files = []
        for name in os.listdir(src_dir):
            if not name.endswith(".rs") or name == "lib.rs":
                continue
            module_files.append(name[:-3])
        module_files = sorted(module_files)
        if not module_files:
            return ""

        export_candidates = {}
        for module_name in module_files:
            module_path = os.path.join(src_dir, f"{module_name}.rs")
            content = self._read_file_content(module_path) or ""
            for item in self._extract_public_exportable_items(content):
                export_candidates.setdefault(item, []).append(module_name)

        lines = ["//! 自动重建的 crate 入口。", ""]
        for module_name in module_files:
            lines.append(f"pub mod {module_name};")

        unique_reexports = []
        for item, owners in sorted(export_candidates.items()):
            if len(owners) == 1:
                unique_reexports.append(f"pub use {owners[0]}::{item};")

        if unique_reexports:
            lines.append("")
            lines.extend(unique_reexports)

        return "\n".join(lines).strip() + "\n"

    def _extract_public_exportable_items(self, content: str) -> List[str]:
        """
        只提取适合由 lib.rs 重导出的公开类型项，避免把函数全部提升到 crate 根。
        """
        items = []
        for pattern in [
            r"pub\s+struct\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"pub\s+enum\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"pub\s+trait\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"pub\s+type\s+([A-Za-z_][A-Za-z0-9_]*)",
        ]:
            items.extend(re.findall(pattern, content or ""))

        seen = set()
        ordered = []
        for item in items:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _read_file_content(self, file_path: str) -> Optional[str]:
        """
        统一读取文件内容。

        这里先保留文件级读取接口，后续如果要升级到函数级、符号级或 AST 级读取，
        只需要在这一层替换，不必改动上层修复流程。
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"读取文件失败：{e}")
            return None

    def _write_file_content(self, file_path: str, content: str) -> bool:
        """
        统一写入文件内容。

        这里先保留文件级写入接口，后续如果要升级到函数级回写、最小补丁回写，
        也可以集中在这一层演进。
        """
        try:
            normalized = file_path.replace("\\", "/").lower()
            if normalized.endswith(".rs"):
                content = self._extract_code(content)
                if (content or "").lstrip().startswith("```"):
                    content = self._extract_code(content)
            elif normalized.endswith("cargo.toml"):
                content = self._extract_code(content)
                if (content or "").lstrip().startswith("```"):
                    content = self._extract_code(content)
            content = (content or "").strip() + ("\n" if (content or "").strip() else "")

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            with open(file_path, 'r', encoding='utf-8') as f:
                written_back = f.read()
            if written_back != content:
                print(f"写回校验失败：{file_path}")
                return False
            print(f"已写入修复文件：{file_path}")
            return True
        except Exception as e:
            print(f"写入文件失败：{e}")
            return False

    def _strip_ansi(self, text: str) -> str:
        """
        移除命令行输出中的 ANSI 转义序列，便于后续做稳定解析。
        """
        return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text or '')

    def _extract_local_context_by_line(self, file_content: str, line_number: int, radius: int = 10) -> str:
        """
        按行号抽取局部上下文。
        """
        lines = file_content.splitlines()
        if not lines:
            return ""

        center = max(0, min(line_number - 1, len(lines) - 1))
        start = max(0, center - radius)
        end = min(len(lines), center + radius + 1)

        numbered_lines = []
        for index in range(start, end):
            numbered_lines.append(f"{index + 1}: {lines[index]}")
        return "\n".join(numbered_lines)

    def _normalize_error_message(self, error_message: str) -> str:
        """
        统一化错误输出，减少格式噪声对后续解析的影响。
        这个能力属于通用错误处理逻辑，CodeFixer 和 TestFixer 都会用到。
        """
        cleaned = self._strip_ansi(error_message).replace('\r\n', '\n').replace('\r', '\n')
        lines = [line.rstrip() for line in cleaned.splitlines()]
        normalized_lines = []
        previous_blank = False
        for line in lines:
            is_blank = not line.strip()
            if is_blank and previous_blank:
                continue
            normalized_lines.append(line)
            previous_blank = is_blank
        return "\n".join(normalized_lines).strip()

    def _group_errors_by_file(self, error_message: str) -> List[Dict]:
        """
        将错误按文件归类，并尽量保留行号信息。
        这个能力也下沉到父类，便于测试修复阶段复用。
        """
        normalized = self._normalize_error_message(error_message)
        grouped: Dict[str, Dict] = {}

        for match in re.finditer(r'--> ([^:\n]+):(\d+):(\d+)', normalized):
            if self._is_secondary_rustc_location(normalized, match.start()):
                continue
            file_path = match.group(1).strip()
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)

            if file_path not in grouped:
                grouped[file_path] = {
                    "file_path": file_path,
                    "locations": [],
                    "normalized_error": normalized,
                }

            location = (int(match.group(2)), int(match.group(3)))
            if location not in grouped[file_path]["locations"]:
                grouped[file_path]["locations"].append(location)

        if not grouped:
            for file_path in self._parse_error_to_files(normalized):
                grouped[file_path] = {
                    "file_path": file_path,
                    "locations": [],
                    "normalized_error": normalized,
                }

        return list(grouped.values())

    def _build_grouped_error_message(self, file_group: Dict) -> str:
        """
        给某个目标文件生成更聚焦的错误描述。
        """
        file_path = file_group["file_path"]
        rel_path = os.path.relpath(file_path, self.project_path).replace("\\", "/")
        locations = file_group.get("locations", [])
        normalized_error = file_group.get("normalized_error", "")

        if not locations:
            return f"Target file: {rel_path}\n\n{normalized_error}"

        location_text = ", ".join(f"{line}:{col}" for line, col in locations[:8])
        return (
            f"Target file: {rel_path}\n"
            f"Key error locations: {location_text}\n\n"
            f"{normalized_error}"
        )

    def _parse_error_location(self, error_message: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """
        从报错中提取文件、行号、列号。
        """
        match = re.search(r'--> ([^:\n]+):(\d+):(\d+)', error_message)
        if not match:
            return None, None, None

        file_path = match.group(1).strip()
        if not os.path.isabs(file_path):
            file_path = os.path.join(self.project_path, file_path)

        return file_path, int(match.group(2)), int(match.group(3))

    def _locate_rust_function_bounds(self, file_content: str, line_number: int) -> Optional[Tuple[int, int, str]]:
        """
        根据报错行号，在 Rust 文件中定位最相关的函数文本范围。

        返回：(起始字符索引, 结束字符索引, 函数代码)
        """
        lines = file_content.splitlines(keepends=True)
        if not lines:
            return None

        target_index = max(0, min(line_number - 1, len(lines) - 1))
        func_pattern = re.compile(
            r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+"[^"]+"\s+)?fn\s+[A-Za-z_][A-Za-z0-9_]*'
        )

        start_line = None
        for index in range(target_index, -1, -1):
            if func_pattern.search(lines[index]):
                start_line = index
                break

        if start_line is None:
            return None

        start_offset = sum(len(line) for line in lines[:start_line])
        search_text = "".join(lines[start_line:])
        open_brace_index = search_text.find("{")
        if open_brace_index == -1:
            return None

        absolute_open_brace = start_offset + open_brace_index
        brace_depth = 0
        end_offset = None
        for index in range(absolute_open_brace, len(file_content)):
            char = file_content[index]
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    end_offset = index + 1
                    break

        if end_offset is None:
            return None

        function_code = file_content[start_offset:end_offset]
        return start_offset, end_offset, function_code

    def _build_function_fix_prompt(
        self,
        file_path: str,
        error_type: str,
        error_message: str,
        file_context: str,
        function_code: str,
        api_contract_summary: str = "",
    ) -> str:
        """
        生成函数级修复提示。
        """
        relative_path = os.path.relpath(file_path, self.project_path).replace("\\", "/")
        return f"""You are a Rust code repair expert. Repair only one function in the file below.

Error type:
{error_type}

Error message:
{error_message}

File path:
{relative_path}

Target function code:
```rust
{function_code}
```

Relevant file context:
```rust
{file_context}
```

Interface contract summary:
```text
{api_contract_summary}
```

Requirements:
1. Return only the complete repaired target function code; do not return the whole file.
2. Do not output explanations.
3. Keep the function signature and surrounding structure as stable as possible, and prioritize fixing the reported error itself.
4. If the function depends on structs, type aliases, or helper functions in the same file, follow the current file context.
5. If the error clearly involves a cross-file interface, follow the interface contract summary first and do not reinvent field names, constructor names, or enum variants.

Return the result in a ```rust code block.
"""

    def _extract_rust_supporting_context(
        self,
        file_content: str,
        function_start: int,
        function_end: int,
        error_message: str = "",
    ) -> str:
        """
        为函数级修复提取轻量上下文，避免把整文件全文都发给 LLM。

        当前策略：
        1. 提取顶部 use / extern crate / type 定义
        2. 优先提取与目标函数更相关的 struct / enum / trait / impl / type 头部行
        3. 提取目标函数附近少量前后文
        """
        lines = file_content.splitlines()
        if not lines:
            return ""

        start_line = file_content[:function_start].count("\n")
        end_line = file_content[:function_end].count("\n")

        function_code = file_content[function_start:function_end]
        related_identifiers = self._extract_related_identifiers(function_code)
        error_identifiers = self._extract_identifiers_from_error(error_message)
        related_identifiers = list(dict.fromkeys(related_identifiers + error_identifiers))

        import_lines = []
        definition_lines = []
        prioritized_definition_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("use ") or stripped.startswith("pub use ") or stripped.startswith("extern crate "):
                import_lines.append(line)
                continue
            if re.match(r'^\s*(?:pub\s+)?(?:struct|enum|trait)\s+\w+', line):
                definition_lines.append(line)
                if any(identifier in line for identifier in related_identifiers):
                    prioritized_definition_lines.append(line)
                continue
            if re.match(r'^\s*impl(?:<[^>]+>)?\s+', line):
                definition_lines.append(line)
                if any(identifier in line for identifier in related_identifiers):
                    prioritized_definition_lines.append(line)
                continue
            if re.match(r'^\s*(?:pub\s+)?type\s+\w+\s*=', line):
                definition_lines.append(line)
                if any(identifier in line for identifier in related_identifiers):
                    prioritized_definition_lines.append(line)

        context_start = max(0, start_line - 12)
        context_end = min(len(lines), end_line + 13)
        local_context = "\n".join(lines[context_start:context_end])

        parts = []
        if import_lines:
            parts.append("// 顶部导入\n" + "\n".join(import_lines[:40]))
        selected_definition_lines = prioritized_definition_lines or definition_lines
        if selected_definition_lines:
            parts.append("// 相关类型与实现头部\n" + "\n".join(selected_definition_lines[:30]))
        if local_context:
            parts.append("// 目标函数邻近上下文\n" + local_context)

        return "\n\n".join(parts)

    def _extract_related_identifiers(self, function_code: str) -> List[str]:
        """
        从目标函数中提取一批可能相关的标识符，用于筛选上下文。
        """
        candidates = re.findall(r'\b[A-Z][A-Za-z0-9_]*\b', function_code)
        seen = set()
        results = []
        for token in candidates:
            if token not in seen:
                seen.add(token)
                results.append(token)
        return results[:20]

    def _extract_identifiers_from_error(self, error_message: str) -> List[str]:
        """
        从报错文本中提取可能相关的符号名，用于辅助筛选上下文。
        """
        patterns = [
            r'`([A-Za-z_][A-Za-z0-9_]*)`',
            r"'([A-Za-z_][A-Za-z0-9_]*)'",
            r'\b[A-Z][A-Za-z0-9_]*\b',
        ]

        seen = set()
        results = []
        for pattern in patterns:
            for token in re.findall(pattern, error_message):
                if token not in seen:
                    seen.add(token)
                    results.append(token)

        return results[:20]

    def _extract_function_signature(self, function_code: str) -> Optional[str]:
        """
        提取函数签名的近似文本，用于替换前后的稳定性校验。
        """
        match = re.search(
            r'^\s*((?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+"[^"]+"\s+)?fn\s+[A-Za-z_][A-Za-z0-9_]*)',
            function_code,
            re.MULTILINE,
        )
        if not match:
            return None
        return match.group(1).strip()

    def _looks_like_complete_rust_function(self, function_code: str) -> bool:
        """
        轻量判断返回内容是否像一个完整的 Rust 函数。
        """
        if not function_code or "fn " not in function_code or "{" not in function_code or "}" not in function_code:
            return False

        brace_depth = 0
        for char in function_code:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth < 0:
                    return False

        return brace_depth == 0

    def _validate_fixed_function(self, original_function: str, fixed_function: str) -> bool:
        """
        对函数级修复结果做轻量校验，避免明显破坏结构。
        """
        if not self._looks_like_complete_rust_function(fixed_function):
            return False

        original_signature = self._extract_function_signature(original_function)
        fixed_signature = self._extract_function_signature(fixed_function)

        # 默认要求签名主干保持不变，避免模型把目标函数替换成完全不同的函数。
        if original_signature and fixed_signature and original_signature != fixed_signature:
            return False

        return True

    def _fix_rust_function(self, file_path: str, error_type: str, error_message: str) -> bool:
        """
        优先按函数粒度修复 Rust 文件。
        """
        file_content = self._read_file_content(file_path)
        if file_content is None:
            return False

        error_file, line_number, _ = self._parse_error_location(error_message)
        if error_file is not None and os.path.normpath(error_file) != os.path.normpath(file_path):
            return False
        if line_number is None:
            return False

        located = self._locate_rust_function_bounds(file_content, line_number)
        if located is None:
            return False

        start_offset, end_offset, function_code = located
        file_context = self._extract_rust_supporting_context(
            file_content=file_content,
            function_start=start_offset,
            function_end=end_offset,
            error_message=error_message,
        )
        api_contract_summary = self._load_api_contract_summary()
        prompt = self._build_function_fix_prompt(
            file_path=file_path,
            error_type=error_type,
            error_message=error_message,
            file_context=file_context,
            function_code=function_code,
            api_contract_summary=api_contract_summary,
        )

        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]

        response = self._generate_with_label(messages, f"函数级修复 {os.path.basename(file_path)}")
        fixed_function = self._extract_code(response[0])
        if not fixed_function:
            return False
        if not self._validate_fixed_function(function_code, fixed_function):
            print("函数级修复结果未通过基本校验，回退到文件级修复。")
            return False

        new_file_content = file_content[:start_offset] + fixed_function + file_content[end_offset:]
        return self._write_file_content(file_path, new_file_content)
    
    def _generate_fix_prompt(self, error_type: str, error_message: str, file_content: str = "") -> str:
        """
        生成修复提示（子类重写）
        
        Args:
            error_type: 错误类型
            error_message: 错误信息
            file_content: 文件内容
            
        Returns:
            提示字符串
        """
        return prompt_manager.get('code_fixer', 'generate_fix_prompt',
                                 error_type=error_type,
                                 error_message=error_message,
                                 file_content=file_content)
    
    def _get_system_prompt(self) -> str:
        """
        获取系统提示（子类重写）
        
        Returns:
            系统提示字符串
        """
        return prompt_manager.get('code_fixer', 'system_prompt')
    
    def fix(self) -> bool:
        """
        执行修复流程（子类重写）
        
        Returns:
            是否成功修复
        """
        raise NotImplementedError("子类必须实现此方法")


class CodeFixer(Fixer):
    """代码修复模块 - 根据格式化、检查、编译错误进行多轮代码修复"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 10, error_organizer_agent=None):
        """
        初始化代码修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        super().__init__(config, project_path, max_iterations, error_organizer_agent=error_organizer_agent)
    
    def _format_code(self) -> Tuple[bool, str]:
        """
        格式化代码
        
        Returns:
            (是否成功，cargo fmt 的输出)
        """
        cmd = f"cd {self.project_path} && cargo fmt"
        return self._run_command(cmd)
    
    def _check_code(self) -> Tuple[bool, str]:
        """
        检查代码
        
        Returns:
            (是否成功，cargo check 的输出)
        """
        cmd = f"cd {self.project_path} && cargo check"
        return self._run_command(cmd)
    
    def _build_code(self) -> Tuple[bool, str]:
        """
        编译代码
        
        Returns:
            (是否成功，cargo build 的输出)
        """
        cmd = f"cd {self.project_path} && cargo build"
        return self._run_command(cmd)
    
    def _parse_error_to_file(self, error_message: str) -> Optional[str]:
        """
        从错误信息中解析出有问题的文件路径
        
        Args:
            error_message: 错误信息（来自 cargo fmt/check/build 的输出）
                        例如："error: expected `;`, found `}`\n
                             --> src/lib.rs:10:5"
            
        Returns:
            文件路径或 None
        """
        # 匹配 Rust 错误信息中的文件路径模式：--> src/lib.rs:10:5
        match = re.search(r'--> ([^:]+):(\d+):(\d+)', error_message)
        if match:
            file_path = match.group(1)
            # 如果是相对路径，转换为绝对路径
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)
            return file_path
        return None

    def _remove_toml_array_table_blocks(self, content: str, table_name: str) -> str:
        """
        移除形如 [[example]] / [[bench]] 的整个数组表块。
        """
        lines = content.splitlines(keepends=True)
        kept = []
        i = 0
        target_header = f"[[{table_name}]]"
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped == target_header:
                i += 1
                while i < len(lines):
                    next_stripped = lines[i].strip()
                    if next_stripped.startswith("[[") or (next_stripped.startswith("[") and not next_stripped.startswith("[[")):
                        break
                    i += 1
                continue
            kept.append(lines[i])
            i += 1
        return "".join(kept)

    def _apply_rule_based_fix(self, error_type: str, error_message: str) -> bool:
        """
        在交给 LLM 之前，优先处理少数高收益、可规则化修复的问题。
        """
        if self._fix_missing_cargo_targets(error_message):
            return True
        if self._strip_inline_rust_tests(error_message):
            return True
        if self._fix_thiserror_residue(error_message):
            return True
        if self._fix_method_field_access(error_message):
            return True
        if self._fix_tree_root_visibility(error_message):
            return True
        if self._fix_node_state_machine_insert(error_message):
            return True
        if self._fix_callback_closure_api(error_message):
            return True
        if self._fix_recursive_borrow_patterns(error_message):
            return True
        return False

    def _strip_inline_test_modules_from_content(self, content: str) -> str:
        """
        移除 Rust 源文件中的 #[cfg(test)] mod tests。
        generate_tests=false 时，这些内联测试只会引入噪声，不应继续浪费修复轮次。
        """
        result = content or ""
        pattern = re.compile(r'(?m)^[ \t]*#\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*$')
        while True:
            cfg_match = pattern.search(result)
            if not cfg_match:
                break

            mod_match = re.search(r'(?m)^[ \t]*mod\s+tests\s*\{', result[cfg_match.end():])
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

    def _strip_inline_rust_tests(self, error_message: str) -> bool:
        """
        当 generate_tests=false 时，优先本地移除源文件中的内联测试模块。
        """
        if getattr(self.config, "generate_tests", False):
            return False

        candidate_files = [path for path in self._parse_error_to_files(error_message) if path.endswith(".rs")]
        if not candidate_files:
            return False

        changed_any = False
        for file_path in candidate_files:
            content = self._read_file_content(file_path)
            if content is None or "#[cfg(test)]" not in content:
                continue
            updated = self._strip_inline_test_modules_from_content(content)
            if updated != content:
                print(f"应用规则修复：移除内联 Rust 测试模块，用于 {os.path.relpath(file_path, self.project_path)}")
                if self._write_file_content(file_path, updated):
                    changed_any = True
        return changed_any

    def _fix_tree_root_visibility(self, error_message: str) -> bool:
        """
        处理 crate 内部模块访问 QuadTree.root 时的私有字段错误。
        这类字段属于内部结构，不需要交给模型自由设计。
        """
        normalized = self._normalize_error_message(error_message).lower()
        if (
            "field `root` of struct `quadtree` is private" not in normalized
            and "field `root` of struct `quadtree" not in normalized
        ):
            return False

        tree_path = os.path.join(self.project_path, "src", "tree.rs")
        content = self._read_file_content(tree_path)
        if content is None:
            return False

        updated = re.sub(
            r'(^\s*)root\s*:\s*',
            r'\1pub(crate) root: ',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if updated == content:
            return False

        print(f"应用规则修复：放宽 QuadTree.root 为 crate 内可见，用于 {os.path.relpath(tree_path, self.project_path)}")
        return self._write_file_content(tree_path, updated)

    def _fix_thiserror_residue(self, error_message: str) -> bool:
        """
        清理残留的 thiserror 派生和属性。
        当项目未声明 thiserror 依赖时，这类派生属于易错包装问题，优先本地清洗。
        """
        normalized = self._normalize_error_message(error_message).lower()
        if "use of undeclared crate or module `thiserror`" not in normalized and "cannot find attribute `error` in this scope" not in normalized:
            return False

        candidate_files = [path for path in self._parse_error_to_files(error_message) if path.endswith(".rs")]
        if not candidate_files:
            candidate_files = [os.path.join(self.project_path, "src", "tree.rs"), os.path.join(self.project_path, "src", "lib.rs")]

        changed_any = False
        for file_path in candidate_files:
            if not os.path.exists(file_path):
                continue
            content = self._read_file_content(file_path)
            if content is None:
                continue

            updated = content
            updated = re.sub(r',?\s*thiserror::Error', '', updated)
            updated = re.sub(r'(?m)^[ \t]*#\[\s*error\([^\]]*\)\s*\]\s*\n?', '', updated)
            updated = re.sub(r'(?m)^[ \t]*use\s+thiserror::Error;\s*\n?', '', updated)
            updated = re.sub(r'\#\[derive\(([^)]*)\)\]', lambda m: self._clean_derive_list(m.group(1)), updated)

            if updated != content:
                print(f"应用规则修复：清理 thiserror 残留，用于 {os.path.relpath(file_path, self.project_path)}")
                if self._write_file_content(file_path, updated):
                    changed_any = True
        return changed_any

    def _clean_derive_list(self, derive_body: str) -> str:
        parts = [part.strip() for part in derive_body.split(",") if part.strip()]
        parts = [part for part in parts if part != "thiserror::Error" and part != "Error"]
        if not parts:
            return ""
        return "#[derive(" + ", ".join(parts) + ")]"

    def _fix_node_state_machine_insert(self, error_message: str) -> bool:
        """
        修复 node.rs 中 try_insert 的高频状态机错误：
        模型常把 `match &mut self.state` 写成既借用旧 leaf，又尝试把值转移进子节点。
        这里统一改成先 `replace` 再匹配，避免 K / &mut K 混用。
        """
        normalized = self._normalize_error_message(error_message).lower()
        markers = [
            "expected `&mut _`, found type parameter",
            "found array `[box<node<&mut",
            "found array `[box<node<&mut k>>; 4]`",
        ]
        if not any(marker in normalized for marker in markers):
            return False

        node_path = os.path.join(self.project_path, "src", "node.rs")
        content = self._read_file_content(node_path)
        if content is None:
            return False

        replacement = """pub fn try_insert(
        &mut self,
        point: Point,
        key: K,
        depth: usize,
    ) -> Result<(), InsertError> {
        if !self.bounds.contains_point(&point) {
            return Err(InsertError::OutOfBounds(point.x(), point.y()));
        }

        const MAX_DEPTH: usize = 64;
        if depth >= MAX_DEPTH {
            return Err(InsertError::RecursionDepthExceeded);
        }

        let current_state = std::mem::replace(&mut self.state, NodeState::Empty);
        match current_state {
            NodeState::Empty => {
                self.state = NodeState::Leaf { point, key };
                Ok(())
            }
            NodeState::Leaf { point: existing_point, key: existing_key } => {
                let child_bounds = self.bounds.subdivide();
                let mut children = [
                    Box::new(Node::with_bounds(child_bounds[0].clone())),
                    Box::new(Node::with_bounds(child_bounds[1].clone())),
                    Box::new(Node::with_bounds(child_bounds[2].clone())),
                    Box::new(Node::with_bounds(child_bounds[3].clone())),
                ];

                let existing_quadrant = Self::find_quadrant(&existing_point, &self.bounds);
                children[existing_quadrant].try_insert(existing_point, existing_key, depth + 1)?;

                let new_quadrant = Self::find_quadrant(&point, &self.bounds);
                children[new_quadrant].try_insert(point, key, depth + 1)?;

                self.state = NodeState::Pointer { children };
                Ok(())
            }
            NodeState::Pointer { mut children } => {
                let quadrant = Self::find_quadrant(&point, &self.bounds);
                let result = children[quadrant].try_insert(point, key, depth + 1);
                self.state = NodeState::Pointer { children };
                result
            }
        }
    }"""

        updated = self._replace_function_block(content, r'pub\s+fn\s+try_insert\s*\(', replacement)
        if updated == content:
            return False

        print(f"应用规则修复：重建 node.rs::try_insert 的状态机转移逻辑，用于 {os.path.relpath(node_path, self.project_path)}")
        return self._write_file_content(node_path, updated)

    def _fix_missing_cargo_targets(self, error_message: str) -> bool:
        """
        处理 Cargo.toml 中声明了 examples / benches，但对应文件未生成的问题。
        """
        normalized = self._normalize_error_message(error_message)
        if "couldn't read examples\\" not in normalized and "couldn't read benches\\" not in normalized:
            return False

        cargo_toml = os.path.join(self.project_path, "Cargo.toml")
        content = self._read_file_content(cargo_toml)
        if content is None:
            return False

        updated = content
        changed = False

        if "couldn't read examples\\" in normalized:
            new_updated = self._remove_toml_array_table_blocks(updated, "example")
            changed = changed or (new_updated != updated)
            updated = new_updated

        if "couldn't read benches\\" in normalized or "criterion" in normalized:
            new_updated = self._remove_toml_array_table_blocks(updated, "bench")
            changed = changed or (new_updated != updated)
            updated = new_updated
            cleaned = re.sub(r'(?m)^\s*criterion\s*=\s*".*?"\s*\n?', '', updated)
            changed = changed or (cleaned != updated)
            updated = cleaned

        if not changed:
            return False

        updated = re.sub(r'\n{3,}', '\n\n', updated).strip() + "\n"
        print("应用规则修复：清理 Cargo.toml 中缺失文件对应的 example/bench 配置")
        return self._write_file_content(cargo_toml, updated)

    def _extract_method_call_fixes(self, error_message: str) -> List[Tuple[str, int, str]]:
        """
        从编译错误中提取“字段/方法混用，应补成 getter()”的修复建议。
        只接受编译器已经明确给出同名方法存在的情况，避免过度猜测。
        """
        normalized = self._normalize_error_message(error_message)
        fixes: List[Tuple[str, int, str]] = []

        patterns = [
            re.compile(
                r'error\[E0616\]: field `(?P<name>[A-Za-z_][A-Za-z0-9_]*)`.*?'
                r'-->\s*(?P<file>[^:\n]+):(?P<line>\d+):\d+.*?'
                r'help:\s*a method `(?P=name)` also exists, call it with parentheses',
                re.DOTALL,
            ),
            re.compile(
                r'error\[E0615\]: attempted to take value of method `(?P<name>[A-Za-z_][A-Za-z0-9_]*)`.*?'
                r'-->\s*(?P<file>[^:\n]+):(?P<line>\d+):\d+.*?'
                r'help:\s*use parentheses to call the method',
                re.DOTALL,
            ),
        ]

        for pattern in patterns:
            for match in pattern.finditer(normalized):
                file_path = match.group("file").strip()
                if not os.path.isabs(file_path):
                    file_path = os.path.join(self.project_path, file_path)
                fixes.append((file_path, int(match.group("line")), match.group("name")))

        deduped = []
        seen = set()
        for item in fixes:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def _fix_method_field_access(self, error_message: str) -> bool:
        """
        规则修复一类高频 getter/field 混用错误：
        - point.x 其实应该是 point.x()
        - node.state 其实应该是 node.state()
        """
        fixes = self._extract_method_call_fixes(error_message)
        if not fixes:
            return False

        grouped: Dict[str, List[Tuple[int, str]]] = {}
        for file_path, line_no, name in fixes:
            if os.path.exists(file_path):
                grouped.setdefault(file_path, []).append((line_no, name))

        any_changed = False
        for file_path, items in grouped.items():
            content = self._read_file_content(file_path)
            if content is None:
                continue

            lines = content.splitlines()
            changed = False
            for line_no, method_name in items:
                index = line_no - 1
                if index < 0 or index >= len(lines):
                    continue
                original_line = lines[index]
                updated_line = re.sub(
                    rf'\.{re.escape(method_name)}\b(?!\s*\()',
                    f'.{method_name}()',
                    original_line,
                )
                if updated_line != original_line:
                    lines[index] = updated_line
                    changed = True

            if changed:
                print(f"应用规则修复：将字段访问改为 getter 调用，用于 {os.path.relpath(file_path, self.project_path)}")
                if self._write_file_content(file_path, "\n".join(lines) + "\n"):
                    any_changed = True

        return any_changed

    def _replace_function_block(self, content: str, signature_pattern: str, replacement: str) -> str:
        """
        用简单的大括号配对替换某个顶层函数块。
        """
        match = re.search(signature_pattern, content, re.MULTILINE)
        if not match:
            return content

        start = match.start()
        open_brace = content.find("{", match.end() - 1)
        if open_brace == -1:
            return content

        depth = 0
        end = None
        for index in range(open_brace, len(content)):
            char = content[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break

        if end is None:
            return content

        return content[:start] + replacement + content[end:]

    def _fix_callback_closure_api(self, error_message: str) -> bool:
        """
        规则修复一类高频回调接口错误：
        1. 闭包里需要可变借用，但签名仍写成 Fn
        2. ControlFlow::Continue 缺少可推断类型，导致泛型推断失败

        这里不再绑定 walk.rs 文件名，而是根据错误模式和目标文件做局部规则修复。
        """
        normalized = self._normalize_error_message(error_message)
        markers = [
            "captured variable in a `fn` closure",
            "controlflow::continue",
            "expects `fn` instead of `fnmut`",
        ]
        lower = normalized.lower()
        if not any(marker in lower for marker in markers):
            return False

        candidate_files = [path for path in self._parse_error_to_files(normalized) if path.endswith(".rs")]
        if not candidate_files:
            fallback_walk = os.path.join(self.project_path, "src", "walk.rs")
            if os.path.exists(fallback_walk):
                candidate_files = [fallback_walk]
        if not candidate_files:
            return False

        target_path = candidate_files[0]
        content = self._read_file_content(target_path)
        if content is None:
            return False

        original = content

        # 1. 回调 trait bound：Fn -> FnMut
        content = re.sub(r'(\b[A-Z]\s*:\s*)Fn(\s*\()', r'\1FnMut\2', content)

        # 2. 顶层函数参数：descent/ascent/callback 等回调参数标成 mut
        content = re.sub(r'(\bpub\s+fn\s+[A-Za-z_][A-Za-z0-9_]*<[^>]*>\([^)]*?)\b(descent|ascent|callback|visitor|handler|predicate):\s*([A-Z])',
                         r'\1mut \2: \3', content)
        content = re.sub(r'(\bfn\s+[A-Za-z_][A-Za-z0-9_]*<[^>]*>\([^)]*?)\b(descent|ascent|callback|visitor|handler|predicate):\s*&([A-Z])',
                         r'\1\2: &mut \3', content)

        # 3. 常见递归/转发调用：&descent -> &mut descent
        content = re.sub(r'&\s*(descent|ascent|callback|visitor|handler|predicate)\b', r'&mut \1', content)

        # 4. 对仅作表达式返回的裸 Continue，补默认泛型；不碰 match arm。
        fixed_lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "ControlFlow::Continue" or stripped.endswith("ControlFlow::Continue"):
                if "=>" not in stripped and "::<" not in stripped:
                    line = line.replace("ControlFlow::Continue", "ControlFlow::<()>::Continue")
            fixed_lines.append(line)
        content = "\n".join(fixed_lines)

        if content == original:
            return False

        print(f"应用规则修复：统一回调接口中的 FnMut/ControlFlow，用于 {os.path.relpath(target_path, self.project_path)}")
        return self._write_file_content(target_path, content)

    def _remove_self_receiver_from_helper(self, content: str, fn_name: str) -> str:
        """
        将只依赖显式参数的内部 helper 从实例方法改成关联函数，
        避免出现 self.method(&mut self.field, ...) 的双重可变借用。
        """
        content = re.sub(
            rf'(fn\s+{re.escape(fn_name)}\s*\(\s*)&(?:mut\s+)?self\s*,\s*',
            r'\1',
            content,
        )
        content = re.sub(
            rf'\bself\.{re.escape(fn_name)}\s*\(',
            f'Self::{fn_name}(',
            content,
        )
        return content

    def _fix_recursive_borrow_patterns(self, error_message: str) -> bool:
        """
        规则修复两类高频递归借用问题：
        1. self.method(&mut self.root, ...) 导致的 E0499 双重可变借用
        2. 递归复用 Option<&mut FnMut(...)> 时，被第一次调用 move 掉
        """
        normalized = self._normalize_error_message(error_message)
        lower = normalized.lower()
        borrow_markers = [
            "cannot borrow `*self` as mutable more than once at a time",
            "cannot borrow `self.root` as mutable more than once at a time",
        ]
        moved_callback = "use of moved value: `key_free`" in lower

        if not any(marker in lower for marker in borrow_markers) and not moved_callback:
            return False

        tree_path = os.path.join(self.project_path, "src", "tree.rs")
        node_path = os.path.join(self.project_path, "src", "node.rs")

        changed_any = False

        if os.path.exists(tree_path):
            tree_content = self._read_file_content(tree_path)
            if tree_content is not None:
                updated_tree = tree_content

                # 这类内部递归 helper 不需要持有 self；改成关联函数即可避开 self/root 双借用。
                for helper_name in [
                    "insert_internal",
                    "split_node",
                    "find_internal",
                    "walk_internal",
                    "reset_node_internal",
                ]:
                    updated_tree = self._remove_self_receiver_from_helper(updated_tree, helper_name)

                # reset 入口保留原始 public API，但递归内部通过 &mut Option<_> 共享回调。
                updated_tree = re.sub(
                    r'pub\s+fn\s+reset\(\s*&mut\s+self\s*,\s*key_free:\s*Option<&mut\s+dyn\s+FnMut\(([^)]*)\)>\s*\)\s*\{\s*Self::reset_node_internal\(&mut\s+self\.root,\s*key_free\);\s*\}',
                    (
                        "pub fn reset(&mut self, key_free: Option<&mut dyn FnMut(\\1)>) {\n"
                        "        let mut key_free = key_free;\n"
                        "        Self::reset_node_internal(&mut self.root, &mut key_free);\n"
                        "    }"
                    ),
                    updated_tree,
                    flags=re.DOTALL,
                )
                updated_tree = re.sub(
                    r'(fn\s+reset_node_internal\s*\(\s*node:\s*&mut\s+Node\s*,\s*)key_free:\s*Option<&mut\s+dyn\s+FnMut\(([^)]*)\)>(\s*,?\s*\))',
                    r'\1key_free: &mut Option<&mut dyn FnMut(\2)>\3',
                    updated_tree,
                )

                if updated_tree != tree_content:
                    print(f"应用规则修复：收敛 tree.rs 中的递归借用模式，用于 {os.path.relpath(tree_path, self.project_path)}")
                    if self._write_file_content(tree_path, updated_tree):
                        changed_any = True

        if moved_callback and os.path.exists(node_path):
            node_content = self._read_file_content(node_path)
            if node_content is not None:
                updated_node = node_content
                updated_node = re.sub(
                    r'pub\s+fn\s+reset\(\s*&mut\s+self\s*,\s*key_free:\s*Option<&mut\s+dyn\s+FnMut\(([^)]*)\)>\s*\)',
                    r'pub fn reset(&mut self, key_free: &mut Option<&mut dyn FnMut(\1)>)',
                    updated_node,
                )
                updated_node = re.sub(
                    r'if\s+let\s+Some\(free_fn\)\s*=\s*key_free\s*\{\s*free_fn\(key\);\s*\}',
                    (
                        "if let Some(free_fn) = key_free.as_deref_mut() {\n"
                        "                free_fn(key);\n"
                        "            }"
                    ),
                    updated_node,
                    flags=re.DOTALL,
                )

                if updated_node != node_content:
                    print(f"应用规则修复：将 node.rs 中的回调复用改为借用共享，用于 {os.path.relpath(node_path, self.project_path)}")
                    if self._write_file_content(node_path, updated_node):
                        changed_any = True

        return changed_any

    def _parse_error_to_files(self, error_message: str) -> List[str]:
        """
        从报错信息中收集候选文件列表。

        相比只解析单个文件，这里会把报错中出现的多个文件都保留下来，
        供后续 LLM 在候选文件之间做判断。
        """
        candidates: List[str] = []

        for match in re.finditer(r'--> ([^:\n]+):(\d+):(\d+)', error_message):
            if self._is_secondary_rustc_location(error_message, match.start()):
                continue
            file_path = match.group(1).strip()
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)
            if os.path.exists(file_path) and file_path not in candidates:
                candidates.append(file_path)

        # 某些 Cargo.toml 报错不会带 --> 行号，这里额外兜底。
        if "Cargo.toml" in error_message:
            cargo_toml = os.path.join(self.project_path, "Cargo.toml")
            if os.path.exists(cargo_toml) and cargo_toml not in candidates:
                candidates.append(cargo_toml)

        return candidates

    def _normalize_error_message(self, error_message: str) -> str:
        """
        统一化错误输出，减少格式噪声对后续解析的影响。
        """
        cleaned = self._strip_ansi(error_message).replace('\r\n', '\n').replace('\r', '\n')
        lines = [line.rstrip() for line in cleaned.splitlines()]
        normalized_lines = []
        previous_blank = False
        for line in lines:
            is_blank = not line.strip()
            if is_blank and previous_blank:
                continue
            normalized_lines.append(line)
            previous_blank = is_blank
        return "\n".join(normalized_lines).strip()

    def _group_errors_by_file(self, error_message: str) -> List[Dict]:
        """
        将错误按文件归类，并尽量保留行号信息。
        """
        normalized = self._normalize_error_message(error_message)
        grouped: Dict[str, Dict] = {}

        for match in re.finditer(r'--> ([^:\n]+):(\d+):(\d+)', normalized):
            if self._is_secondary_rustc_location(normalized, match.start()):
                continue
            file_path = match.group(1).strip()
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)

            if file_path not in grouped:
                grouped[file_path] = {
                    "file_path": file_path,
                    "locations": [],
                    "normalized_error": normalized,
                }

            location = (int(match.group(2)), int(match.group(3)))
            if location not in grouped[file_path]["locations"]:
                grouped[file_path]["locations"].append(location)

        if not grouped:
            for file_path in self._parse_error_to_files(normalized):
                grouped[file_path] = {
                    "file_path": file_path,
                    "locations": [],
                    "normalized_error": normalized,
                }

        return list(grouped.values())

    def _build_grouped_error_message(self, file_group: Dict) -> str:
        """
        给某个目标文件生成更聚焦的错误描述。
        """
        file_path = file_group["file_path"]
        rel_path = os.path.relpath(file_path, self.project_path).replace("\\", "/")
        locations = file_group.get("locations", [])
        normalized_error = file_group.get("normalized_error", "")

        if not locations:
            return f"Target file: {rel_path}\n\n{normalized_error}"

        location_text = ", ".join(f"{line}:{col}" for line, col in locations[:8])
        return (
            f"Target file: {rel_path}\n"
            f"Key error locations: {location_text}\n\n"
            f"{normalized_error}"
        )

    def _should_prefer_local_fix(self, iteration: int, error_message: str = "") -> bool:
        """
        前几轮优先局部修复，后几轮切换到整体修复。
        """
        if self._looks_like_cross_file_interface_error(error_message):
            return False
        local_rounds = max(2, self.max_iterations // 2)
        return iteration <= local_rounds

    def _extract_target_file(self, response_text: str) -> Optional[str]:
        """
        从 LLM 返回中提取目标文件路径。
        """
        match = re.search(r'<target_file>\s*(.*?)\s*</target_file>', response_text, re.DOTALL)
        if not match:
            return None
        return match.group(1).strip()

    def _fix_from_candidates(
        self,
        error_type: str,
        error_message: str,
        candidate_files: List[str],
        prefer_local: bool = True,
    ) -> bool:
        """
        基于报错中的候选文件集合，让 LLM 决定优先修改哪个文件。

        这里仍然保持最小改动：一次只修改一个文件。
        但相比旧逻辑，LLM 至少可以在多个候选文件之间做判断，而不是完全依赖本地单文件解析。
        """
        existing_candidates = [path for path in candidate_files if os.path.exists(path)]
        if not existing_candidates:
            return False

        candidate_blocks = []
        for path in existing_candidates[:5]:
            content = self._read_file_content(path)
            if content is None:
                continue
            rel_path = os.path.relpath(path, self.project_path).replace("\\", "/")
            candidate_blocks.append(
                f"=== Candidate file: {rel_path} ===\n{content}\n"
            )

        if not candidate_blocks:
            return False

        api_contract_summary = self._load_api_contract_summary(max_chars=50000)
        contract_block = ""
        if api_contract_summary:
            contract_block = (
                "\n\nInterface contract and complete reference table:\n"
                "```text\n"
                f"{api_contract_summary}\n"
                "```\n"
            )

        prompt = f"""Below is a Rust project repair task.

Error type:
{error_type}

Compiler/formatter error:
{error_message}
{contract_block}

Candidate file content:
{chr(10).join(candidate_blocks)}

Decide which file should be modified first.

The output format must be exactly:
<target_file>relative/path</target_file>

Requirements:
1. Select only one of the candidate files given above.
2. Do not output explanations.
3. If the error is E0061 / no field / no method / argument count mismatch, prioritize the caller file where the error occurs; do not choose the callee definition file because of `note: associated function defined here`.
4. When judging whether a call is legal, you must read `params`, `return_type`, `owner_type`, `visibility`, and `signature` from the complete reference table.
5. If a call in a candidate file violates the reference-table signature, for example `Bounds::new` has `params=[]` but arguments are passed, select that candidate file to fix the call.
"""

        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]

        response = self._generate_with_label(messages, "候选文件选择")
        response_text = response[0]
        target_file = self._extract_target_file(response_text)

        resolved_target: Optional[str] = None
        if target_file:
            normalized_target = target_file.replace("\\", "/").strip()
            for candidate in existing_candidates:
                rel_candidate = os.path.relpath(candidate, self.project_path).replace("\\", "/")
                if normalized_target == rel_candidate or normalized_target == candidate.replace("\\", "/"):
                    resolved_target = candidate
                    break

        if resolved_target is None:
            resolved_target = existing_candidates[0]

        return self._fix_file(resolved_target, error_type, error_message, prefer_local=prefer_local)

    def _attempt_grouped_fix(self, error_type: str, error_message: str, iteration: int) -> bool:
        """
        统一处理一次报错修复尝试：
        1. 规范化错误
        2. 按文件归类
        3. 结合轮次选择局部修复或整体修复
        """
        if self._apply_rule_based_fix(error_type, error_message):
            return True

        if self.error_organizer_agent is not None:
            if self._attempt_organized_fix(error_type, error_message, iteration):
                return True

        grouped_errors = self._group_errors_by_file(error_message)
        prefer_local = self._should_prefer_local_fix(iteration, error_message)

        if not grouped_errors:
            return False

        print(f"当前修复策略：{'局部优先' if prefer_local else '整体优先'}")

        # 逐个尝试目标文件，优先修复有定位信息的文件。
        grouped_errors.sort(key=lambda item: (0 if item.get("locations") else 1, item["file_path"]))
        for file_group in grouped_errors:
            file_path = file_group["file_path"]
            if not os.path.exists(file_path):
                continue

            self._mark_plan_file_failed(file_path, f"{error_type}_reported")

            focused_error = self._build_grouped_error_message(file_group)
            if self._fix_from_candidates(
                error_type=error_type,
                error_message=focused_error,
                candidate_files=[file_path],
                prefer_local=prefer_local,
            ):
                return True

        return False

    def _attempt_organized_fix(self, error_type: str, error_message: str, iteration: int) -> bool:
        """
        可选的错误梳理路径：
        1. 先把长错误切成较小批次
        2. 每次只处理一批，降低单次喂给模型的错误密度
        3. 每批内仍沿用局部优先 / 整体优先的既有修复策略
        """
        batches = self.error_organizer_agent.organize_errors(error_message, self.project_path)
        prefer_local = self._should_prefer_local_fix(iteration, error_message)

        if not batches:
            return False

        print(f"错误已梳理为 {len(batches)} 个批次，当前修复策略：{'局部优先' if prefer_local else '整体优先'}")

        for batch in batches:
            diagnostics = batch.get("diagnostics", [])
            candidate_files = batch.get("candidate_files", [])
            if not diagnostics or not candidate_files:
                continue

            batch_error_message = "\n\n".join(diagnostics)
            batch_summary = str(batch.get("summary", "")).strip()
            batch_context = str(batch.get("context_text", "")).strip()
            if batch_summary:
                batch_error_message = f"{batch_summary}\n\n{batch_error_message}"
            if batch_context:
                batch_error_message = f"{batch_error_message}\n\nOrganized source context:\n{batch_context}"
            print(
                f"处理错误批次 {batch['batch_index']}/{len(batches)}："
                f"{len(diagnostics)} 条诊断，{len(candidate_files)} 个候选文件"
            )

            if self._fix_from_candidates(
                error_type=error_type,
                error_message=batch_error_message,
                candidate_files=candidate_files[:10],
                prefer_local=prefer_local,
            ):
                return True

        return False
    
    def fix(self) -> bool:
        """
        执行代码修复流程
        
        Returns:
            是否成功修复所有错误
        """
        print(f"开始代码修复，最大迭代次数：{self.max_iterations}")
        format_success = False
        check_success = False
        build_success = False
        
        print("1. 格式化代码...")
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮格式化 ===")
            
            format_success, format_output = self._format_code()
            print(f"格式化输出：{format_output}")
            if format_success:
                print("代码格式化通过")
                break
            else:
                print(f"格式化失败：{format_output}")
                if self._attempt_grouped_fix("format", format_output, iteration):
                    continue
                else:
                    print("无法定位需要格式化修复的文件")
                    self.fix_history.append({
                        'iteration': iteration,
                        'type': 'format',
                        'error': format_output,
                        'success': False
                    })
                    continue
        
        if not format_success:
            print("格式化代码失败，无法进行后续修复")
            return False

        print("2. 检查代码...")
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮check代码 ===")
            # 2. 检查代码
            check_success, check_output = self._check_code()
            print(f"检查输出：{check_output}")
            if check_success:
                print("代码检查通过")
                break
            else:
                print(f"代码检查失败：{check_output}")
                if self._attempt_grouped_fix("check", check_output, iteration):
                    continue
                else:
                    print("无法定位需要check修复的文件")
                    self.fix_history.append({
                        'iteration': iteration,
                        'type': 'check',
                        'error': check_output,
                        'success': False
                    })
                    continue
        
        if not check_success:
            print("检查代码失败，无法进行后续修复")
            return False


        print("3. 编译代码...")
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮编译 ===")
            build_success, build_output = self._build_code()
            if build_success:
                print("代码编译通过")
                self.fix_history.append({
                    'iteration': iteration,
                    'type': 'build',
                    'success': True
                })
                return True
            else:
                print(f"编译失败：{build_output}")
                if self._attempt_grouped_fix("build", build_output, iteration):
                    continue
                else:
                    print("无法定位需要build修复的文件")
                    self.fix_history.append({
                        'iteration': iteration,
                        'type': 'build',
                        'error': build_output,
                        'success': False
                    })
            
            # # 保存修复历史
            # self.fix_history.append({
            #     'iteration': iteration,
            #     'format_success': format_success,
            #     'check_success': check_success,
            #     'build_success': build_success
            # })
        
        print("\n达到最大迭代次数，build修复失败")
        return False


class TestFixer(Fixer):
    """代码测试修复模块 - 根据测试失败信息进行多轮修复"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 10, error_organizer_agent=None):
        """
        初始化测试修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        super().__init__(config, project_path, max_iterations, error_organizer_agent=error_organizer_agent)
    
    def _run_tests(self) -> Tuple[bool, str]:
        """运行测试"""
        cmd = f"cd {self.project_path} && cargo test"
        try:
            return self._run_command(cmd)
        except RuntimeError as e:
            # 测试阶段超时不应直接打断整个修复流程，而应作为一次失败结果交给后续策略处理。
            if str(e) == "Timeout":
                return False, "RuntimeError: Timeout"
            raise
    
    def _generate_fix_prompt(self, test_error: str, test_name: str, file_content: str = "") -> str:
        """
        生成测试修复提示
        
        Args:
            test_error: 测试错误信息
            test_name: 失败的测试名称
            file_content: 文件内容
            
        Returns:
            提示字符串
        """
        return prompt_manager.get('test_fixer', 'generate_fix_prompt',
                                 test_error=test_error,
                                 test_name=test_name,
                                 file_content=file_content)
    
    def _get_system_prompt(self) -> str:
        """获取系统提示"""
        return prompt_manager.get('test_fixer', 'system_prompt')
    
    def _parse_test_error(self, error_message: str) -> Tuple[Optional[str], str]:
        """
        从测试错误信息中解析出测试名称和文件路径
        
        Args:
            error_message: 错误信息
            
        Returns:
            (文件路径，测试名称)
        """
        # 匹配测试名称
        test_name_match = re.search(r'test (\S+) \.\.\. FAILED', error_message)
        test_name = test_name_match.group(1) if test_name_match else "unknown"

        # 对 cargo test 期间出现的编译错误，优先沿用通用编译错误归类逻辑，
        # 避免被某个无关文件或默认路径误导。
        grouped_errors = self._group_errors_by_file(error_message)
        if grouped_errors:
            grouped_errors.sort(key=lambda item: (0 if item.get("locations") else 1, item["file_path"]))
            file_path = grouped_errors[0]["file_path"]
            if os.path.exists(file_path):
                return file_path, test_name
        
        # 匹配文件路径
        match = re.search(r'--> ([^:]+):(\d+):(\d+)', error_message)
        if match:
            file_path = match.group(1)
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)
            return file_path, test_name
        
        # 尝试从 src/lib.rs 或 src/main.rs 中查找
        default_paths = [
            os.path.join(self.project_path, 'src', 'lib.rs'),
            os.path.join(self.project_path, 'src', 'main.rs')
        ]
        
        for path in default_paths:
            if os.path.exists(path):
                return path, test_name
        
        return None, test_name

    def _looks_like_test_compile_error(self, error_message: str) -> bool:
        """
        判断 cargo test 失败是否本质上是编译错误，而不是测试断言失败。
        """
        normalized = self._normalize_error_message(error_message)
        compile_markers = [
            "error[E",
            "error:",
            "could not compile",
            "--> ",
            "warning: build failed",
        ]
        return any(marker in normalized for marker in compile_markers)
    
    def _extract_test_code(self, file_content: str, test_name: str) -> str:
        """
        从文件内容中提取测试相关代码
        
        Args:
            file_content: 文件内容
            test_name: 测试名称
            
        Returns:
            测试相关代码
        """
        # 查找测试函数
        pattern = r'#\[test\]\s*(?:fn\s+' + re.escape(test_name.split('::')[-1]) + r'[\s\S]*?\})'
        match = re.search(pattern, file_content)
        if match:
            return match.group(0)
        
        # 如果没有找到特定测试，返回整个文件
        return file_content
    
    def _fix_file(self, file_path: str, test_error: str, test_name: str) -> bool:
        """
        修复单个文件
        
        Args:
            file_path: 文件路径
            test_error: 测试错误信息
            test_name: 测试名称
            
        Returns:
            是否成功修复
        """
        # 读取文件内容
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except Exception as e:
            print(f"读取文件失败：{e}")
            return False
        
        # 提取测试相关代码
        test_code = self._extract_test_code(file_content, test_name)
        
        prompt = self._generate_fix_prompt(test_error, test_name, test_code)
        
        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self._generate_with_label(messages, f"测试修复 {os.path.basename(file_path)}")
        fixed_code = response[0]
        
        fixed_code = self._extract_code(fixed_code)
        
        # 写入修复后的代码
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(fixed_code)
            return True
        except Exception as e:
            print(f"写入文件失败：{e}")
            return False
    
    def fix(self) -> bool:
        """
        执行测试修复流程
        
        Returns:
            是否所有测试通过
        """
        print(f"开始测试修复，最大迭代次数：{self.max_iterations}")
        
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮测试修复 ===")
            
            # 运行测试
            test_success, test_output = self._run_tests()
            
            if test_success:
                print("所有测试通过！")
                self.fix_history.append({
                    'iteration': iteration,
                    'success': True
                })
                return True
            
            print(f"测试失败：\n{test_output}")

            # cargo test 期间经常先暴露编译错误，此时不应按“测试函数修复”处理，
            # 而应复用代码修复器的按文件归类 + 局部/整体切换策略。
            if self._looks_like_test_compile_error(test_output):
                print("检测到测试阶段本质上是编译错误，切换到按文件归类的代码修复模式")
                compile_fixer = CodeFixer(
                    self.config,
                    self.project_path,
                    max_iterations=self.max_iterations,
                    error_organizer_agent=self.error_organizer_agent,
                )
                if compile_fixer._attempt_grouped_fix("test_compile", test_output, iteration):
                    self.fix_history.append({
                        'iteration': iteration,
                        'error': test_output,
                        'fixed': True,
                        'mode': 'test_compile'
                    })
                    if iteration == self.max_iterations:
                        print("最后一轮已应用编译修复，立即补跑一次测试确认结果")
                        final_success, final_output = self._run_tests()
                        if final_success:
                            print("最后一轮补跑后，所有测试通过！")
                            return True
                        print(f"最后一轮补跑后仍失败：\n{final_output}")
                    continue
            
            # 解析测试错误
            file_path, test_name = self._parse_test_error(test_output)
            
            if file_path and os.path.exists(file_path):
                print(f"定位到失败测试：{test_name}，文件：{file_path}")
                if self._fix_file(file_path, test_output, test_name):
                    self.fix_history.append({
                        'iteration': iteration,
                        'test_name': test_name,
                        'file': file_path,
                        'error': test_output,
                        'fixed': True
                    })
                    if iteration == self.max_iterations:
                        print("最后一轮已应用测试修复，立即补跑一次测试确认结果")
                        final_success, final_output = self._run_tests()
                        if final_success:
                            print("最后一轮补跑后，所有测试通过！")
                            return True
                        print(f"最后一轮补跑后仍失败：\n{final_output}")
                    continue  # 修复后继续下一轮测试
            else:
                print(f"无法定位测试文件，测试名称：{test_name}")
            
            self.fix_history.append({
                'iteration': iteration,
                'test_name': test_name,
                'file': file_path,
                'error': test_output,
                'fixed': False
            })
        
        print("\n达到最大迭代次数，测试修复失败")
        return False
