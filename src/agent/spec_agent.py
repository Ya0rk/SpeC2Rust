import os
import sys
import json
import re
from pathlib import Path
from typing import List, Dict, Set, Tuple
from datetime import datetime

sys.path.append(str(Path(__file__).parent))

from parse.c_ast import CCodeAnalyzer
from utils.code_analyzer import CodeAnalyzer
from utils.document_generator import DocumentGenerator
from config.config import Config
from config.prompt import prompt_manager
from llm.model import Model
from agent.split import ModuleSplitter
from utils.spec import specify_init


class SpecAgent:
    """C 项目 Spec 文档生成 Agent - 使用分层聚类 + 语义细分方法"""
    # 以下常量主要用于控制 prompt 尺寸，避免本地模型在单次生成时吃到过长上下文。
    # 这些值不是业务语义的一部分，而是服务于“文档可生成、可汇总、可继续喂给下游模型”。
    TEMP = 9999999
    MAX_CONTEXT_CHARS = TEMP # 12000
    MAX_BATCH_CHARS = TEMP # 9000
    MAX_MODULE_ANALYSIS_CHARS =  TEMP # 1600
    MAX_CONSTITUTION_DOC_CHARS = TEMP # 2000
    MAX_INTERFACE_HEADERS = TEMP # 12
    MAX_INTERFACE_FUNCTIONS = TEMP # 20
    MAX_INTERFACE_STRUCTS = TEMP # 20
    
    def __init__(self, config: Config = None):
        """
        初始化 SpecAgent
        
        Args:
            config: 配置对象
        """
        # 加载配置
        self.llm = Model(config)
        
        # 初始化工具
        self.parser = CCodeAnalyzer()
        self.analyzer = CodeAnalyzer(self.llm)
        self.doc_generator = DocumentGenerator(self.llm)
        
        # 初始化模块划分器
        self.module_splitter = ModuleSplitter()
        
        # 存储分析结果
        self.project_analysis = None
        self.repo_unit = None
        self.module_units = []
        self.file_units = []
        self.cluster_units = []
        self.dependency_graph = {}

    def _truncate_text(self, text: str, max_chars: int) -> str:
        # 简单的字符级裁剪器。这里不用 tokenizer，是为了保持依赖轻量且实现简单。
        # 可优化点：如果后续要更精细地控制上下文预算，可以改成 token 级裁剪，
        # 并按标题、列表、代码块边界截断，减少把语义片段从中间截开的情况。
        if not text or len(text) <= max_chars:
            return text or ""
        return text[:max_chars].rstrip() + "\n...[truncated]"

    def _chunk_blocks(self, blocks: List[str], max_chars: int) -> List[str]:
        # 把多个文本块按近似字符预算分批，供多轮 LLM 汇总使用。
        chunks = []
        current_blocks = []
        current_size = 0

        for block in blocks:
            block_size = len(block)
            if current_blocks and current_size + block_size + 2 > max_chars:
                chunks.append("\n\n".join(current_blocks))
                current_blocks = [block]
                current_size = block_size
                continue

            current_blocks.append(block)
            current_size += block_size + (2 if current_blocks else 0)

        if current_blocks:
            chunks.append("\n\n".join(current_blocks))

        return chunks

    def _normalize_struct_entries(self, structs: List) -> List[Dict]:
        # 接口文档阶段统一把结构体条目变成 dict，避免后续 prompt 拼接逻辑分支过多。
        normalized = []
        for struct in structs:
            if isinstance(struct, dict):
                record = dict(struct)
                if not record.get("file"):
                    record["file"] = record.get("filename", "unknown")
                if not record.get("start_line"):
                    record["start_line"] = record.get("startLine", 0)
                if not record.get("end_line"):
                    record["end_line"] = record.get("endLine", 0)
                normalized.append(record)
            elif isinstance(struct, str):
                normalized.append({"name": struct, "file": "unknown"})
        return normalized

    def _collect_headers_for_module(self, module: Dict, project_analysis: Dict) -> List[str]:
        # module_units 本身主要是从 .c 文件划出来的，这里额外把同目录头文件挂到模块接口文档上。
        file_map = project_analysis.get("file_path_map", {})
        module_dir = module.get("directory", "root")
        headers = []

        for rel_path in file_map.keys():
            if not rel_path.endswith(".h"):
                continue
            header_dir = os.path.dirname(rel_path) or "root"
            if header_dir == module_dir:
                headers.append(rel_path)

        return sorted(headers)

    def _normalize_path(self, path: str) -> str:
        return (path or "").replace("\\", "/")

    def _format_source_location(self, file_path: str, start_line: int = 0, end_line: int = 0) -> str:
        normalized = self._normalize_path(file_path) or "unknown"
        start = int(start_line or 0)
        end = int(end_line or 0)

        if start > 0 and end > 0 and end != start:
            return f"[{normalized}:{start}-{end}]"
        if start > 0:
            return f"[{normalized}:{start}]"
        return f"[{normalized}]"

    def _collapse_whitespace(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def _trim_inline(self, text: str, max_chars: int = 200) -> str:
        collapsed = self._collapse_whitespace(text)
        if len(collapsed) <= max_chars:
            return collapsed
        return collapsed[: max_chars - 3].rstrip() + "..."

    def _extract_declaration_excerpt(self, source: str, terminator: str = ";", max_chars: int = 220) -> str:
        if not source:
            return ""

        snippet = source.split("{", 1)[0].strip()
        if not snippet:
            snippet = source.strip().splitlines()[0]

        snippet = self._collapse_whitespace(snippet)
        if snippet and terminator and not snippet.endswith(terminator):
            snippet += terminator

        if len(snippet) <= max_chars:
            return snippet
        return snippet[: max_chars - 3].rstrip() + "..."

    def _build_directory_summary(self, files: List[str]) -> List[Tuple[str, List[str]]]:
        grouped = {}
        for rel_path in sorted(self._normalize_path(path) for path in files):
            directory = os.path.dirname(rel_path) or "root"
            grouped.setdefault(directory, []).append(rel_path)
        return sorted(grouped.items(), key=lambda item: item[0])

    def _resolve_header_reference(self, include_name: str, header_files: List[str]) -> str:
        normalized = self._normalize_path(include_name)
        if not normalized:
            return ""

        header_set = set(header_files)
        if normalized in header_set:
            return normalized

        basename = os.path.basename(normalized)
        matches = [header for header in header_files if os.path.basename(header) == basename]
        if len(matches) == 1:
            return matches[0]

        stem = os.path.splitext(basename)[0]
        stem_matches = [
            header for header in header_files
            if os.path.splitext(os.path.basename(header))[0] == stem
        ]
        if len(stem_matches) == 1:
            return stem_matches[0]

        return ""

    def _collect_module_header_records(self, module: Dict, project_analysis: Dict) -> List[Dict]:
        file_map = {
            self._normalize_path(rel_path): abs_path
            for rel_path, abs_path in project_analysis.get("file_path_map", {}).items()
        }
        header_files = sorted([path for path in file_map.keys() if path.endswith(".h")])
        observed = set(self._normalize_path(path) for path in module.get("headers", []))
        observed.update(self._collect_headers_for_module(module, project_analysis))

        include_graph = self.dependency_graph.get("include_graph", {})
        for module_file in module.get("files", []):
            normalized_file = self._normalize_path(module_file)
            for included in include_graph.get(normalized_file, []):
                resolved = self._resolve_header_reference(included, header_files)
                if resolved:
                    observed.add(resolved)

        for func in module.get("functions", []):
            func_file = self._normalize_path(func.get("file", ""))
            if not func_file:
                continue
            stem = os.path.splitext(os.path.basename(func_file))[0]
            stem_matches = [
                header for header in header_files
                if os.path.splitext(os.path.basename(header))[0] == stem
            ]
            if len(stem_matches) == 1:
                observed.add(stem_matches[0])

        records = []
        for header in sorted(path for path in observed if path):
            records.append(
                {
                    "path": header,
                    "location": self._format_source_location(header),
                    "absolute_path": file_map.get(header, ""),
                }
            )

        return records[:self.MAX_INTERFACE_HEADERS]

    def _collect_module_macros(self, module: Dict, project_analysis: Dict, header_records: List[Dict]) -> List[Dict]:
        relevant_files = {self._normalize_path(path) for path in module.get("files", [])}
        relevant_files.update(self._normalize_path(item["path"]) for item in header_records)
        macros = []

        for macro in project_analysis.get("macros", []):
            file_path = self._normalize_path(macro.get("filename", ""))
            if file_path not in relevant_files:
                continue
            macros.append(
                {
                    "name": macro.get("name", "unknown"),
                    "file": file_path,
                    "start_line": macro.get("startLine", 0),
                    "end_line": macro.get("endLine", 0),
                    "source": macro.get("source", ""),
                }
            )

        macros.sort(key=lambda item: (item["file"], item["start_line"], item["name"]))
        return macros[:20]

    def _collect_module_globals(self, module: Dict, project_analysis: Dict) -> List[Dict]:
        relevant_files = {self._normalize_path(path) for path in module.get("files", [])}
        globals_list = []

        for variable in project_analysis.get("global_vars", []):
            file_path = self._normalize_path(variable.get("filename", ""))
            if file_path not in relevant_files:
                continue
            globals_list.append(
                {
                    "name": variable.get("var_name", "unknown"),
                    "file": file_path,
                    "start_line": variable.get("startLine", 0),
                    "end_line": variable.get("endLine", 0),
                    "source": variable.get("source", ""),
                }
            )

        globals_list.sort(key=lambda item: (item["file"], item["start_line"], item["name"]))
        return globals_list[:20]

    def _collect_module_struct_records(self, module: Dict, project_analysis: Dict, header_records=None) -> List[Dict]:
        relevant_files = {self._normalize_path(path) for path in module.get("files", [])}
        if header_records:
            relevant_files.update(self._normalize_path(item["path"]) for item in header_records)

        struct_records = []
        for struct in project_analysis.get("structs", []):
            normalized = self._normalize_struct_entries([struct])[0]
            file_path = self._normalize_path(normalized.get("file", ""))
            if file_path not in relevant_files:
                continue
            struct_records.append(normalized)

        struct_records.sort(
            key=lambda item: (
                self._normalize_path(item.get("file", "")),
                int(item.get("start_line", 0) or 0),
                item.get("name", "unknown"),
            )
        )
        return struct_records[:self.MAX_INTERFACE_STRUCTS]

    def _collect_module_struct_references(self, module: Dict, struct_records: List[Dict]) -> List[str]:
        defined_names = {record.get("name") for record in struct_records if record.get("name")}
        referenced = []
        for item in module.get("structs", []):
            name = item.get("name") if isinstance(item, dict) else item
            if not name or name in defined_names:
                continue
            referenced.append(name)
        return sorted(set(referenced))[:20]

    def _build_function_fact_line(self, func: Dict) -> str:
        name = func.get("name", "unknown")
        file_path = self._normalize_path(func.get("file", "unknown"))
        start_line = func.get("start_line", func.get("startLine", 0))
        end_line = func.get("end_line", func.get("endLine", 0))
        signature = self._extract_declaration_excerpt(func.get("source", ""))
        return (
            f"- `{name}` {self._format_source_location(file_path, start_line, end_line)}: "
            f"`{signature or 'definition signature unavailable'}`"
        )

    def _build_struct_fact_line(self, struct: Dict) -> str:
        name = struct.get("name", "anonymous")
        file_path = self._normalize_path(struct.get("file", "unknown"))
        start_line = struct.get("start_line", struct.get("startLine", 0))
        end_line = struct.get("end_line", struct.get("endLine", 0))
        declaration = self._extract_declaration_excerpt(
            struct.get("source", ""),
            terminator="",
            max_chars=220,
        )
        return (
            f"- `{name}` {self._format_source_location(file_path, start_line, end_line)}: "
            f"`{declaration or 'definition excerpt unavailable'}`"
        )

    def _build_repo_manifest_content(self, project_info: Dict) -> str:
        project_name = project_info["project_name"]
        all_files = project_info.get("c_files", []) + project_info.get("h_files", []) + project_info.get("other_files", [])
        directory_summary = self._build_directory_summary(all_files)
        header_summary = self._build_directory_summary(project_info.get("h_files", []))
        # 可优化点：这里目前更偏“人类可读的仓库清单”。
        # 如果后续要增强给 Rust 生成端的消费能力，可以把入口文件、公共头文件、
        # 关键模块边界和构建目标额外沉淀为结构化字段，而不只是一份 markdown 概览。

        lines = [
            f"# {project_name} 仓库清单",
            "",
            "该文档只记录当前仓库扫描阶段直接观察到的事实，不补写缺失目录树，不猜测尚未出现的产物文件。",
            "",
            "## 快照",
            f"- 项目名称：`{project_name}`",
            f"- 构建系统：`{project_info.get('build_system', 'unknown')}`",
            f"- C 文件数：{len(project_info.get('c_files', []))}",
            f"- 头文件数：{len(project_info.get('h_files', []))}",
            f"- 其他文件数：{len(project_info.get('other_files', []))}",
            f"- 构建文件：{', '.join(project_info.get('build_files', [])) or '无'}",
            f"- 入口文件：{', '.join(project_info.get('entry_files', [])[:10]) or '无'}",
            f"- 仓库根目录下观察到的可执行文件：{', '.join(project_info.get('executables', [])) or '无'}",
            f"- 仓库根目录下观察到的库文件：{', '.join(project_info.get('libraries', [])) or '无'}",
            "",
            "## 目录清单",
        ]

        for directory, files in directory_summary:
            c_count = sum(1 for path in files if path.endswith(".c"))
            h_count = sum(1 for path in files if path.endswith(".h"))
            other_count = len(files) - c_count - h_count
            sample = ", ".join(files[:6])
            lines.append(
                f"- `{directory}`：共 {len(files)} 个文件"
                f"（{c_count} 个 C 文件，{h_count} 个头文件，{other_count} 个其他文件）。"
                f"示例：{sample}"
            )

        lines.extend(["", "## 源文件清单"])
        for source_file in project_info.get("c_files", []):
            entry_tag = "（入口候选）" if source_file in project_info.get("entry_files", []) else ""
            lines.append(f"- `{self._normalize_path(source_file)}`{entry_tag}")

        lines.extend(["", "## 按目录划分的头文件清单"])
        if header_summary:
            for directory, headers in header_summary:
                lines.append(f"### `{directory}`")
                for header in headers[:40]:
                    lines.append(f"- `{header}`")
        else:
            lines.append("- 当前没有观察到头文件。")

        lines.extend(
            [
                "",
                "## README 摘录",
                self._truncate_text(project_info.get("readme_content", "") or "当前没有找到 README 文件。", 2000),
                "",
                "## 证据边界",
                "- 该清单来自文件系统扫描，不代表最终安装布局。",
                "- 未发现的可执行文件、库文件或生成目录不会被推断为存在。",
                "- 文件职责和模块行为需要结合后续 `01_subsystems` / `03_behaviors` 文档理解。",
                "",
            ]
        )

        return "\n".join(lines)

    def _build_interface_doc_content(
        self,
        module: Dict,
        header_records: List[Dict],
        functions: List[Dict],
        structs: List[Dict],
        referenced_structs: List[str],
        macros: List[Dict],
        globals_list: List[Dict],
    ) -> str:
        lines = [
            f"# 接口事实：{module['name']}",
            "",
            "该文档面向后续 Rust 仓库级重写，只保留当前源码分析阶段直接观察到的接口事实。",
            "没有在当前解析结果中出现的头文件、宏、错误码、配置项不会被补写或假设。",
            "",
            "## 模块范围",
            f"- 模块类别：`{module.get('category', 'unknown')}`",
            f"- 所在目录：`{self._normalize_path(module.get('directory', 'root'))}`",
            f"- 文件列表：{', '.join(self._normalize_path(path) for path in module.get('files', [])) or '无'}",
            f"- 候选头文件：{', '.join(item['path'] for item in header_records) or '无'}",
            f"- 观察到的导出函数数量：{len(functions)}",
            f"- 观察到的结构体定义数量：{len(structs)}",
            f"- 引用但未在本地定义的类型名数量：{len(referenced_structs)}",
            f"- 相关文件中观察到的宏数量：{len(macros)}",
            f"- 观察到的全局变量数量：{len(globals_list)}",
            "",
            "## 头文件证据",
        ]

        if header_records:
            for header in header_records:
                lines.append(f"- `{header['path']}` {header['location']}")
        else:
            lines.append("- 当前没有从目录、include 图或文件名证据中关联到项目头文件。")

        lines.extend(["", "## 函数"])
        if functions:
            for func in functions:
                name = func.get("name", "unknown")
                file_path = self._normalize_path(func.get("file", "unknown"))
                start_line = func.get("start_line", func.get("startLine", 0))
                end_line = func.get("end_line", func.get("endLine", 0))
                signature = self._extract_declaration_excerpt(func.get("source", ""))
                lines.extend(
                    [
                        f"### `{name}`",
                        f"- 定义位置：{self._format_source_location(file_path, start_line, end_line)}",
                        f"- 源文件：`{file_path}`",
                        f"- 观察到的声明：`{signature or '当前 parser 输出中不可用'}`",
                        f"- 近似函数体长度：{func.get('line_count', func.get('num_lines', 0)) or '未知'} 行",
                    ]
                )
        else:
            lines.append("- 当前模块没有观察到函数定义。")

        lines.extend(["", "## 结构体与类型"])
        if structs:
            for struct in structs:
                name = struct.get("name", "anonymous")
                file_path = self._normalize_path(struct.get("file", "unknown"))
                start_line = struct.get("start_line", struct.get("startLine", 0))
                end_line = struct.get("end_line", struct.get("endLine", 0))
                declaration = self._extract_declaration_excerpt(struct.get("source", ""), terminator="", max_chars=220)
                lines.extend(
                    [
                        f"### `{name}`",
                        f"- 定义位置：{self._format_source_location(file_path, start_line, end_line)}",
                        f"- 源文件：`{file_path}`",
                        f"- 观察到的定义前缀：`{declaration or '当前 parser 输出中不可用'}`",
                    ]
                )
        else:
            lines.append("- 当前模块切片中没有观察到结构体定义。")

        lines.extend(["", "## 被引用的外部类型"])
        if referenced_structs:
            for struct_name in referenced_structs:
                lines.append(
                    f"- `{struct_name}`：该名称来自聚类元数据或邻近调用分析，但在当前模块文件中没有观察到本地定义。"
                )
        else:
            lines.append("- 当前没有记录到本地定义之外的外部结构体或类型引用。")

        lines.extend(["", "## 宏与常量"])
        if macros:
            for macro in macros:
                snippet = self._trim_inline(macro.get("source", ""), 180)
                lines.append(
                    f"- `{macro['name']}` {self._format_source_location(macro['file'], macro['start_line'], macro['end_line'])}: `{snippet or '定义内容不可用'}`"
                )
        else:
            lines.append("- 当前模块文件及相关头文件中没有观察到宏或常量定义。")

        lines.extend(["", "## 全局变量"])
        if globals_list:
            for variable in globals_list:
                declaration = self._extract_declaration_excerpt(variable.get("source", ""))
                lines.append(
                    f"- `{variable['name']}` {self._format_source_location(variable['file'], variable['start_line'], variable['end_line'])}: `{declaration or '声明内容不可用'}`"
                )
        else:
            lines.append("- 当前模块的 `.c` 文件中没有观察到全局变量定义。")

        lines.extend(
            [
                "",
                "## 已知缺口",
                "- 该文档根据函数定义、结构体定义、宏和全局变量的解析结果生成，不自动推断 `.h` 中未解析到的声明签名。",
                "- 如果某个函数在“函数”一节中出现但没有明确头文件绑定，后续 Rust 迁移时应回查对应源码的 `#include` 关系与构建脚本。",
                "- 错误码、配置项、输入输出协议只在源码中出现明确符号时记录；缺失并不代表语义不存在，只代表当前事实提取未观察到。",
                "",
            ]
        )

        return "\n".join(lines)

    def _infer_module_focus(self, module: Dict) -> str:
        function_names = [func.get("name", "") for func in module.get("functions", []) if isinstance(func, dict)]
        file_stems = [os.path.splitext(os.path.basename(path))[0] for path in module.get("files", [])]

        if function_names:
            common_prefix = os.path.commonprefix(function_names).strip("_- ")
            if len(common_prefix) >= 4:
                return f"围绕 `{common_prefix}` 前缀函数组织"

        if file_stems:
            unique_stems = sorted(set(stem for stem in file_stems if stem))
            if len(unique_stems) == 1:
                return f"围绕 `{unique_stems[0]}` 相关源码文件组织"
            if len(unique_stems) <= 3:
                return f"围绕 {', '.join(f'`{stem}`' for stem in unique_stems)} 相关源码文件组织"

        return "当前只能从文件和符号分布看出这是一个局部源码切片，职责需要结合源码进一步确认"

    def _build_module_summary_content(self, module: Dict) -> str:
        functions = module.get("functions", [])
        project_analysis = self.project_analysis or {}
        struct_records = self._collect_module_struct_records(module, project_analysis)
        referenced_structs = self._collect_module_struct_references(module, struct_records)
        focus = self._infer_module_focus(module)
        cohesion_score = module.get("cohesion_score", 0)
        internal_calls = module.get("internal_calls", 0)
        external_calls = module.get("external_calls", 0)

        lines = [
            "# 模块摘要",
            "",
            "该文档只根据模块划分结果和已解析源码事实生成，不把“信息不足”写成“空实现”或“设计错误”。",
            "",
            "## 1. 模块职责",
            f"- 观察到的焦点：{focus}",
            f"- 模块类别：`{module.get('category', 'unknown')}`",
            f"- 目录范围：`{self._normalize_path(module.get('directory', 'root'))}`",
            "",
            "## 2. 输入和输出",
            "- 当前阶段不对运行时 I/O 做臆测，接口边界以已观察到的函数签名和源码文件为准。",
            f"- 文件输入边界：{', '.join(self._normalize_path(path) for path in module.get('files', [])) or 'none'}",
            f"- 函数数量：{len(functions)}",
            "",
            "## 3. 核心接口列表",
        ]

        if functions:
            for func in functions[:20]:
                lines.append(self._build_function_fact_line(func))
        else:
            lines.append("- 当前模块没有解析到函数定义。")

        lines.extend(["", "## 4. 依赖哪些其他模块"])
        lines.append(f"- 内部调用次数：{internal_calls}")
        lines.append(f"- 外部调用次数：{external_calls}")
        lines.append(f"- 内聚度分数：{cohesion_score:.2f}")
        if module.get("headers"):
            lines.append(f"- 关联头文件：{', '.join(self._normalize_path(path) for path in module.get('headers', []))}")
        else:
            lines.append("- 关联头文件：当前模块元数据中未记录。")

        lines.extend(["", "## 5. 必须保留的关键行为"])
        if functions:
            lines.append("- 至少需要保留这些函数定义所在源码中的控制流和返回约定，具体行为应回查实现体，而不是依赖摘要脑补。")
        else:
            lines.append("- 当前模块没有函数定义可供提炼关键行为。")

        if struct_records:
            struct_names = ", ".join(
                f"`{struct.get('name', 'anonymous')}`" for struct in struct_records[:10]
            )
            lines.append(f"- 在本模块文件中定义的数据结构：{struct_names}")
        elif referenced_structs:
            lines.append(f"- 仅观察到结构体引用名：{', '.join(f'`{name}`' for name in referenced_structs[:10])}")
        else:
            lines.append("- 当前模块没有解析到结构体定义。")

        lines.extend(["", "## 6. 模块划分信号"])
        if module.get("parent_module"):
            lines.append(
                f"- 当前模块是从父模块 `{module['parent_module']}` 拆分出来的子模块，cluster 类型为 `{module.get('cluster_type', 'unknown')}`。"
            )
            split_reasons = module.get("origin_split_reasons", [])
            if split_reasons:
                lines.append(f"- 父模块触发拆分的真实原因：{'；'.join(split_reasons)}")
            else:
                lines.append("- 父模块已发生拆分，但当前未保留更细的拆分原因。")
        elif module.get("needs_split"):
            split_reasons = module.get("split_reasons", [])
            lines.append("- 模块划分器仍认为这个模块需要进一步拆分。")
            if split_reasons:
                lines.append(f"- 拆分原因：{'；'.join(split_reasons)}")
        else:
            lines.append("- 当前模块已经是划分器收敛后的可消费单元，没有额外的拆分信号。")

        lines.extend(
            [
                "",
                "## 结论",
                "- 如果源码中确实存在函数定义，就不应被描述成“空实现”；当前文档以源码位置和声明摘录为准。",
                "- “模块划分不合理”只应来自划分器的真实拆分信号，而不应由摘要模型在信息不足时自行下结论。",
                "",
            ]
        )

        return "\n".join(lines)

    def _build_interfaces_overview(self, project_name: str, interface_entries: List[Dict]) -> str:
        # 这里故意不用模型生成 overview，而是直接写成稳定索引。
        # 目的不是“写得漂亮”，而是给后续 Rust 迁移提供低成本、低噪声的导航页。
        lines = [
            f"# {project_name} 公共接口总览",
            "",
            "该索引面向后续 Rust 重写阶段，保留模块级接口入口，而不是把所有接口事实压到单一超长文档里。",
            "",
            f"- 接口模块数：{len(interface_entries)}",
            "",
        ]

        for entry in interface_entries:
            lines.append(f"## {entry['module_name']}")
            lines.append(f"- 模块类别：{entry['category']}")
            lines.append(f"- 关联头文件：{', '.join(entry['headers']) if entry['headers'] else '无'}")
            lines.append(
                f"- 代表函数：{', '.join(entry['functions']) if entry['functions'] else '无'}"
            )
            lines.append(
                f"- 代表结构体：{', '.join(entry['structs']) if entry['structs'] else '无'}"
            )
            lines.append(f"- 详细文档：{entry['detail_doc']}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _build_behavior_blocks(self, module_analyses: List[Dict]) -> List[str]:
        # 行为文档最容易爆上下文，因此先把每个模块摘要截成较短片段，再做批处理汇总。
        blocks = []
        for analysis in module_analyses:
            excerpt = self._truncate_text(
                analysis.get("analysis", ""),
                self.MAX_MODULE_ANALYSIS_CHARS,
            )
            blocks.append(f"### {analysis['module_name']} 模块\n{excerpt}")
        return blocks

    def _build_constitution_context(
        self,
        project_info: Dict,
        interfaces_doc: str,
        behaviors_doc: str,
    ) -> str:
        # constitution 不需要重新看全量源码，只需要项目轮廓 + 接口摘要 + 行为摘要。
        # 这里构造的是一个“高信噪比”的治理上下文，而不是原始分析转储。
        lines = [
            f"- Build system: {project_info.get('build_system', 'unknown')}",
            f"- C files: {len(project_info.get('c_files', []))}",
            f"- Header files: {len(project_info.get('h_files', []))}",
            f"- Entry files: {', '.join(project_info.get('entry_files', [])[:5]) or 'none'}",
            f"- Module units: {len(self.module_units)}",
            f"- Cluster units: {len(self.cluster_units)}",
            "",
            "## Module Inventory",
        ]

        for module in self.module_units[:12]:
            lines.append(
                f"- {module['name']} ({module['category']}): "
                f"{len(module.get('files', []))} files, "
                f"{len(module.get('functions', []))} functions, "
                f"{len(module.get('structs', []))} structs"
            )

        lines.extend(
            [
                "",
                "## Interface Overview Excerpt",
                self._truncate_text(interfaces_doc, self.MAX_CONSTITUTION_DOC_CHARS),
                "",
                "## Behavior Overview Excerpt",
                self._truncate_text(behaviors_doc, self.MAX_CONSTITUTION_DOC_CHARS),
            ]
        )

        return "\n".join(lines)
        
    def _collect_project_info(self, project_path: str) -> Dict:
        """
        收集项目基本信息
        
        Args:
            project_path: 项目路径
            
        Returns:
            项目信息字典
        """
        project_name = Path(project_path).name
        
        # 这一阶段只做轻量项目探查，不解析 AST。
        # 产物主要用于 repo_manifest / constitution / prompt 上下文。
        c_files = []
        h_files = []
        other_files = []
        
        for root, dirs, files in os.walk(project_path):
            # 跳过隐藏目录和常见非源码目录
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['build', 'dist', 'bin', 'obj']]
            
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, project_path)
                
                if file.endswith('.c'):
                    c_files.append(rel_path)
                elif file.endswith('.h'):
                    h_files.append(rel_path)
                else:
                    other_files.append(rel_path)
        
        # README 往往是项目用途和构建方式最浓缩的自然语言说明。
        readme_content = ""
        for readme_name in ['README.md', 'README', 'readme.md']:
            readme_path = Path(project_path) / readme_name
            if readme_path.exists():
                try:
                    with open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                        readme_content = f.read()
                    break
                except Exception as e:
                    print(f"读取 README 文件失败：{e}")
        
        # 构建系统信息会影响后续 Rust 重写的工程组织方式。
        build_system = "unknown"
        build_files = []
        for bf in ['Makefile', 'CMakeLists.txt', 'configure', 'Makefile.am', 'Makefile.in']:
            if Path(project_path, bf).exists():
                build_system = bf
                build_files.append(bf)
        
        # 可执行文件 / 库文件的识别是很粗糙的启发式，只做辅助信息使用。
        executables = []
        libraries = []
        for ext in ['', '.out', '.bin', '.exe']:
            for f in Path(project_path).glob(f"*{ext}"):
                if f.is_file() and os.access(f, os.X_OK):
                    executables.append(f.name)
        for pattern in ['*.a', '*.so', '*.so.*', '*.dylib']:
            for f in Path(project_path).glob(pattern):
                libraries.append(f.name)
        
        # 入口文件通常暗示初始化顺序和主流程，是行为文档的重要线索。
        entry_files = []
        for f in c_files:
            if 'main' in f.lower() or 'entry' in f.lower() or 'start' in f.lower():
                entry_files.append(f)
        
        return {
            'project_name': project_name,
            'c_files': c_files,
            'h_files': h_files,
            'other_files': other_files,
            'readme_content': readme_content,
            'build_system': build_system,
            'build_files': build_files,
            'executables': executables,
            'libraries': libraries,
            'entry_files': entry_files
        }
    
    def _build_dependency_graph(self, project_path: str, project_analysis: Dict = None) -> Dict:
        """
        构建文件依赖图和调用关系图
        
        Args:
            project_path: 项目路径
            
        Returns:
            依赖图字典
        """
        print("  构建依赖图...")
        
        from collections import defaultdict
        import re
        
        # 这里构造的 dependency_graph 并不追求编译器级精度，而是服务于模块划分和文档生成。
        dependency_graph = {
            'include_graph': defaultdict(set),  # 文件包含关系
            'call_graph': defaultdict(list),      # 函数调用关系
            'struct_usage': defaultdict(list),    # 函数使用结构体
            'global_vars': defaultdict(set),     # 文件使用的全局变量
            'file_symbols': defaultdict(dict)    # 文件定义的符号
        }
        
        # 这里直接按文本扫描 include / 函数调用 / struct 使用，成本低，但会有一定噪声。
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['build', 'dist', 'bin', 'obj']]
            
            for file in files:
                if not (file.endswith('.c') or file.endswith('.h')):
                    continue
                
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, project_path)
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        lines = content.split('\n')
                    
                    # 分析 #include
                    for line in lines:
                        line = line.strip()
                        if line.startswith('#include'):
                            # 提取包含的文件名
                            if '"' in line:
                                included = line.split('"')[1]
                                dependency_graph['include_graph'][rel_path].add(included)
                            elif '<' in line:
                                included = line.split('<')[1].split('>')[0]
                                dependency_graph['include_graph'][rel_path].add(included)
                    
                    # 简单分析函数定义和使用
                    current_function = None
                    for i, line in enumerate(lines):
                        # 检测函数定义
                        if '(' in line and ')' in line and ('{' in line or line.strip().endswith(')')):
                            parts = line.split('(')
                            if len(parts) >= 2 and not line.strip().startswith('#') and not line.strip().startswith('//'):
                                # 安全提取函数名
                                func_part = parts[0].strip()
                                if func_part:
                                    func_tokens = func_part.split()
                                    if func_tokens:
                                        func_name = func_tokens[-1]
                                        if func_name and not func_name.startswith('*') and not func_name.startswith('&'):
                                            current_function = func_name
                                            dependency_graph['file_symbols'][rel_path]['functions'] = \
                                                dependency_graph['file_symbols'][rel_path].get('functions', []) + [func_name]
                        
                        # 检测函数调用（简单启发式）
                        if current_function:
                            calls = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', line)
                            for call in calls:
                                if call != current_function and not call.startswith('_'):
                                    if call not in dependency_graph['call_graph'][current_function]:
                                        dependency_graph['call_graph'][current_function].append(call)
                    
                    # 检测结构体定义
                    struct_defs = re.findall(r'struct\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\{', content)
                    for struct in struct_defs:
                        dependency_graph['file_symbols'][rel_path]['structs'] = \
                            dependency_graph['file_symbols'][rel_path].get('structs', []) + [struct]
                
                except Exception as e:
                    print(f"    a.分析文件 {rel_path} 时出错：{e}")

        if project_analysis:
            dependency_graph['call_graph'] = defaultdict(list)
            dependency_graph['struct_usage'] = defaultdict(list)

            for func in project_analysis.get("functions", []):
                func_name = func.get("name") or func.get("func_defid", "").rsplit(":", 1)[-1]
                file_path = self._normalize_path(
                    func.get("file")
                    or func.get("filename")
                    or func.get("func_defid", "").rsplit(":", 1)[0]
                )
                if func_name and file_path:
                    dependency_graph['file_symbols'][file_path]['functions'] = \
                        dependency_graph['file_symbols'][file_path].get('functions', []) + [func_name]

                for caller in func.get("calls", []):
                    caller_name = caller.get("caller", "").rsplit(":", 1)[-1]
                    if caller_name and func_name and func_name not in dependency_graph['call_graph'][caller_name]:
                        dependency_graph['call_graph'][caller_name].append(func_name)

                func_source = func.get("source", "")
                for struct_name in re.findall(r'\bstruct\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', func_source):
                    if func_name and struct_name not in dependency_graph['struct_usage'][func_name]:
                        dependency_graph['struct_usage'][func_name].append(struct_name)

            for struct in project_analysis.get("structs", []):
                normalized_struct = self._normalize_struct_entries([struct])[0]
                file_path = self._normalize_path(normalized_struct.get("file", ""))
                struct_name = normalized_struct.get("name")
                if file_path and struct_name:
                    dependency_graph['file_symbols'][file_path]['structs'] = \
                        dependency_graph['file_symbols'][file_path].get('structs', []) + [struct_name]

        # 转换为集合以便快速查找
        for func in dependency_graph['call_graph']:
            dependency_graph['call_graph'][func] = list(set(dependency_graph['call_graph'][func]))
        for func in dependency_graph['struct_usage']:
            dependency_graph['struct_usage'][func] = list(set(dependency_graph['struct_usage'][func]))
        
        print(f"  ✓ 依赖图构建完成")
        print(f"    - 文件依赖：{len(dependency_graph['include_graph'])}")
        print(f"    - 函数调用：{len(dependency_graph['call_graph'])}")
        print(f"    - 结构体使用：{len(dependency_graph['struct_usage'])}")
        
        return dependency_graph
    
    def _split_modules(self, project_info: Dict, project_analysis: Dict, dependency_graph: Dict) -> Tuple[List[Dict], List[Dict]]:
        """
        使用 ModuleSplitter 进行模块划分
        
        Args:
            project_info: 项目信息
            project_analysis: 项目分析结果
            dependency_graph: 依赖图
            
        Returns:
            (module_units, cluster_units) 元组
        """
        # SpecAgent 不自己决定模块边界，统一委托给 ModuleSplitter。
        # 这样模块划分逻辑可以独立演进，不污染文档生成代码。
        print("  使用 ModuleSplitter 进行模块划分...")
        
        # 调用公共接口方法
        module_units, cluster_units = self.module_splitter.split(
            project_info, 
            project_analysis, 
            dependency_graph
        )
        
        print(f"  ✓ 模块划分完成: {len(module_units)} 个模块, {len(cluster_units)} 个函数簇")
        
        return module_units, cluster_units
    
    def _generate_repo_manifest(self, project_info: Dict, output_dir: str) -> str:
        """
        生成 00_repo_manifest.md - 仓库地图
        
        Args:
            project_info: 项目信息
            output_dir: 输出目录
            
        Returns:
            生成的文档内容
        """
        print("生成 00_repo_manifest.md - 仓库地图...")
        
        # repo manifest 直接用事实型模板构建，避免 LLM 擅自补全目录树、产物和职责。
        content = self._build_repo_manifest_content(project_info)
        
        # 保存到文件
        rewrite_context_dir = Path(output_dir) / "docs" / "rewrite-context"
        rewrite_context_dir.mkdir(parents=True, exist_ok=True)
        
        manifest_path = rewrite_context_dir / "00_repo_manifest.md"
        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✓ 00_repo_manifest.md 已生成：{manifest_path}")
        return content
    
    def _generate_subsystem_docs(self, module_units: List[Dict], output_dir: str) -> List[Dict]:
        """
        生成 01_subsystems/*.md - 子系统说明
        
        Args:
            module_units: 模块单元列表
            output_dir: 输出目录
            
        Returns:
            模块分析结果列表
        """
        print("生成子系统文档...")
        
        rewrite_context_dir = Path(output_dir) / "docs" / "rewrite-context" / "01_subsystems"
        rewrite_context_dir.mkdir(parents=True, exist_ok=True)
        
        # module_analyses 是后续行为文档 / 风险文档的上游输入，因此这里不仅要写文件，还要保留内存态结果。
        # 可优化点：这里可以补模块质量指标，例如模块内调用密度、跨模块耦合度、
        # 以及文档长度与后续生成成功率的关联，方便做实验分析和自动调参。
        module_analyses = []
        
        for module in module_units:
            print(f"  处理模块：{module['name']}")
            
            # 生成模块摘要
            summary = self._generate_module_summary(module, [], output_dir)
            
            # 保存文档
            doc_path = rewrite_context_dir / f"{module['name']}.md"
            with open(doc_path, 'w', encoding='utf-8') as f:
                f.write(summary)
            
            print(f"  ✓ {module['name']}.md 已生成")
            
            # 添加到分析结果
            module_analyses.append({
                'module_name': module['name'],
                'files': [{'name': Path(f).name, 'path': f, 'content': ''} 
                         for f in module['files']],
                'analysis': summary
            })
        
        return module_analyses
    
    def _generate_module_summary(self, module: Dict, file_summaries: List[Dict], 
                                output_dir: str) -> str:
        """
        生成模块摘要
        
        Args:
            module: 模块信息
            file_summaries: 文件摘要列表
            output_dir: 输出目录
            
        Returns:
            生成的摘要内容
        """
        # 模块摘要是行为文档 / spec / tasks 的上游输入，因此这里优先保真，不再让模型凭空判断
        # “空实现”“重复定义”或“划分不合理”。
        return self._build_module_summary_content(module)
    
    def _generate_interfaces_docs(self, project_analysis: Dict, output_dir: str) -> str:
        """
        生成 02_interfaces/*.md - 接口事实文档
        
        Args:
            project_analysis: 项目分析结果
            output_dir: 输出目录
            
        Returns:
            生成的文档内容
        """
        print("生成 02_interfaces 文档...")
        
        rewrite_context_dir = Path(output_dir) / "docs" / "rewrite-context" / "02_interfaces"
        rewrite_context_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_analysis.get("project_name", "project")
        interface_entries = []

        # 当前实现是“按模块生成接口文档”，不是“按文件逐个生成”。
        # 这样更贴近后续 Rust 模块迁移的消费方式，也更有利于控制 prompt 大小。
        # 可优化点：后续可以同时产出一份机器可读的接口清单，
        # 例如把函数签名、输入输出、错误返回、所属头文件单独整理成 JSON。
        for index, module in enumerate(self.module_units, start=2):
            header_records = self._collect_module_header_records(module, project_analysis)
            headers = [item["path"] for item in header_records]
            functions = module.get("functions", [])[:self.MAX_INTERFACE_FUNCTIONS]
            structs = self._collect_module_struct_records(module, project_analysis, header_records)
            referenced_structs = self._collect_module_struct_references(module, structs)
            macros = self._collect_module_macros(module, project_analysis, header_records)
            globals_list = self._collect_module_globals(module, project_analysis)

            if not headers and not functions and not structs and not referenced_structs and not macros and not globals_list:
                continue

            # 详细接口文档改为事实型拼装，避免生成“假设存在 xxx.h”这类不可执行信息。
            content = self._build_interface_doc_content(
                module=module,
                header_records=header_records,
                functions=functions,
                structs=structs,
                referenced_structs=referenced_structs,
                macros=macros,
                globals_list=globals_list,
            )

            detail_name = f"{index:03d}_{module['name']}.md"
            detail_path = rewrite_context_dir / detail_name
            with open(detail_path, 'w', encoding='utf-8') as f:
                f.write(content)

            interface_entries.append(
                {
                    "module_name": module["name"],
                    "category": module["category"],
                    "headers": headers,
                    "functions": [func.get("name", "unknown") for func in functions[:8] if isinstance(func, dict)],
                    "structs": [struct.get("name", "unknown") for struct in structs[:8]],
                    "detail_doc": detail_name,
                }
            )

            print(f"  ✓ {detail_name} 已生成")

        # 总览页只保留索引性质的信息，避免再做一次大型全量汇总。
        content = self._build_interfaces_overview(project_name, interface_entries)
        interfaces_path = rewrite_context_dir / "001_public_interfaces.md"
        with open(interfaces_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"✓ 001_public_interfaces.md 已生成：{interfaces_path}")
        return content
    
    def _generate_behaviors_docs(self, project_path: str, module_analyses: List[Dict], 
                                output_dir: str) -> str:
        """
        生成 03_behaviors/*.md - 行为说明文档
        
        Args:
            project_path: 项目路径
            module_analyses: 模块分析结果
            output_dir: 输出目录
            
        Returns:
            生成的文档内容
        """
        print("生成 03_behaviors 文档...")
        
        rewrite_context_dir = Path(output_dir) / "docs" / "rewrite-context" / "03_behaviors"
        rewrite_context_dir.mkdir(parents=True, exist_ok=True)
        project_name = Path(project_path).name
        behavior_blocks = self._build_behavior_blocks(module_analyses)
        # 可优化点：行为归纳现在仍然偏摘要式。
        # 如果后续要增强修复器或测试生成器，可以把 precondition / postcondition /
        # invariant / error_case 明确拆成稳定字段，而不是主要保留在自然语言段落里。
        behavior_chunks = self._chunk_blocks(behavior_blocks, self.MAX_BATCH_CHARS)

        if not behavior_chunks:
            # 模块分析为空时，至少生成一个可消费的占位文档，避免后续阶段找不到文件。
            content = (
                f"# {project_name} Behavior Specification\n\n"
                "当前没有足够的模块分析结果来推导完整行为文档。"
                "后续 Rust 重写时需要结合模块 spec 补充运行流程和状态约束。\n"
            )
        elif len(behavior_chunks) == 1:
            # 模块不多时，直接单轮生成行为文档。
            prompt = prompt_manager.get(
                'spec_agent',
                'generate_behaviors_doc',
                project_name=project_name,
                all_analyses=behavior_chunks[0],
            )

            messages = [
                {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_behaviors_doc_system_prompt')},
                {'role': 'user', 'content': prompt}
            ]

            response = self.llm.generate(messages)
            content = response[0]
        else:
            # 模块很多时，先做分批行为摘要，再做最终汇总。
            # 这是为了兼顾“尽量完整理解项目”和“本地模型上下文有限”这两个目标。
            batches_dir = rewrite_context_dir / "batches"
            batches_dir.mkdir(parents=True, exist_ok=True)
            batch_summaries = []

            for batch_index, chunk in enumerate(behavior_chunks, start=1):
                prompt = prompt_manager.get(
                    'spec_agent',
                    'generate_behaviors_batch_summary',
                    project_name=project_name,
                    batch_index=batch_index,
                    total_batches=len(behavior_chunks),
                    batch_analyses=chunk,
                )

                messages = [
                    {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_behaviors_batch_summary_system_prompt')},
                    {'role': 'user', 'content': prompt}
                ]

                response = self.llm.generate(messages)
                batch_content = response[0]
                batch_path = batches_dir / f"{batch_index:03d}_behavior_summary.md"
                with open(batch_path, 'w', encoding='utf-8') as f:
                    f.write(batch_content)

                batch_summaries.append(f"## Batch {batch_index}\n{batch_content}")
                print(f"  ✓ 行为摘要批次 {batch_index}/{len(behavior_chunks)} 已生成")

            # 所有批次摘要生成完后，再合成为最终行为规范。
            prompt = prompt_manager.get(
                'spec_agent',
                'generate_behaviors_final_doc',
                project_name=project_name,
                batch_summaries="\n\n".join(batch_summaries),
            )

            messages = [
                {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_behaviors_final_doc_system_prompt')},
                {'role': 'user', 'content': prompt}
            ]

            response = self.llm.generate(messages)
            content = response[0]
        
        # 保存文档
        behaviors_path = rewrite_context_dir / "001_behavior_specification.md"
        with open(behaviors_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✓ 001_behavior_specification.md 已生成：{behaviors_path}")
        return content
    
    def _generate_constitution(
        self,
        project_path: str,
        project_info: Dict,
        interfaces_doc: str,
        behaviors_doc: str,
        output_dir: str,
    ) -> str:
        """
        生成 constitution.md - 项目级原则文档
        
        Args:
            project_path: 项目路径
            output_dir: 输出目录
            
        Returns:
            生成的文档内容
        """
        print("生成 constitution.md - 项目级原则文档...")
        
        project_name = Path(project_path).name
        project_context = self._build_constitution_context(project_info, interfaces_doc, behaviors_doc)
        
        # constitution 更像“迁移工程治理规则”，不应该重新阅读原始源码，而应该消费精炼后的上游文档。
        # 可优化点：这里后续可以区分“强约束”和“软建议”两层，
        # 让后续 Rust 生成与修复阶段更清楚哪些规则必须满足，哪些只是优先遵守。
        prompt = prompt_manager.get('spec_agent', 'generate_constitution',
                                   project_name=project_name,
                                   project_context=project_context,
                                   interface_summary=self._truncate_text(interfaces_doc, self.MAX_CONSTITUTION_DOC_CHARS),
                                   behavior_summary=self._truncate_text(behaviors_doc, self.MAX_CONSTITUTION_DOC_CHARS))
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_constitution_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        content = response[0]
        
        # 保存文档
        memory_dir = Path(output_dir) / ".specify" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        
        constitution_path = memory_dir / "constitution.md"
        with open(constitution_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✓ constitution.md 已生成：{constitution_path}")
        return content
    
    def _generate_spec_per_module(self, project_path: str, module: Dict, 
                                 output_dir: str, module_index: int) -> str:
        """
        为单个模块生成 spec.md
        
        Args:
            project_path: 项目路径
            module: 模块信息
            output_dir: 输出目录
            module_index: 模块索引
            
        Returns:
            生成的文档内容
        """
        project_name = Path(project_path).name
        feature_name = module['name'].replace('-', '_').replace(' ', '_')
        branch_name = f"{module_index:03d}-{feature_name}-rust-port"
        today = datetime.now().strftime("%Y-%m-%d")
        
        specs_dir = Path(output_dir) / "specs" / branch_name
        specs_dir.mkdir(parents=True, exist_ok=True)
        
        # 这里把模块元数据重新压缩成 prompt 可用的文本块，
        # 目的是为 spec-kit 的 spec.md 提供足够明确但不过载的上下文。
        # 可优化点：这里可以进一步拆成“类型事实 / 接口事实 / 行为事实 / 风险事实”四段，
        # 减少自然语言混排，提高下游 agent 对上下文的稳定消费能力。
        functions_info = ""
        for func in module.get('functions', [])[:30]:
            if isinstance(func, dict):
                functions_info += self._build_function_fact_line(func) + "\n"
        
        module_structs = self._collect_module_struct_records(module, self.project_analysis or {})
        referenced_structs = self._collect_module_struct_references(module, module_structs)
        structs_info = ""
        for struct in module_structs[:30]:
            structs_info += self._build_struct_fact_line(struct) + "\n"
        for struct_name in referenced_structs[:20]:
            structs_info += f"- `{struct_name}`: referenced type name without local definition\n"
        
        prompt = prompt_manager.get('spec_agent', 'generate_module_spec',
                                   project_name=project_name,
                                   module_name=module['name'],
                                   module_category=module['category'],
                                   branch_name=branch_name,
                                   today=today,
                                   files=module['files'],
                                   functions_info=functions_info,
                                   structs_info=structs_info)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_module_spec_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        content = response[0]
        
        # 保存文档
        spec_path = specs_dir / "spec.md"
        with open(spec_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"  ✓ spec.md 已生成：{spec_path}")
        return content
    
    def _generate_plan_per_module(self, project_path: str, module: Dict, 
                                 output_dir: str, module_index: int) -> str:
        """
        为单个模块生成 plan.md
        
        Args:
            project_path: 项目路径
            module: 模块信息
            output_dir: 输出目录
            module_index: 模块索引
            
        Returns:
            生成的文档内容
        """
        project_name = Path(project_path).name
        feature_name = module['name'].replace('-', '_').replace(' ', '_')
        branch_name = f"{module_index:03d}-{feature_name}-rust-port"
        
        specs_dir = Path(output_dir) / "specs" / branch_name
        
        # plan.md 更偏“技术实现路线”，所以直接吃模块级信息，不依赖全局大上下文。
        module_structs = self._collect_module_struct_records(module, self.project_analysis or {})
        prompt = prompt_manager.get('spec_agent', 'generate_module_plan',
                                   project_name=project_name,
                                   module_name=module['name'],
                                   module_category=module['category'],
                                   branch_name=branch_name,
                                   files=module['files'],
                                   functions=module.get('functions', []),
                                   structs=module_structs)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_module_plan_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        content = response[0]
        
        # 保存文档
        plan_path = specs_dir / "plan.md"
        with open(plan_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"  ✓ plan.md 已生成：{plan_path}")
        return content
    
    def _generate_tasks_per_module(self, project_path: str, module: Dict, 
                                  output_dir: str, module_index: int) -> str:
        """
        为单个模块生成 tasks.md
        
        Args:
            project_path: 项目路径
            module: 模块信息
            output_dir: 输出目录
            module_index: 模块索引
            
        Returns:
            生成的文档内容
        """
        project_name = Path(project_path).name
        feature_name = module['name'].replace('-', '_').replace(' ', '_')
        branch_name = f"{module_index:03d}-{feature_name}-rust-port"
        
        specs_dir = Path(output_dir) / "specs" / branch_name
        
        # tasks.md 是执行层文档，因此这里只保留任务分解所需的最小上下文。
        module_structs = self._collect_module_struct_records(module, self.project_analysis or {})
        prompt = prompt_manager.get('spec_agent', 'generate_module_tasks',
                                   project_name=project_name,
                                   module_name=module['name'],
                                   module_category=module['category'],
                                   branch_name=branch_name,
                                   files=module['files'],
                                   functions=module.get('functions', []),
                                   structs=module_structs)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_module_tasks_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        content = response[0]
        
        # 保存文档
        tasks_path = specs_dir / "tasks.md"
        with open(tasks_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"  ✓ tasks.md 已生成：{tasks_path}")
        return content
    
    def _generate_gaps_and_risks(self, project_path: str, module_analyses: List[Dict], 
                                output_dir: str) -> str:
        """
        生成 04_gaps_and_risks.md - 不确定点和风险文档
        
        Args:
            project_path: 项目路径
            module_analyses: 模块分析结果
            output_dir: 输出目录
            
        Returns:
            生成的文档内容
        """
        print("生成 04_gaps_and_risks.md - 不确定点和风险文档...")
        
        rewrite_context_dir = Path(output_dir) / "docs" / "rewrite-context"
        
        # 风险文档仍然是全量汇总路径，因此在大项目上最容易成为上下文瓶颈。
        # 你当前把它注释掉是合理的。
        all_analyses = ""
        for analysis in module_analyses:
            all_analyses += f"### {analysis['module_name']} 模块\n"
            all_analyses += analysis['analysis']
            all_analyses += "\n\n"
        
        prompt = prompt_manager.get('spec_agent', 'generate_gaps_and_risks',
                                   project_name=Path(project_path).name,
                                   all_analyses=all_analyses)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('spec_agent', 'generate_gaps_and_risks_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        content = response[0]
        
        # 保存文档
        gaps_path = rewrite_context_dir / "04_gaps_and_risks.md"
        with open(gaps_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✓ 04_gaps_and_risks.md 已生成：{gaps_path}")
        return content
    
    def analyze_and_generate_spec(self, project_path: str, output_dir: str) -> None:
        """
        分析 C 项目并生成完整的 spec 文档集（分层聚类方法）
        
        Args:
            project_path: 项目路径
            output_dir: 输出目录
        """
        print("=" * 60)
        print("SpecAgent - C 项目 Spec 文档生成（分层聚类 + 语义细分）")
        print("=" * 60)
        
        # 整个流程可以粗分为三层：
        # 1. 静态分析层：收集项目信息、AST、依赖图
        # 2. 认知压缩层：模块划分、子系统摘要、接口/行为/constitution
        # 3. 执行规划层：为每个模块生成 spec / plan / tasks
        # 可优化点：如果后续要支持更大项目，可以把这三层做成可缓存流水线，
        # 例如拆成“静态分析缓存 / 文档缓存 / spec 缓存”，避免每次实验都从头全量生成。

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 步骤 0: 初始化 spec-kit 项目结构
        print("\n[步骤 0/9] 初始化 spec-kit 项目结构...")
        try:
            specify_init(output_dir, model_type="qwen", script="sh")
            print("  ✓ spec-kit 项目结构初始化完成")
        except Exception as e:
            print(f"  ⚠ 初始化 spec-kit 失败：{e}，继续生成文档...")
        
        # 步骤 1: 收集项目基本信息
        print("\n[步骤 1/9] 收集项目基本信息...")
        project_info = self._collect_project_info(project_path)
        print(f"  项目名称：{project_info['project_name']}")
        print(f"  C 文件数量：{len(project_info['c_files'])}")
        print(f"  头文件数量：{len(project_info['h_files'])}")
        print(f"  构建系统：{project_info['build_system']}")
        
        # 步骤 2: 解析 C 代码结构并构建依赖图。
        # parser 负责产出事实，dependency_graph 负责给模块划分和后续摘要提供关系信息。
        print("\n[步骤 2/9] 解析 C 代码结构并构建依赖图...")
        input_dir_name = Path(project_path).name
        output_dir_path = Path(__file__).parent.parent / "parse" / "res"
        output_dir_path.mkdir(parents=True, exist_ok=True)
        output_file = str(output_dir_path / f"{input_dir_name}.json")
        
        self.parser.analyze_directory(project_path, output_file)
        self.project_analysis = self.parser.get_project_analysis()
        self.dependency_graph = self._build_dependency_graph(project_path, self.project_analysis)
        print(f"  文件数量：{len(self.project_analysis['file_path_map'])}")
        print(f"  函数数量：{len(self.project_analysis['functions'])}")
        print(f"  结构体数量：{len(self.project_analysis['structs'])}")
        
        # 步骤 3: 使用 ModuleSplitter 进行模块划分。
        # 这一步的输出会决定后面文档是按什么粒度生成。
        print("\n[步骤 3/9] 使用 ModuleSplitter 进行模块划分...")
        self.module_units, self.cluster_units = self._split_modules(
            project_info, 
            self.project_analysis, 
            self.dependency_graph
        )
        
        # 步骤 4: 生成 repo_manifest.md
        print("\n[步骤 4/9] 生成仓库地图...")
        self._generate_repo_manifest(project_info, output_dir)
        
        # 步骤 5: 生成子系统文档（基于模块单元）
        print("\n[步骤 5/9] 生成子系统文档...")
        module_analyses = self._generate_subsystem_docs(self.module_units, output_dir)
        
        # 步骤 6: 生成 rewrite-context 中最关键的“横切面文档”。
        # 这些文档不是直接拿来替代源码，而是给后续 Rust 生成阶段提供结构化约束。
        print("\n[步骤 6/9] 生成其他文档...")
        self.project_analysis['project_name'] = project_info['project_name']
        interfaces_doc = self._generate_interfaces_docs(self.project_analysis, output_dir)
        behaviors_doc = self._generate_behaviors_docs(project_path, module_analyses, output_dir)
        # self._generate_gaps_and_risks(project_path, module_analyses, output_dir)
        self._generate_constitution(project_path, project_info, interfaces_doc, behaviors_doc, output_dir)
        
        # 步骤 7: 为每个模块生成 spec-kit 文档集。
        # 这是把“理解结果”转成“执行文档”的过程。
        print("\n[步骤 7/8] 为每个模块生成 spec-kit 文档集...")
        for i, module in enumerate(self.module_units, 1):
            print(f"\n处理模块 {i}/{len(self.module_units)}: {module['name']}")
            self._generate_spec_per_module(project_path, module, output_dir, i)
            self._generate_plan_per_module(project_path, module, output_dir, i)
            self._generate_tasks_per_module(project_path, module, output_dir, i)
        
        print("\n" + "=" * 60)
        print("✓ SpecAgent 完成！")
        print("=" * 60)
        print(f"\n文档生成在：{output_dir}")
        print(f"\n生成的模块 spec 数量：{len(self.module_units)}")
        print(f"生成的函数簇数量：{len(self.cluster_units)}")
        print("\n生成的文档结构:")
        print("  docs/rewrite-context/")
        print("    ├── 00_repo_manifest.md")
        print("    ├── 01_subsystems/*.md")
        print("    ├── 02_interfaces/001_public_interfaces.md")
        print("    ├── 03_behaviors/001_behavior_specification.md")
        # print("    └── 04_gaps_and_risks.md")
        print("  .specify/memory/constitution.md")
        print("  specs/<index>-<module>-rust-port/")
        print("    ├── spec.md")
        print("    ├── plan.md")
        print("    └── tasks.md")
