import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config
from parse.c_ast import CCodeAnalyzer


class PointerAgent:
    """
    可选的指针分析 Agent。

    目标：
    1. 扫描 C 项目中的指针声明和典型指针用法
    2. 基于启发式规则给出 Rust 翻译指导
    3. 产出 markdown / json 文档，供 RustAgent 作为补充上下文使用
    """

    POINTER_DECL_RE = re.compile(
        r'(?P<decl>(?:const\s+)?[A-Za-z_][A-Za-z0-9_\s]*?\*+\s*[A-Za-z_][A-Za-z0-9_]*)'
    )
    FUNCTION_POINTER_RE = re.compile(
        r'(?P<ret>[A-Za-z_][A-Za-z0-9_\s\*]*?)\(\s*\*\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\('
    )

    def __init__(self, config: Config = None):
        self.config = config or Config()

    def collect_findings(self, project_path: str, output_dir: str) -> tuple:
        """
        只采集分析结果，不直接写文件。
        供 SpecAgent 复用，把结果拆分写入 spec 风格目录。
        """
        project_root = Path(project_path)
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        findings = []

        project_analysis = self._load_project_analysis(project_root, output_root)
        if project_analysis:
            findings.extend(self._analyze_project_analysis(project_analysis))

        for file_path in sorted(project_root.rglob("*")):
            if file_path.suffix.lower() not in {".c", ".h"}:
                continue
            findings.extend(self._analyze_file(file_path))

        findings = self._deduplicate_findings(findings)
        summary = self._summarize_findings(findings)
        return findings, summary

    def analyze_project(self, project_path: str, output_dir: str) -> Dict[str, str]:
        """
        分析项目中的指针用法，并输出指导文档。
        """
        project_root = Path(project_path)
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        findings, summary = self.collect_findings(project_path, output_dir)
        markdown = self._build_markdown(project_root.name, findings, summary)
        json_data = self._build_json(project_root.name, findings, summary)

        md_path = output_root / "pointer_guidance.md"
        json_path = output_root / "pointer_guidance.json"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        print(f"PointerAgent：已生成指针翻译指导文档：{md_path}")
        print(f"PointerAgent：已生成指针翻译指导 JSON：{json_path}")

        return {
            "markdown_path": str(md_path),
            "json_path": str(json_path),
        }

    def _load_project_analysis(self, project_root: Path, output_root: Path) -> Dict:
        """
        借助 CCodeAnalyzer 获取结构化分析结果。
        如果分析失败，则回退到纯源码扫描。
        """
        try:
            analyzer = CCodeAnalyzer()
            analysis_output = output_root / "_pointer_agent_analysis.json"
            analyzer.analyze_directory(str(project_root), str(analysis_output))
            return analyzer.get_project_analysis()
        except Exception as e:
            print(f"PointerAgent：结构化分析失败，回退到源码扫描：{e}")
            return {}

    def _analyze_project_analysis(self, project_analysis: Dict) -> List[Dict]:
        """
        基于结构化分析结果补充指针相关发现。
        """
        findings: List[Dict] = []

        for func in project_analysis.get("functions", []):
            func_source = func.get("source", "")
            func_defid = func.get("func_defid", "unknown")
            file_name, line_no = self._extract_file_and_line_from_func(func_defid, func.get("span", ""))
            if not func_source:
                continue

            if "malloc(" in func_source or "calloc(" in func_source or "realloc(" in func_source:
                findings.append({
                    "file": file_name,
                    "line": line_no,
                    "kind": "allocation_pattern",
                    "declaration": func_defid,
                    "name": func_defid.rsplit(":", 1)[-1],
                    "rust_hint": "该函数涉及堆分配或重分配，Rust 侧优先考虑 Box<T>、Vec<T>、String 或显式所有权封装，而不是机械地保留裸指针。",
                })

            if "free(" in func_source:
                findings.append({
                    "file": file_name,
                    "line": line_no,
                    "kind": "deallocation_pattern",
                    "declaration": func_defid,
                    "name": func_defid.rsplit(":", 1)[-1],
                    "rust_hint": "该函数涉及显式释放，Rust 迁移时要把释放责任转换为 Drop 驱动的所有权回收，避免继续手工 free 风格。",
                })

            for match in self.FUNCTION_POINTER_RE.finditer(func_source):
                findings.append({
                    "file": file_name,
                    "line": line_no,
                    "kind": "function_pointer",
                    "declaration": match.group(0).strip(),
                    "name": match.group("name"),
                    "rust_hint": "该函数签名或局部上下文包含函数指针，Rust 侧优先考虑 fn 指针、泛型闭包参数或 Box<dyn Fn...>。",
                })

        for struct in project_analysis.get("structs", []):
            struct_source = struct.get("source", "")
            struct_name = struct.get("name", "anonymous")
            file_name = struct.get("file", "unknown")
            start_line = int(struct.get("start_line", 1))
            if not struct_source:
                continue

            if "*" in struct_source:
                findings.append({
                    "file": file_name,
                    "line": start_line,
                    "kind": "struct_pointer_field",
                    "declaration": f"struct {struct_name}",
                    "name": struct_name,
                    "rust_hint": "该结构体包含指针字段，Rust 迁移时优先判断字段是借用、拥有型节点链接、共享引用还是 FFI 边界指针。",
                })

        return self._deduplicate_findings(findings)

    def _extract_file_and_line_from_func(self, func_defid: str, span: str) -> tuple:
        file_name = "unknown"
        line_no = 1
        if ":" in func_defid:
            file_name = func_defid.rsplit(":", 1)[0]
        span_match = re.match(r'([^:]+):(\d+):', span)
        if span_match:
            file_name = span_match.group(1)
            line_no = int(span_match.group(2))
        return file_name, line_no

    def _analyze_file(self, file_path: Path) -> List[Dict]:
        """
        分析单个文件中的指针声明与典型模式。
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"PointerAgent：读取文件失败 {file_path}: {e}")
            return []

        findings: List[Dict] = []
        lines = content.splitlines()
        relative_path = file_path.as_posix()

        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                continue

            for match in self.FUNCTION_POINTER_RE.finditer(line):
                name = match.group("name")
                findings.append({
                    "file": relative_path,
                    "line": line_no,
                    "kind": "function_pointer",
                    "declaration": match.group(0).strip(),
                    "name": name,
                    "rust_hint": "优先考虑 fn 指针、泛型闭包参数或 Box<dyn Fn...>；如果只是回调接口，先保持函数指针风格。",
                })

            for match in self.POINTER_DECL_RE.finditer(line):
                decl = match.group("decl").strip()
                if "(*" in decl:
                    continue

                kind = self._classify_pointer_decl(decl, line, content)
                findings.append({
                    "file": relative_path,
                    "line": line_no,
                    "kind": kind["kind"],
                    "declaration": decl,
                    "name": kind["name"],
                    "rust_hint": kind["rust_hint"],
                })

        return self._deduplicate_findings(findings)

    def _classify_pointer_decl(self, decl: str, line: str, file_content: str) -> Dict[str, str]:
        """
        根据声明和局部上下文对指针用法做启发式分类。
        """
        decl_normalized = " ".join(decl.replace("\t", " ").split())
        name_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*$', decl_normalized)
        name = name_match.group(1) if name_match else "unknown"

        if "char *" in decl_normalized or "char*" in decl_normalized:
            if "const char" in decl_normalized:
                return {
                    "kind": "c_string_borrowed",
                    "name": name,
                    "rust_hint": "通常优先翻译为 &str、&CStr 或 &[u8]；如果仍处于 FFI 边界，可暂时保持 *const c_char。",
                }
            return {
                "kind": "c_string_owned",
                "name": name,
                "rust_hint": "优先判断所有权：拥有型字符串可考虑 String/CString/Vec<u8>，FFI 边界可先保留 *mut c_char。",
            }

        if "void *" in decl_normalized or "void*" in decl_normalized:
            return {
                "kind": "void_pointer",
                "name": name,
                "rust_hint": "优先识别其真实承载类型；若只是泛型容器或上下文指针，可考虑泛型参数、trait object、*mut c_void 或 NonNull<c_void>。",
            }

        if "**" in decl_normalized:
            return {
                "kind": "double_pointer",
                "name": name,
                "rust_hint": "双重指针常见于 out-parameter、可变缓冲区或链式结构；优先区分是否应翻译为 &mut Option<T>、&mut *mut T、Vec<T> 或 Box<T> 的可变引用。",
            }

        if "const " in decl_normalized and "*" in decl_normalized:
            return {
                "kind": "borrowed_const_pointer",
                "name": name,
                "rust_hint": "只读指针通常优先考虑 &T、&[T] 或 *const T；如果存在长度参数，优先联想到 slice。",
            }

        struct_like = re.search(r'(struct\s+\w+|\w+)\s*\*', decl_normalized)
        if struct_like:
            owns_memory = any(token in file_content for token in [f"{name} =", "malloc(", "calloc(", "realloc("])
            if owns_memory:
                return {
                    "kind": "heap_pointer",
                    "name": name,
                    "rust_hint": "看起来可能承载堆对象所有权；优先考虑 Box<T>、Option<Box<T>>、Vec<T>，必要时再退回裸指针或 NonNull<T>。",
                }
            return {
                "kind": "node_or_alias_pointer",
                "name": name,
                "rust_hint": "优先判断是借用、树节点链接还是共享引用；可在 &T / &mut T、Box<T>、Rc<RefCell<T>>、NonNull<T> 之间选择。",
            }

        return {
            "kind": "generic_pointer",
            "name": name,
            "rust_hint": "需要结合读写方式、生命周期和所有权判断，常见候选是 &T、&mut T、Box<T>、Vec<T>、*mut T、NonNull<T>。",
        }

    def _deduplicate_findings(self, findings: List[Dict]) -> List[Dict]:
        seen = set()
        results = []
        for item in findings:
            key = (item["file"], item["line"], item["declaration"], item["kind"])
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

    def _build_markdown(self, project_name: str, findings: List[Dict], summary: Dict[str, int]) -> str:
        lines = [
            f"# {project_name} 指针翻译指导",
            "",
            "该文档由 PointerAgent 自动生成，用于补充 C 指针到 Rust 的迁移指导。",
            "注意：这里给出的是启发式建议，真正翻译时仍需结合所有权、生命周期、别名关系和 FFI 边界判断。",
            "",
            "## 总体规则",
            "- `const T *`：优先考虑 `&T`、`&[T]` 或 `*const T`",
            "- `T *`：优先区分借用、所有权、可变访问和节点链接，再在 `&mut T`、`Box<T>`、`Vec<T>`、`*mut T`、`NonNull<T>` 之间选择",
            "- `char * / const char *`：优先考虑 `String`、`&str`、`CString`、`&CStr` 或 `Vec<u8>`",
            "- `void *`：优先恢复真实类型；否则考虑泛型、trait object 或 `c_void`",
            "- `T **`：优先判断是否是 out-parameter、重分配缓冲区或链式结构",
            "- 函数指针：优先考虑 `fn(...) -> ...`、泛型闭包或 `Box<dyn Fn...>`",
            "",
            "## 分类统计",
        ]

        if summary:
            for kind, count in summary.items():
                lines.append(f"- `{kind}`: {count}")
        else:
            lines.append("- 未检测到明显的指针声明")

        lines.extend([
            "",
            "## 分类指导模板",
        ])

        guidance_templates = self._build_guidance_templates()
        for kind, template in guidance_templates.items():
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

        if not findings:
            lines.append("- 未发现需要特别记录的指针模式")
            return "\n".join(lines)

        by_file: Dict[str, List[Dict]] = {}
        for item in findings:
            by_file.setdefault(item["file"], []).append(item)

        for file_name, items in by_file.items():
            lines.append(f"### {file_name}")
            for item in items[:40]:
                lines.append(
                    f"- 第 {item['line']} 行：`{item['declaration']}` -> {item['rust_hint']}"
                )
            if len(items) > 40:
                lines.append(f"- 该文件其余 {len(items) - 40} 条发现已省略")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _build_json(self, project_name: str, findings: List[Dict], summary: Dict[str, int]) -> Dict:
        return {
            "project_name": project_name,
            "summary": summary,
            "guidance_templates": self._build_guidance_templates(),
            "findings": findings,
        }

    def _build_guidance_templates(self) -> Dict[str, Dict]:
        """
        按指针模式类别给出更稳定的 Rust 翻译模板。
        """
        return {
            "c_string_borrowed": {
                "scenario": "只读 C 字符串指针，例如 const char *。",
                "rust_candidates": ["&str", "&CStr", "&[u8]", "*const c_char"],
                "notes": [
                    "如果已经脱离 FFI 边界，优先恢复为 &str 或 &[u8]。",
                    "如果仍要与 C API 直接交互，可暂时保留 *const c_char。",
                ],
            },
            "c_string_owned": {
                "scenario": "拥有型或可变 C 字符串指针，例如 char *。",
                "rust_candidates": ["String", "CString", "Vec<u8>", "*mut c_char"],
                "notes": [
                    "先判断内存是否由当前模块拥有，再决定是否转为 String/CString。",
                    "如果字符串长度和编码不稳定，Vec<u8> 往往比 String 更保守。",
                ],
            },
            "void_pointer": {
                "scenario": "无具体静态类型的 void * 指针。",
                "rust_candidates": ["泛型参数", "trait object", "*mut c_void", "NonNull<c_void>"],
                "notes": [
                    "优先恢复真实承载类型，而不是长期保留 void 指针。",
                    "如果只是上下文句柄或 FFI 用户数据，可以暂时保留 c_void 风格。",
                ],
            },
            "double_pointer": {
                "scenario": "双重指针，例如 T **，常见于 out-parameter 或重分配。",
                "rust_candidates": ["&mut Option<T>", "&mut *mut T", "Vec<T>", "Box<T>"],
                "notes": [
                    "先判断它是在返回新对象、重写缓冲区，还是维护链式结构。",
                    "如果本质上是“返回结果”，优先考虑 Option 或 Result 风格而不是裸双指针。",
                ],
            },
            "borrowed_const_pointer": {
                "scenario": "只读借用型指针，例如 const T *。",
                "rust_candidates": ["&T", "&[T]", "*const T"],
                "notes": [
                    "如果同时出现长度参数，优先怀疑它本质上是 slice。",
                    "如果存在跨 FFI 生命周期不清晰的问题，可先保留 *const T。",
                ],
            },
            "heap_pointer": {
                "scenario": "看起来承担堆对象所有权的指针。",
                "rust_candidates": ["Box<T>", "Option<Box<T>>", "Vec<T>", "NonNull<T>"],
                "notes": [
                    "如果伴随 malloc/realloc/free，优先恢复成明确所有权容器。",
                    "只有在自引用结构、侵入式结构或 FFI 边界里再考虑裸指针/NonNull。",
                ],
            },
            "node_or_alias_pointer": {
                "scenario": "节点链接、树结构、链表结构或共享别名指针。",
                "rust_candidates": ["&T", "&mut T", "Box<T>", "Rc<RefCell<T>>", "NonNull<T>"],
                "notes": [
                    "先区分它是普通借用、独占拥有、共享拥有还是 intrusive pointer。",
                    "树、链表、图结构往往不能简单替换成 &mut T，需要结合所有权图判断。",
                ],
            },
            "function_pointer": {
                "scenario": "回调、比较器、访问器等函数指针。",
                "rust_candidates": ["fn(...) -> ...", "F: Fn(...)", "Box<dyn Fn(...)>"],
                "notes": [
                    "如果回调签名固定且无捕获，优先考虑 fn 指针。",
                    "如果需要封装环境或延迟绑定，再考虑闭包泛型或 Box<dyn Fn(...)>。",
                ],
            },
            "allocation_pattern": {
                "scenario": "函数内部存在 malloc/calloc/realloc 等分配模式。",
                "rust_candidates": ["Box<T>", "Vec<T>", "String", "自定义拥有型封装"],
                "notes": [
                    "迁移重点不是照搬分配 API，而是恢复谁拥有内存、谁负责释放。",
                    "如果容量变化明显，Vec<T> 通常比手工 realloc 风格更自然。",
                ],
            },
            "deallocation_pattern": {
                "scenario": "函数内部存在 free 或显式释放逻辑。",
                "rust_candidates": ["Drop", "所有权转移", "RAII 封装"],
                "notes": [
                    "Rust 侧优先把释放责任交给 Drop/所有权，而不是保留手工释放。",
                    "如果释放依赖外部回调，需要明确析构责任边界。",
                ],
            },
            "struct_pointer_field": {
                "scenario": "结构体字段中包含一个或多个指针。",
                "rust_candidates": ["Box<T>", "Option<Box<T>>", "Rc<RefCell<T>>", "NonNull<T>", "*mut T"],
                "notes": [
                    "要先判断字段是拥有型链接、借用型视图、共享状态还是 FFI 句柄。",
                    "这类字段通常决定整个 Rust 数据结构设计，优先单独分析。",
                ],
            },
            "generic_pointer": {
                "scenario": "无法快速归类的一般指针模式。",
                "rust_candidates": ["&T", "&mut T", "Box<T>", "Vec<T>", "*mut T", "NonNull<T>"],
                "notes": [
                    "先结合读写方式、生命周期、别名关系和释放责任判断。",
                    "如果缺少信息，优先保守建模，再在修复阶段逐步收紧。",
                ],
            },
        }
