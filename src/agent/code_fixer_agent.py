import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple


sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config
from utils.cmd import run
from config.prompt import prompt_manager
from llm.model import Model


class Fixer:
    """代码修复父类 - 提供通用的修复功能"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 5, error_organizer_agent=None):
        """
        初始化修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        self.llm = Model(config)

        self.project_path = project_path
        self.max_iterations = max_iterations
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
    
    def _extract_code(self, code: str) -> str:
        """
        从 LLM 响应中提取代码（去除 markdown 标记）
        
        Args:
            code: 包含 markdown 标记的代码字符串
            
        Returns:
            纯代码字符串
        """
        if '```rust' in code:
            code = code.split('```rust')[1].split('```')[0].strip()
        elif '```' in code:
            code = code.split('```')[1].split('```')[0].strip()
        return code
    
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

        file_content = self._read_file_content(file_path)
        if file_content is None:
            return False
        
        prompt = self._generate_fix_prompt(error_type, error_message, file_content)
        
        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        fixed_code = response[0]
        
        fixed_code = self._extract_code(fixed_code)
        
        return self._write_file_content(file_path, fixed_code)

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
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
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
    ) -> str:
        """
        生成函数级修复提示。
        """
        relative_path = os.path.relpath(file_path, self.project_path).replace("\\", "/")
        return f"""你是一个 Rust 代码修复专家。请只修复下面这个文件中的一个函数。

错误类型：
{error_type}

错误信息：
{error_message}

文件路径：
{relative_path}

目标函数代码：
```rust
{function_code}
```

相关文件上下文：
```rust
{file_context}
```

要求：
1. 只返回修复后的“目标函数完整代码”，不要返回整个文件
2. 不要输出解释
3. 保持函数签名与周边结构尽量稳定，优先修复报错本身
4. 如果函数依赖同文件中的结构体、类型别名或辅助函数，请以当前文件上下文为准

请把结果放在 ```rust 代码块中返回。
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
        prompt = self._build_function_fix_prompt(
            file_path=file_path,
            error_type=error_type,
            error_message=error_message,
            file_context=file_context,
            function_code=function_code,
        )

        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]

        response = self.llm.generate(messages)
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
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 5, error_organizer_agent=None):
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

    def _parse_error_to_files(self, error_message: str) -> List[str]:
        """
        从报错信息中收集候选文件列表。

        相比只解析单个文件，这里会把报错中出现的多个文件都保留下来，
        供后续 LLM 在候选文件之间做判断。
        """
        candidates: List[str] = []

        for match in re.finditer(r'--> ([^:\n]+):(\d+):(\d+)', error_message):
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
            return f"目标文件：{rel_path}\n\n{normalized_error}"

        location_text = ", ".join(f"{line}:{col}" for line, col in locations[:8])
        return (
            f"目标文件：{rel_path}\n"
            f"重点错误位置：{location_text}\n\n"
            f"{normalized_error}"
        )

    def _should_prefer_local_fix(self, iteration: int) -> bool:
        """
        前几轮优先局部修复，后几轮切换到整体修复。
        """
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
                f"=== 候选文件：{rel_path} ===\n{content}\n"
            )

        if not candidate_blocks:
            return False

        prompt = f"""下面是一次 Rust 项目修复任务。

错误类型：
{error_type}

编译器/格式化器报错：
{error_message}

候选文件内容：
{chr(10).join(candidate_blocks)}

请你判断最应该优先修改哪个文件。

输出格式必须严格如下：
<target_file>相对路径</target_file>

要求：
1. 只能选择上面给出的候选文件之一
2. 不要输出解释
"""

        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]

        response = self.llm.generate(messages)
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
        if self.error_organizer_agent is not None:
            if self._attempt_organized_fix(error_type, error_message, iteration):
                return True

        grouped_errors = self._group_errors_by_file(error_message)
        prefer_local = self._should_prefer_local_fix(iteration)

        if not grouped_errors:
            return False

        print(f"当前修复策略：{'局部优先' if prefer_local else '整体优先'}")

        # 逐个尝试目标文件，优先修复有定位信息的文件。
        grouped_errors.sort(key=lambda item: (0 if item.get("locations") else 1, item["file_path"]))
        for file_group in grouped_errors:
            file_path = file_group["file_path"]
            if not os.path.exists(file_path):
                continue

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
        prefer_local = self._should_prefer_local_fix(iteration)

        if not batches:
            return False

        print(f"错误已梳理为 {len(batches)} 个批次，当前修复策略：{'局部优先' if prefer_local else '整体优先'}")

        for batch in batches:
            diagnostics = batch.get("diagnostics", [])
            candidate_files = batch.get("candidate_files", [])
            if not diagnostics or not candidate_files:
                continue

            batch_error_message = "\n\n".join(diagnostics)
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
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 5, error_organizer_agent=None):
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
        return self._run_command(cmd)
    
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
        
        response = self.llm.generate(messages)
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
                if self._attempt_grouped_fix("test_compile", test_output, iteration):
                    self.fix_history.append({
                        'iteration': iteration,
                        'error': test_output,
                        'fixed': True,
                        'mode': 'test_compile'
                    })
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
