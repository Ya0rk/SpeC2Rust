import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config


class MacroAgent:
    """
    可选的宏分析 Agent。
    目标：
    1. 扫描 C 项目中的宏定义
    2. 识别常量宏、函数式宏、位标志宏、条件编译宏等典型模式
    3. 生成 Rust 宏/常量/内联函数/条件编译迁移指导
    """

    MACRO_RE = re.compile(
        r'^\s*#\s*define\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)'
        r'(?P<args>\([^)]*\))?\s*(?P<body>.*)$'
    )
    CONDITIONAL_RE = re.compile(
        r'^\s*#\s*(?P<directive>ifdef|ifndef|if|elif|else|endif)\b(?P<body>.*)$'
    )

    def __init__(self, config: Config = None):
        self.config = config or Config()

    def analyze_project(self, project_path: str, output_dir: str) -> Dict[str, str]:
        """
        分析项目中的宏定义，并输出 Rust 迁移指导文档。
        """
        project_root = Path(project_path)
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        findings: List[Dict] = []
        for file_path in sorted(project_root.rglob("*")):
            if file_path.suffix.lower() not in {".c", ".h"}:
                continue
            findings.extend(self._analyze_file(file_path))

        findings = self._deduplicate_findings(findings)
        summary = self._summarize_findings(findings)
        markdown = self._build_markdown(project_root.name, findings, summary)
        json_data = self._build_json(project_root.name, findings, summary)

        md_path = output_root / "macro_guidance.md"
        json_path = output_root / "macro_guidance.json"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        print(f"MacroAgent：已生成宏迁移指导文档：{md_path}")
        print(f"MacroAgent：已生成宏迁移指导 JSON：{json_path}")

        return {
            "markdown_path": str(md_path),
            "json_path": str(json_path),
        }

    def _analyze_file(self, file_path: Path) -> List[Dict]:
        """
        分析单个文件中的宏定义。
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"MacroAgent：读取文件失败 {file_path}: {e}")
            return []

        findings: List[Dict] = []
        lines = content.splitlines()
        relative_path = file_path.as_posix()

        include_guard_name = self._detect_include_guard(lines)
        if include_guard_name:
            findings.append({
                "file": relative_path,
                "line": 1,
                "kind": "include_guard",
                "name": include_guard_name,
                "args": "",
                "body": "",
                "declaration": f"include guard: {include_guard_name}",
                "rust_hint": "这是典型的头文件保护宏。Rust 模块系统本身不需要 include guard，通常不必迁移，只需保留模块组织即可。",
                "rust_candidates": ["不迁移", "模块组织替代"],
            })

        index = 0
        while index < len(lines):
            line_no = index + 1
            line = lines[index]
            match = self.MACRO_RE.match(line)
            if match:
                declaration_lines = [line.rstrip()]
                # 支持带反斜杠续行的多行宏，避免复杂宏被截成半行误判。
                while declaration_lines[-1].rstrip().endswith("\\") and index + 1 < len(lines):
                    index += 1
                    declaration_lines.append(lines[index].rstrip())

                full_declaration = "\n".join(declaration_lines)
                full_match = self.MACRO_RE.match(full_declaration.splitlines()[0])
                if not full_match:
                    index += 1
                    continue

                name = full_match.group("name")
                args = (full_match.group("args") or "").strip()
                body_lines = declaration_lines[:]
                body_lines[0] = (full_match.group("body") or "").rstrip()
                body = "\n".join(body_lines).strip()
                macro_info = self._classify_macro(name, args, body)

                findings.append({
                    "file": relative_path,
                    "line": line_no,
                    "kind": macro_info["kind"],
                    "name": name,
                    "args": args,
                    "body": body,
                    "declaration": full_declaration.strip(),
                    "rust_hint": macro_info["rust_hint"],
                    "rust_candidates": macro_info["rust_candidates"],
                })
                index += 1
                continue

            conditional_match = self.CONDITIONAL_RE.match(line)
            if conditional_match:
                directive = conditional_match.group("directive")
                body = (conditional_match.group("body") or "").strip()
                conditional_info = self._classify_conditional_block(directive, body)
                findings.append({
                    "file": relative_path,
                    "line": line_no,
                    "kind": conditional_info["kind"],
                    "name": directive,
                    "args": "",
                    "body": body,
                    "declaration": line.strip(),
                    "rust_hint": conditional_info["rust_hint"],
                    "rust_candidates": conditional_info["rust_candidates"],
                })
            index += 1

        return findings

    def _detect_include_guard(self, lines: List[str]) -> str:
        """
        识别典型的 include guard 模式：
        #ifndef XXX
        #define XXX
        ...
        #endif
        """
        significant = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            significant.append(stripped)
            if len(significant) >= 8:
                break

        if len(significant) < 2:
            return ""

        first_match = re.match(r'^#\s*ifndef\s+([A-Za-z_][A-Za-z0-9_]*)$', significant[0])
        second_match = re.match(r'^#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s*$', significant[1])
        if not first_match or not second_match:
            return ""

        if first_match.group(1) != second_match.group(1):
            return ""

        if not any(re.match(r'^#\s*endif\b', line.strip()) for line in lines[-10:]):
            return ""

        return first_match.group(1)

    def _classify_macro(self, name: str, args: str, body: str) -> Dict[str, object]:
        """
        对宏做启发式分类，并给出 Rust 迁移建议。
        """
        upper_name = name.upper()
        stripped_body = body.strip()

        if args:
            if any(token in stripped_body for token in ["do {", "while (0)", ";"]):
                return {
                    "kind": "statement_macro",
                    "rust_candidates": ["宏规则 macro_rules!", "普通函数", "闭包"],
                    "rust_hint": "该宏更像语句封装或控制流包装，Rust 侧优先考虑普通函数；若必须保留语法级展开，再考虑 macro_rules!。",
                }
            return {
                "kind": "function_like_macro",
                "rust_candidates": ["内联函数", "泛型函数", "macro_rules!"],
                "rust_hint": "该宏带参数，Rust 侧优先考虑内联函数或泛型函数；只有在需要保留语法级展开、可变参数或 token 拼接时再考虑 macro_rules!。",
            }

        if upper_name.startswith("FLAG_") or upper_name.startswith("BIT_") or re.fullmatch(r'[A-Z0-9_]+', name) and stripped_body.startswith("(") and "<<" in stripped_body:
            return {
                "kind": "bit_flag_macro",
                "rust_candidates": ["const", "bitflags!", "枚举 + 位运算"],
                "rust_hint": "该宏像位标志定义，Rust 侧优先考虑 const 或 bitflags!；如果存在成组标志，bitflags! 通常更合适。",
            }

        if any(token in stripped_body for token in ["sizeof", "offsetof", "##", "#"]):
            return {
                "kind": "preprocessor_magic_macro",
                "rust_candidates": ["core::mem API", "手写辅助函数", "保留构建期处理"],
                "rust_hint": "该宏依赖预处理器能力或编译期元信息，Rust 侧通常不能直接一比一翻译，应改写为类型/内存 API、构建脚本或显式辅助函数。",
            }

        if upper_name in {"NULL", "TRUE", "FALSE"} or re.fullmatch(r'[-+]?0x[0-9A-Fa-f]+|[-+]?\d+[uUlLfF]*', stripped_body):
            return {
                "kind": "constant_macro",
                "rust_candidates": ["const", "static"],
                "rust_hint": "该宏更像常量定义，Rust 侧优先翻译为 const；只有涉及全局可变状态或地址稳定性时再考虑 static。",
            }

        if upper_name.startswith("CONFIG_") or upper_name.startswith("ENABLE_") or upper_name.startswith("HAVE_"):
            return {
                "kind": "conditional_macro",
                "rust_candidates": ["cfg", "cfg!", "Cargo feature", "build.rs"],
                "rust_hint": "该宏更像条件编译开关，Rust 侧优先考虑 #[cfg]、cfg!、Cargo features 或 build.rs，而不是保留运行时常量判断。",
            }

        return {
            "kind": "generic_macro",
            "rust_candidates": ["const", "函数", "macro_rules!", "枚举"],
            "rust_hint": "该宏需要结合上下文进一步判断。优先避免直接机械翻译为 Rust 宏，应先判断它到底在表达常量、函数封装、标志位还是条件编译。",
        }

    def _classify_conditional_block(self, directive: str, body: str) -> Dict[str, object]:
        """
        对条件编译指令做分类，并给出 Rust 迁移建议。
        """
        normalized_body = body.strip()
        candidates = ["#[cfg]", "cfg!", "Cargo feature", "build.rs"]

        if directive in {"ifdef", "ifndef"}:
            hint = "这是典型的宏存在性条件编译。Rust 侧优先映射为 #[cfg(feature = ...)]、#[cfg(target_...)] 或 build.rs 产生的 cfg 标记。"
        elif directive in {"if", "elif"}:
            hint = "这是表达式型预处理条件。Rust 侧通常不能机械保留，应先恢复平台/特性/配置语义，再映射到 #[cfg]、Cargo features 或 build.rs。"
        elif directive == "else":
            hint = "这是条件编译分支的 else 块。Rust 侧通常与前一个 #[cfg] 分支配对出现，需要整体重构条件块。"
        else:
            hint = "这是条件编译块的结束标记。Rust 侧不会直接保留该指令，而是通过 #[cfg] 结构自然闭合。"

        if any(token in normalized_body for token in ["_WIN", "WIN32", "__linux__", "__APPLE__"]):
            hint += " 当前条件看起来与平台相关，优先考虑 target_os、target_family、target_env 等 cfg 条件。"
        elif any(token in normalized_body for token in ["DEBUG", "NDEBUG", "CONFIG_", "ENABLE_", "HAVE_"]):
            hint += " 当前条件看起来与构建配置相关，优先考虑 Cargo feature、profile 或 build.rs 注入的 cfg。"

        return {
            "kind": "conditional_block",
            "rust_candidates": candidates,
            "rust_hint": hint,
        }

    def _deduplicate_findings(self, findings: List[Dict]) -> List[Dict]:
        seen = set()
        results = []
        for item in findings:
            key = (item["file"], item["line"], item["name"], item["kind"])
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
        return results

    def _summarize_findings(self, findings: List[Dict]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for item in findings:
            summary[item["kind"]] = summary.get(item["kind"], 0) + 1
        return dict(sorted(summary.items(), key=lambda kv: (-kv[1], kv[0])))

    def _build_guidance_templates(self) -> Dict[str, Dict]:
        return {
            "constant_macro": {
                "scenario": "不带参数的常量宏，如数字常量、布尔常量、空指针别名",
                "rust_candidates": ["const", "static"],
                "notes": [
                    "优先使用 const，避免继续保留宏风格。",
                    "如果常量依赖具体类型，显式补全 Rust 类型。",
                ],
            },
            "function_like_macro": {
                "scenario": "带参数的表达式宏，如 min/max、包装调用、简易算术",
                "rust_candidates": ["内联函数", "泛型函数", "macro_rules!"],
                "notes": [
                    "优先使用普通函数或泛型函数，减少宏带来的可读性与调试成本。",
                    "只有在必须保留语法级展开时再考虑 macro_rules!。",
                ],
            },
            "statement_macro": {
                "scenario": "带 do { ... } while (0) 或包含多语句副作用的宏",
                "rust_candidates": ["普通函数", "闭包", "macro_rules!"],
                "notes": [
                    "优先改写为普通函数，避免宏级控制流副作用。",
                    "如果宏依赖调用点语法结构，再考虑 macro_rules!。",
                ],
            },
            "bit_flag_macro": {
                "scenario": "位标志、位掩码、标志组合宏",
                "rust_candidates": ["const", "bitflags!", "枚举 + 位运算"],
                "notes": [
                    "成组标志优先考虑 bitflags!。",
                    "孤立标志可直接翻译为 const。",
                ],
            },
            "conditional_macro": {
                "scenario": "条件编译或平台配置相关宏",
                "rust_candidates": ["#[cfg]", "cfg!", "Cargo feature", "build.rs"],
                "notes": [
                    "不要机械翻译成普通常量判断。",
                    "优先用 Rust 的条件编译体系表达平台差异。",
                ],
            },
            "conditional_block": {
                "scenario": "由 #ifdef / #ifndef / #if / #elif / #else / #endif 形成的条件编译块",
                "rust_candidates": ["#[cfg]", "cfg!", "Cargo feature", "build.rs"],
                "notes": [
                    "优先恢复条件块真正表达的平台、特性或构建配置语义。",
                    "不要试图逐行保留预处理器结构，而应整体改写为 Rust 的条件编译结构。",
                ],
            },
            "include_guard": {
                "scenario": "头文件保护宏，如 #ifndef XXX_H / #define XXX_H / #endif",
                "rust_candidates": ["不迁移", "模块组织替代"],
                "notes": [
                    "Rust 模块系统本身不需要 include guard。",
                    "通常只需保留模块边界，不需要把头文件保护宏继续迁移成 Rust 代码。",
                ],
            },
            "preprocessor_magic_macro": {
                "scenario": "依赖 sizeof、offsetof、token 拼接、字符串化等预处理能力的宏",
                "rust_candidates": ["core::mem", "辅助函数", "build.rs", "手工改写"],
                "notes": [
                    "这类宏通常无法一比一平移，需要按语义重构。",
                    "优先恢复宏真实目的，再选择 Rust 等价表达。",
                ],
            },
        }

    def _build_markdown(self, project_name: str, findings: List[Dict], summary: Dict[str, int]) -> str:
        lines = [
            f"# {project_name} 宏迁移指导",
            "",
            "该文档由 MacroAgent 自动生成，用于补充 C 宏到 Rust 的迁移指导。",
            "注意：这里给出的结论是启发式建议，真正迁移时仍需结合宏的调用位置、类型语义、条件编译策略和副作用判断。",
            "",
            "## 总体规则",
            "- 不带参数的常量宏：优先考虑 `const`",
            "- 表达式型函数宏：优先考虑普通函数或泛型函数",
            "- 语句型宏：优先改写为函数或闭包，仅在必要时使用 `macro_rules!`",
            "- 条件编译宏：优先映射到 `#[cfg]`、`cfg!`、Cargo feature 或 `build.rs`",
            "- 位标志宏：优先考虑 `const` 或 `bitflags!`",
            "",
            "## 分类统计",
        ]

        if summary:
            for kind, count in summary.items():
                lines.append(f"- `{kind}`: {count}")
        else:
            lines.append("- 未检测到明显的宏定义")

        lines.extend([
            "",
            "## 分类指导模板",
        ])

        templates = self._build_guidance_templates()
        for kind, template in templates.items():
            lines.append(f"### {kind}")
            lines.append(f"- 场景说明：{template['scenario']}")
            lines.append(f"- Rust 候选：{', '.join(template['rust_candidates'])}")
            for note in template["notes"]:
                lines.append(f"- {note}")
            lines.append("")

        lines.extend([
            "",
            "## 文件级发现",
        ])

        if findings:
            for item in findings:
                lines.extend([
                    f"### {item['name']} ({item['kind']})",
                    f"- 位置：`{item['file']}:{item['line']}`",
                    f"- 原始定义：`{item['declaration']}`",
                    f"- Rust 候选：{', '.join(item['rust_candidates'])}",
                    f"- 迁移建议：{item['rust_hint']}",
                    "",
                ])
        else:
            lines.append("- 未发现需要特别关注的宏定义。")

        return "\n".join(lines)

    def _build_json(self, project_name: str, findings: List[Dict], summary: Dict[str, int]) -> Dict:
        return {
            "project_name": project_name,
            "summary": summary,
            "guidance_templates": self._build_guidance_templates(),
            "findings": findings,
        }
