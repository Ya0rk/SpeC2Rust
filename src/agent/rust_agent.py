import os
import sys
import json
import re
import hashlib
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    tomllib = None

sys.path.append(str(Path(__file__).parent.parent))

from utils.cmd import run
from config.config import Config
from agent.prompt import prompt_manager
from llm.model import Model


class RustAgent:
    """根据项目文档生成地道 Rust 代码的 Agent"""
    
    def __init__(self, config: Config = None):
        """
        初始化 RustDocAgent
        
        Args:
            config: 配置对象
        """
        self.config = config or Config()
        self.llm = Model(self.config)
        
        # 存储项目信息
        self.project_name: str = ""
        self.project_path: str = ""
        self.doc_paths: List[str] = []
        self.doc_contents: Dict[str, str] = {}
        self.generated_files: List[str] = []
        self._last_generation_completed: bool = False
        self._last_generation_rounds: int = 0
        self.generation_plan: Dict = {}
        self.api_contract: Dict = {}
        self.continue_mode: bool = False
        self.source_project_path: str = ""
        self.source_json_path: str = ""
        self.source_records: List[Dict] = []
        self.source_context_summary: str = ""
        self.tool_interface_constraints: str = ""
        self.source_interface_summary: str = ""

    def _clip_document_content(self, doc_path: str, content: str) -> str:
        """
        对输入文档做长度裁剪，避免超长中间文档直接压垮后续生成。
        不同类型的文档使用不同上限；宏/指针指导文档默认更严格。
        """
        normalized = doc_path.replace("\\", "/").lower()
        max_chars = 20000

        if normalized.endswith("macro_guidance.md"):
            max_chars = 12000
        elif normalized.endswith("pointer_guidance.md"):
            max_chars = 12000
        elif normalized.endswith("spec_context.json"):
            max_chars = 16000
        elif normalized.endswith(".md"):
            max_chars = 20000

        if len(content) <= max_chars:
            return content

        clipped = content[:max_chars]
        return (
            clipped
            + "\n\n[文档过长，后续内容已截断；如需完整内容，请回到源文档查看。]\n"
        )

    def _normalize_rel_path(self, path: str) -> str:
        return (path or "").replace("\\", "/").strip()

    def _tokenize_identifier(self, text: str) -> List[str]:
        normalized = self._normalize_rel_path(text)
        base = os.path.splitext(os.path.basename(normalized))[0]
        pieces = re.split(r"[^A-Za-z0-9_]+", normalized + " " + base)
        tokens = []
        for piece in pieces:
            if not piece:
                continue
            for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", piece):
                lowered = part.lower()
                if lowered:
                    tokens.append(lowered)
            lowered_piece = piece.lower()
            if lowered_piece and lowered_piece not in tokens:
                tokens.append(lowered_piece)
        stopwords = {
            "src", "tests", "test", "mod", "lib", "main", "readme",
            "cargo", "parser", "module", "utils", "common",
        }
        return [token for token in tokens if token not in stopwords and len(token) > 1]

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _derive_source_json_path(self, c_project_path: str = "", project_name: str = "") -> str:
        candidates = []
        if c_project_path:
            project_base = Path(c_project_path).name
            candidates.append(self._repo_root() / "src" / "parse" / "res" / f"{project_base}.json")
        if project_name:
            candidates.append(self._repo_root() / "src" / "parse" / "res" / f"{project_name}.json")

        seen = set()
        for candidate in candidates:
            candidate_str = str(candidate)
            if candidate_str in seen:
                continue
            seen.add(candidate_str)
            if os.path.exists(candidate_str):
                return candidate_str
        return ""

    def configure_source_context(self, c_project_path: str = "", source_json_path: str = ""):
        """
        绑定原始 C 项目源码上下文，供 Rust 生成阶段使用。
        """
        self.source_project_path = c_project_path or self.source_project_path
        resolved_json_path = source_json_path or self._derive_source_json_path(
            c_project_path=self.source_project_path,
            project_name=self.project_name,
        )
        self.source_json_path = resolved_json_path or ""
        self.source_records = self._load_source_records(self.source_json_path)
        self.source_context_summary = self._build_source_context_summary()
        self.tool_interface_constraints = self._build_tool_interface_constraints()
        self.source_interface_summary = self._build_source_interface_summary()

    def _load_source_records(self, source_json_path: str) -> List[Dict]:
        """
        从 parse/res/*.json 中加载可供生成阶段使用的源码事实。
        """
        if not source_json_path or not os.path.exists(source_json_path):
            return []

        try:
            with open(source_json_path, "r", encoding="utf-8", errors="ignore") as f:
                payload = json.load(f)
        except Exception as e:
            print(f"加载源码 JSON 失败：{source_json_path}，错误：{e}")
            return []

        raw_records = []
        if isinstance(payload, list):
            raw_records = payload
        elif isinstance(payload, dict):
            for key in ("functions", "records", "items"):
                if isinstance(payload.get(key), list):
                    raw_records.extend(payload.get(key, []))
            if not raw_records:
                raw_records = [payload]

        normalized_records = []
        for item in raw_records:
            if not isinstance(item, dict):
                continue

            func_defid = item.get("func_defid", "")
            name = item.get("name") or (func_defid.rsplit(":", 1)[-1] if ":" in func_defid else "")
            span = item.get("span", "")
            file_path = ""
            if ":" in func_defid:
                file_path = func_defid.rsplit(":", 1)[0]
            elif span:
                span_match = re.match(r"^(.*):\d+:\d+:\d+:\d+$", span)
                if span_match:
                    file_path = span_match.group(1)

            normalized_records.append(
                {
                    "name": name or "unknown",
                    "file": self._normalize_rel_path(file_path),
                    "span": span,
                    "source": item.get("source", "") or "",
                    "num_lines": item.get("num_lines") or len(str(item.get("source", "")).splitlines()),
                    "calls": item.get("calls", []) if isinstance(item.get("calls", []), list) else [],
                    "func_defid": func_defid,
                }
            )
        return normalized_records

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[截断]..."

    def _build_source_context_summary(self, max_records: int = 12, max_chars: int = 16000) -> str:
        """
        构造项目级源码事实摘要，避免 Rust 生成只依赖 rewrite-context 摘要文档。
        """
        if not self.source_records:
            return ""

        parts = []
        parts.append("原始 C 源码事实摘要（来自解析 JSON，优先级高于摘要文档）：")
        grouped_by_file = defaultdict(list)
        for record in self.source_records:
            grouped_by_file[record["file"]].append(record)

        parts.append("源文件概览：")
        for file_path, records in list(grouped_by_file.items())[:8]:
            names = ", ".join(record["name"] for record in records[:8])
            parts.append(f"- {file_path}: {len(records)} 个函数/记录，代表项：{names}")

        main_records = [record for record in self.source_records if record["name"] == "main"]
        if main_records:
            parts.append("入口函数：")
            for record in main_records[:3]:
                signature = self._truncate_text(record.get("source", "").split("{", 1)[0].strip(), 220)
                parts.append(f"- {record['file']} {record['span']}: {signature}")

        parts.append("关键源码片段：")
        prioritized = sorted(
            self.source_records,
            key=lambda item: (
                0 if item["name"] == "main" else 1,
                -int(item.get("num_lines", 0)),
                item["name"],
            ),
        )
        for record in prioritized[:max_records]:
            snippet = self._truncate_text(record.get("source", "").strip(), 1200)
            parts.append(
                f"\n### {record['name']} [{record['file']} {record['span']}]\n"
                f"```c\n{snippet}\n```"
            )

        text = "\n".join(parts).strip()
        return self._truncate_text(text, max_chars)

    def _build_source_interface_summary(self, max_chars: int = 5000) -> str:
        """
        提炼与对外接口相关的源码事实，用于约束项目结构和入口设计。
        """
        if not self.source_records:
            return ""

        parts = ["原始 C 项目的外部接口事实："]
        main_records = [record for record in self.source_records if record["name"] == "main"]
        if main_records:
            for record in main_records[:4]:
                signature = self._truncate_text(record.get("source", "").split("{", 1)[0].strip(), 220)
                parts.append(f"- 入口函数：{signature} @ {record['file']}")

        for record in self.source_records[:10]:
            if record["name"] == "main":
                continue
            signature = self._truncate_text(record.get("source", "").split("{", 1)[0].strip(), 180)
            if signature:
                parts.append(f"- 函数：{signature} @ {record['file']}")

        return self._truncate_text("\n".join(parts).strip(), max_chars)

    def _build_tool_interface_constraints(self, max_chars: int = 4000) -> str:
        """
        判断原项目是否更像工具/CLI，并生成必须保留的接口约束。
        """
        signals = []
        main_records = [record for record in self.source_records if record["name"] == "main"]
        cli_like = False
        for record in main_records:
            source = record.get("source", "")
            header = source.split("{", 1)[0]
            if "argc" in header or "argv" in header:
                cli_like = True
            if any(token in source for token in ("usage", "printf(", "fprintf(", "open(", "read(", "exit(")):
                cli_like = True

        doc_blob = "\n".join(self.doc_contents.values()) if self.doc_contents else ""
        if "可执行文件" in doc_blob or "入口候选" in doc_blob:
            cli_like = True

        if not cli_like:
            return ""

        signals.append("检测结果：原始 C 项目更像工具/CLI/可执行程序，而不是单纯库。")
        for record in main_records[:3]:
            header = self._truncate_text(record.get("source", "").split("{", 1)[0].strip(), 220)
            signals.append(f"- 入口签名：{header} @ {record['file']}")
        signals.append("- 必须保留命令行入口，而不是擅自改造成仅能被库调用的 API。")
        signals.append("- 必须尽量保持参数顺序、参数含义、stdout/stderr 输出通道、usage/error 文案职责和退出语义一致。")
        signals.append("- 如果需要额外的库层封装，可以新增内部模块，但不能破坏原工具的外部使用方式。")
        signals.append("- 对 main/入口调度、解析流程、错误退出路径，应优先参考原 C 源码，而不是仅根据摘要重写。")
        return self._truncate_text("\n".join(signals), max_chars)

    def _extract_record_call_tokens(self, record: Dict) -> set[str]:
        """
        从函数调用关系中提取通用符号 token，用于弱关联匹配。
        """
        tokens = set()
        for call in record.get("calls", [])[:12]:
            caller = str(call.get("caller", "")).rsplit(":", 1)[-1]
            source = str(call.get("source", ""))
            for token in self._tokenize_identifier(caller):
                tokens.add(token)
            for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", source):
                lowered = token.lower()
                if len(lowered) > 1:
                    tokens.add(lowered)
        return tokens

    def _score_source_record_for_target_file(self, file_path: str, record: Dict) -> int:
        """
        基于项目无关的通用信号，为目标 Rust 文件挑选相关 C 源码。
        只依赖名字、路径、入口约束和调用关系，不做项目特例判断。
        """
        normalized_path = self._normalize_rel_path(file_path)
        path_tokens = set(self._tokenize_identifier(normalized_path))
        file_stem = os.path.splitext(os.path.basename(normalized_path))[0].lower()

        record_name = str(record.get("name", "")).lower()
        record_file = self._normalize_rel_path(record.get("file", ""))
        record_file_stem = os.path.splitext(os.path.basename(record_file))[0].lower()
        record_tokens = set(self._tokenize_identifier(record_name) + self._tokenize_identifier(record_file))
        call_tokens = self._extract_record_call_tokens(record)

        score = 0

        if file_stem and record_name == file_stem:
            score += 24
        if file_stem and record_file_stem == file_stem:
            score += 14

        token_overlap = path_tokens & record_tokens
        score += len(token_overlap) * 4

        call_overlap = path_tokens & call_tokens
        score += min(len(call_overlap), 4) * 2

        if normalized_path.endswith("main.rs") and record_name == "main":
            score += 25
        elif "main" in path_tokens and record_name == "main":
            score += 12

        # 若 Rust 目标文件和 C 源文件来自同名模块/同名编译单元，给适度加权。
        file_path_segments = set(segment.lower() for segment in normalized_path.split("/") if segment)
        record_path_segments = set(segment.lower() for segment in record_file.split("/") if segment)
        segment_overlap = file_path_segments & record_path_segments
        score += min(len(segment_overlap), 3) * 3

        return score

    def _build_relevant_source_context_for_file(self, file_path: str, max_records: int = 6, max_chars: int = 12000) -> str:
        """
        为目标 Rust 文件挑选最相关的 C 源码片段。
        """
        if not self.source_records:
            return ""

        scored = []
        for record in self.source_records:
            score = self._score_source_record_for_target_file(file_path, record)
            if score > 0:
                scored.append((score, record))

        if not scored:
            fallback_records = sorted(
                self.source_records,
                key=lambda item: (
                    0 if item["name"] == "main" else 1,
                    -int(item.get("num_lines", 0)),
                ),
            )[:max_records]
        else:
            fallback_records = [
                item[1]
                for item in sorted(
                    scored,
                    key=lambda pair: (-pair[0], -int(pair[1].get("num_lines", 0)), pair[1]["name"]),
                )[:max_records]
            ]

        parts = [f"与 Rust 文件 `{file_path}` 最相关的原始 C 源码："]
        for record in fallback_records:
            calls = record.get("calls", [])[:6]
            parts.append(f"\n### {record['name']} [{record['file']} {record['span']}]")
            if calls:
                call_lines = ", ".join(
                    self._truncate_text(str(call.get("source", "")).strip(), 80)
                    for call in calls
                    if call.get("source")
                )
                if call_lines:
                    parts.append(f"调用位置示例：{call_lines}")
            snippet = self._truncate_text(record.get("source", "").strip(), 1800)
            parts.append(f"```c\n{snippet}\n```")

        return self._truncate_text("\n".join(parts).strip(), max_chars)

    def _is_cargo_toml(self, file_path: str) -> bool:
        """判断是否为 Cargo.toml。"""
        normalized = file_path.replace("\\", "/")
        return os.path.basename(normalized).lower() == "cargo.toml"

    def _is_readme(self, file_path: str) -> bool:
        """判断是否为 README.md。"""
        normalized = file_path.replace("\\", "/")
        return os.path.basename(normalized).lower() == "readme.md"

    def _generation_plan_path(self) -> str:
        """
        生成计划的持久化路径。
        """
        return os.path.join(self.project_path, ".cgr_generation_plan.json")

    def _api_contract_path(self) -> str:
        """
        接口契约的持久化路径。
        """
        return os.path.join(self.project_path, ".cgr_api_contract.json")

    def _load_api_contract(self) -> Dict:
        """
        从磁盘加载已有接口契约；不存在时返回空结构。
        """
        contract_path = self._api_contract_path()
        if not os.path.exists(contract_path):
            return {"files": {}}
        try:
            with open(contract_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and "files" in data:
                return data
        except Exception as e:
            print(f"加载接口契约失败：{e}")
        return {"files": {}}

    def _save_api_contract(self):
        """
        持久化当前接口契约。
        """
        if not self.project_path:
            return
        contract_path = self._api_contract_path()
        try:
            with open(contract_path, 'w', encoding='utf-8') as f:
                json.dump(self.api_contract, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存接口契约失败：{e}")

    def _ensure_api_contract_loaded(self):
        """
        确保接口契约已加载到内存。
        """
        if not self.api_contract:
            self.api_contract = self._load_api_contract()
        self.api_contract.setdefault("project_name", self.project_name)
        self.api_contract.setdefault("files", {})

    def _extract_api_contract_from_code(self, file_path: str, code: str) -> Dict:
        """
        从单个 Rust 文件中抽取轻量接口事实。
        这里只做启发式提取，目标是给后续文件生成和修复提供稳定事实源，
        而不是做完整语法分析。
        """
        normalized = file_path.replace("\\", "/")
        if not normalized.endswith(".rs"):
            return {}

        contract = {
            "public_structs": [],
            "public_enums": [],
            "public_traits": [],
            "public_functions": [],
            "constructors": [],
            "accessors": [],
            "impl_methods": {},
        }

        for match in re.finditer(r'pub\s+struct\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\{(.*?)\n\}', code, re.DOTALL):
            struct_name = match.group(1)
            body = match.group(2)
            fields = []
            for line in body.splitlines():
                stripped = line.strip().rstrip(',')
                if not stripped or stripped.startswith("//"):
                    continue
                field_match = re.match(r'(pub\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)', stripped)
                if field_match:
                    fields.append({
                        "name": field_match.group(2),
                        "public": bool(field_match.group(1)),
                        "type": field_match.group(3).strip(),
                    })
            contract["public_structs"].append({
                "name": struct_name,
                "fields": fields,
            })

        for match in re.finditer(r'pub\s+enum\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\{(.*?)\n\}', code, re.DOTALL):
            enum_name = match.group(1)
            body = match.group(2)
            variants = []
            for line in body.splitlines():
                stripped = line.strip().rstrip(',')
                if not stripped or stripped.startswith("//"):
                    continue
                variant_match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)', stripped)
                if variant_match:
                    variants.append(variant_match.group(1))
            contract["public_enums"].append({
                "name": enum_name,
                "variants": variants,
            })

        for match in re.finditer(r'pub\s+trait\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\{(.*?)\n\}', code, re.DOTALL):
            trait_name = match.group(1)
            body = match.group(2)
            methods = re.findall(r'fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', body)
            contract["public_traits"].append({
                "name": trait_name,
                "methods": methods,
            })

        for match in re.finditer(r'pub\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', code):
            fn_name = match.group(1)
            contract["public_functions"].append(fn_name)

        impl_pattern = re.compile(r'impl(?:<[^>]*>)?\s+([A-Za-z_][A-Za-z0-9_]*)[^{]*\{(.*?)\n\}', re.DOTALL)
        for match in impl_pattern.finditer(code):
            type_name = match.group(1)
            body = match.group(2)
            methods = []
            for fn_match in re.finditer(r'pub\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', body):
                method_name = fn_match.group(1)
                methods.append(method_name)
                if method_name == "new" or method_name.startswith("with_"):
                    contract["constructors"].append(f"{type_name}::{method_name}")
                if method_name in {"x", "y", "root", "bounds", "state", "config"} or method_name.startswith(("get_", "as_")):
                    contract["accessors"].append(f"{type_name}::{method_name}")
            if methods:
                contract["impl_methods"][type_name] = sorted(set(methods))

        contract["public_functions"] = sorted(set(contract["public_functions"]))
        contract["constructors"] = sorted(set(contract["constructors"]))
        contract["accessors"] = sorted(set(contract["accessors"]))
        return contract

    def _update_api_contract_for_file(self, file_path: str, code: str):
        """
        用单个文件的最新内容更新接口契约。
        """
        self._ensure_api_contract_loaded()
        rel_path = file_path.replace("\\", "/")
        self.api_contract["files"][rel_path] = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "contract": self._extract_api_contract_from_code(rel_path, code),
        }
        self.api_contract["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_api_contract()

    def _build_api_contract_context(self, max_chars: int = 12000) -> str:
        """
        构造给模型看的精简接口契约上下文。
        """
        self._ensure_api_contract_loaded()
        parts = ["当前已生成 Rust 接口契约摘要："]
        for rel_path, info in self.api_contract.get("files", {}).items():
            contract = info.get("contract", {})
            if not contract:
                continue
            parts.append(f"\n### {rel_path}")
            for struct in contract.get("public_structs", []):
                field_desc = ", ".join(
                    f"{f['name']}({'pub' if f['public'] else 'private'}:{f['type']})"
                    for f in struct.get("fields", [])[:12]
                )
                parts.append(f"- struct {struct['name']}: {field_desc or '无字段信息'}")
            for enum in contract.get("public_enums", []):
                parts.append(f"- enum {enum['name']}: {', '.join(enum.get('variants', [])[:12])}")
            for trait in contract.get("public_traits", []):
                parts.append(f"- trait {trait['name']}: {', '.join(trait.get('methods', [])[:12])}")
            if contract.get("constructors"):
                parts.append(f"- constructors: {', '.join(contract['constructors'][:12])}")
            if contract.get("accessors"):
                parts.append(f"- accessors: {', '.join(contract['accessors'][:12])}")
            if contract.get("public_functions"):
                parts.append(f"- public_functions: {', '.join(contract['public_functions'][:12])}")

        text = "\n".join(parts).strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[接口契约摘要过长，后续内容已截断]"
        return text

    def _load_generation_plan(self) -> Dict:
        """
        从磁盘加载已有的生成计划；不存在时返回空计划。
        """
        plan_path = self._generation_plan_path()
        if not os.path.exists(plan_path):
            return {}
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载生成计划失败：{e}")
            return {}

    def _save_generation_plan(self):
        """
        持久化当前生成计划，便于中断后续跑。
        """
        if not self.project_path:
            return
        plan_path = self._generation_plan_path()
        try:
            with open(plan_path, 'w', encoding='utf-8') as f:
                json.dump(self.generation_plan, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存生成计划失败：{e}")

    def _is_nonempty_existing_file(self, file_path: str) -> bool:
        """
        判断文件是否已存在且非空。
        """
        full_path = os.path.join(self.project_path, file_path)
        return os.path.exists(full_path) and os.path.isfile(full_path) and os.path.getsize(full_path) > 0

    def _file_metadata(self, file_path: str) -> Dict[str, object]:
        """
        计算文件大小和内容 hash，用于续跑时判断文件是否真的还是已完成版本。
        """
        full_path = os.path.join(self.project_path, file_path)
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return {}
        try:
            with open(full_path, 'rb') as f:
                data = f.read()
            return {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        except Exception as e:
            print(f"读取文件元数据失败：{full_path}，错误：{e}")
            return {}

    def _is_completed_file_still_valid(self, file_path: str, file_state: Dict) -> bool:
        """
        判断计划里标记为 completed 的文件，当前磁盘版本是否仍可信。
        """
        if not self._is_nonempty_existing_file(file_path):
            return False

        current_meta = self._file_metadata(file_path)
        if not current_meta:
            return False

        recorded_hash = file_state.get("sha256")
        recorded_size = file_state.get("size")
        if recorded_hash and recorded_size is not None:
            return (
                recorded_hash == current_meta.get("sha256")
                and recorded_size == current_meta.get("size")
            )

        # 兼容旧计划：如果还没有 hash/size，但文件非空，则允许跳过，
        # 同时后续会在状态更新时自动补上元数据。
        return True

    def _initialize_generation_plan(self, project_structure: str, implementation_plan: str, planned_files: List[str]) -> Dict:
        """
        初始化或更新文件生成计划。
        """
        existing_plan = self._load_generation_plan() if self.continue_mode else {}
        files_state = existing_plan.get("files", {}) if isinstance(existing_plan, dict) else {}

        normalized_files_state = {}
        for path in planned_files:
            normalized_files_state[path] = files_state.get(path, {})
            if self.continue_mode and self._is_nonempty_existing_file(path):
                previous_status = normalized_files_state[path].get("status")
                if previous_status != "completed":
                    normalized_files_state[path]["status"] = "completed"
                    normalized_files_state[path]["updated_at"] = datetime.now().isoformat(timespec="seconds")
                current_meta = self._file_metadata(path)
                if current_meta:
                    normalized_files_state[path].update(current_meta)
            else:
                if normalized_files_state[path].get("status") == "completed":
                    normalized_files_state[path]["status"] = "pending"
                    normalized_files_state[path].pop("sha256", None)
                    normalized_files_state[path].pop("size", None)
                normalized_files_state[path].setdefault("status", "pending")

        self.generation_plan = {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "project_structure": project_structure,
            "implementation_plan": implementation_plan,
            "planned_files": planned_files,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "files": normalized_files_state,
        }
        self._save_generation_plan()
        return self.generation_plan

    def _mark_generation_status(self, file_path: str, status: str, note: str = ""):
        """
        更新单个文件在生成计划中的状态。
        """
        if not self.generation_plan:
            return
        files_state = self.generation_plan.setdefault("files", {})
        file_state = files_state.setdefault(file_path, {})
        file_state["status"] = status
        file_state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if status == "completed":
            current_meta = self._file_metadata(file_path)
            if current_meta:
                file_state.update(current_meta)
        elif status != "completed":
            file_state.pop("sha256", None)
            file_state.pop("size", None)
        if note:
            file_state["note"] = note
        self.generation_plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_generation_plan()

    def _append_existing_file_to_context(self, file_path: str, context: str) -> str:
        """
        将已存在文件的内容补回上下文，保证续跑时上下文连续。
        """
        full_path = os.path.join(self.project_path, file_path)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if content.strip():
                if file_path.replace("\\", "/").lower().endswith(".rs"):
                    self._update_api_contract_for_file(file_path, content)
                return context + f"\n\n=== 已生成文件：{file_path} ===\n{content}\n"
        except Exception as e:
            print(f"读取已存在文件失败：{full_path}，错误：{e}")
        return context

    def _is_supported_generation_file(self, file_path: str) -> bool:
        """
        判断当前阶段是否应该生成该文件。
        首轮生成只保留 Rust crate 核心文件，避免把 C 头文件、CI 配置等非核心产物误当 Rust 文件生成。
        """
        normalized = file_path.replace("\\", "/").lower()
        file_name = os.path.basename(normalized)

        if file_name in {"cargo.toml", "readme.md", ".gitignore", "build.rs"}:
            return True
        if normalized.startswith("src/") and normalized.endswith(".rs"):
            return True
        if normalized.startswith("tests/") and normalized.endswith(".rs"):
            return bool(getattr(self.config, "generate_tests", False))
        if normalized.startswith("examples/") and normalized.endswith(".rs"):
            return bool(getattr(self.config, "generate_examples", False))
        if normalized.startswith("benches/") and normalized.endswith(".rs"):
            return bool(getattr(self.config, "generate_benches", False))
        return False

    def _looks_like_truncated_rust_source(self, file_path: str, content: str) -> bool:
        """
        轻量判断 Rust 源文件是否疑似截断。
        目标不是完美语法分析，而是拦截明显的半截输出。
        """
        normalized = file_path.replace("\\", "/").lower()
        if not normalized.endswith(".rs"):
            return False

        text = (content or "").strip()
        if not text:
            return True

        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if not lines:
            return True

        last_line = lines[-1].strip()
        suspicious_endings = (
            "///", "//!", "pub", "fn", "struct", "enum", "trait", "impl",
            "where", "=", "->", "{", "(", "[", ",", ":"
        )
        if last_line.endswith(suspicious_endings):
            return True

        open_braces = text.count("{")
        close_braces = text.count("}")
        if close_braces < open_braces:
            return True

        open_parens = text.count("(")
        close_parens = text.count(")")
        if close_parens < open_parens:
            return True

        open_brackets = text.count("[")
        close_brackets = text.count("]")
        if close_brackets < open_brackets:
            return True

        return False

    def _looks_like_doc_only_lib_rs(self, content: str) -> bool:
        """
        判断 lib.rs 是否退化成只有注释/文档而没有实际模块声明。
        """
        text = (content or "").strip()
        if not text:
            return True

        code_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("//"):
                continue
            code_lines.append(stripped)

        if not code_lines:
            return True

        return not any(
            marker in text
            for marker in ["mod ", "pub mod ", "pub use ", "fn ", "struct ", "enum ", "trait ", "impl "]
        )

    def _build_fallback_lib_rs(self, planned_files: List[str]) -> str:
        """
        当 lib.rs 明显失真时，按已规划文件构造一个最小模块入口，确保源码被真正编译到。
        """
        top_level_mods = []
        directory_mods = []

        for item in planned_files:
            normalized = item.replace("\\", "/")
            if not normalized.startswith("src/") or not normalized.endswith(".rs"):
                continue

            rel = normalized[4:]
            if rel in {"lib.rs", "main.rs"}:
                continue

            if "/" not in rel:
                mod_name = rel[:-3]
                if mod_name != "mod":
                    top_level_mods.append(mod_name)
            elif rel.endswith("/mod.rs"):
                directory_mods.append(rel.split("/", 1)[0])

        ordered_mods = []
        seen = set()
        for name in top_level_mods + directory_mods:
            if name not in seen:
                seen.add(name)
                ordered_mods.append(name)

        lines = [
            "//! 自动生成的 crate 入口。",
            "//!",
            "//! 原始生成结果的 lib.rs 疑似被截断，因此这里回退为最小模块声明，",
            "//! 确保其它源码文件会被真正纳入编译。",
            "",
        ]

        for name in ordered_mods:
            lines.append(f"pub mod {name};")

        if len(lines) == 5:
            lines.append("// 当前未解析到可公开的模块。")

        return "\n".join(lines) + "\n"

    def _extract_generated_content(self, content: str, code_lang: str = "rust") -> str:
        """
        提取模型返回的最终内容。
        Rust 文件优先提取 ```rust 代码块，TOML 文件优先提取 ```toml 代码块。
        对模型未闭合的 markdown block 做了兼容。
        """
        if not code_lang:
            # 对项目结构、实现计划这类非代码文本，必须保留完整输出。
            return content

        # 1. 先处理“整个响应就是一个 fenced block”的常见情况。
        # 使用更宽容的正则是为了兼容 ```rust, ```Rust, ```rust name=main.rs 等
        fence_match = re.match(
            r'^\s*```[^\n]*\n(.*)\n\s*```\s*$',
            content,
            re.DOTALL,
        )
        if fence_match:
            return fence_match.group(1).strip()

        # 2. 兜底处理“响应里夹带了解释，但仍包含一个完整 fenced block”的情况。
        # 注意使用 (?m)^\s*``` 来严格匹配行首的 ```，避免误伤 Rust doc 注释里的 /// ```
        fence_search = re.search(
            r'(?m)^\s*```[^\n]*\n(.*?)\n\s*```',
            content,
            re.DOTALL,
        )
        if fence_search:
            return fence_search.group(1).strip()
            
        # 3. 处理因为大模型截断，导致没有结尾 ``` 的情况
        # 同样使用 (?m)^\s*``` 确保是顶格的 markdown 代码块
        unclosed_search = re.search(
            r'(?m)^\s*```[^\n]*\n(.*)$',
            content,
            re.DOTALL,
        )
        if unclosed_search:
            inner_text = unclosed_search.group(1)
            # 确保提取的内容里不再包含顶格的 ```（说明确实是最后一个未闭合的块）
            if not re.search(r'(?m)^\s*```', inner_text):
                return inner_text.strip()

        return content

    def _extract_done_marker(self, content: str) -> tuple[str, bool]:
        """
        从模型输出中剥离续写完成标记。
        """
        text = content or ""
        done = "<CGR_DONE>" in text
        return text.replace("<CGR_DONE>", "").strip(), done

    def _strip_outer_code_fences(self, content: str, code_lang: str) -> str:
        """
        去掉源码文件外层可能残留的 fenced code block 标记。

        这里只移除“最外层、顶格”的围栏，不处理 doc 注释中的 `/// ````
        之类合法示例，以免再次误伤文档注释。
        """
        if not code_lang:
            return content

        lines = content.splitlines()
        if not lines:
            return content

        start = 0
        end = len(lines)

        while start < end and not lines[start].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1

        # 头部残留处理
        if start < end:
            first = lines[start].strip()
            # 兼容任意格式的 ``` 开头，例如 ```rust, ```, ```Rust 等
            # 不用担心误伤合法代码，因为正常 Rust 代码绝不会以 ``` 顶格开头
            if first.startswith("```"):
                start += 1
                
        # 尾部残留处理
        if end > start:
            last = lines[end - 1].strip()
            # 兼容任意格式的 ``` 结尾
            if last.startswith("```"):
                end -= 1

        return "\n".join(lines[start:end]).strip()

    def _strip_inline_test_modules(self, content: str) -> str:
        """
        当 generate_tests=false 时，移除源文件中的内联 #[cfg(test)] mod tests。
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

    def _strip_last_lone_closing_brace(self, text: str) -> str:
        """
        从已累计内容末尾移除一个孤立的 `}` 行。
        用于处理续写时模型过早把当前作用域提前收尾的情况。
        """
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and lines[-1].strip() == "}":
            lines.pop()
        return "\n".join(lines).rstrip()

    def _merge_continuation_chunk(self, accumulated: str, chunk: str, code_lang: str, done: bool) -> str:
        """
        合并续写片段。

        当前重点处理一种常见坏情况：
        - 上一轮尚未完成，但模型已经提前输出了一个结尾 `}`
        - 下一轮又继续输出缩进代码，导致后续代码落到作用域之外
        """
        if not chunk:
            return accumulated.strip()

        merged = accumulated or ""
        first_nonempty_line = ""
        for line in chunk.splitlines():
            if line.strip():
                first_nonempty_line = line
                break

        if code_lang == "rust" and not done and merged:
            # 如果下一轮一开始就是缩进代码/属性/文档注释，而上一轮刚好以一个孤立 `}` 结束，
            # 那大概率是模型过早闭合了外层块；这里保守移除一个 `}` 再继续拼接。
            looks_like_inner_continuation = (
                first_nonempty_line.startswith((" ", "\t"))
                or first_nonempty_line.lstrip().startswith(("#[", "///", "//!"))
            )
            trailing_lines = [line for line in merged.splitlines() if line.strip()]
            last_nonempty_line = trailing_lines[-1].strip() if trailing_lines else ""
            if looks_like_inner_continuation and last_nonempty_line == "}":
                merged = self._strip_last_lone_closing_brace(merged)

        if merged and not merged.endswith(("\n", "\r")):
            merged += "\n"
        merged += chunk
        return merged.strip()

    def _generate_with_continuation(
        self,
        system_prompt: str,
        user_prompt: str,
        code_lang: str = "rust",
        max_rounds: int = 4,
        label: str = "",
    ) -> str:
        """
        对长代码/长配置启用续写式生成，避免单次长响应直接截断。
        """
        accumulated = ""
        self._last_generation_completed = False
        self._last_generation_rounds = 0

        initial_prompt = (
            user_prompt
            + "\n\n额外要求：\n"
            + "1. 如果一次无法输出完整内容，请先输出前半部分，并在真正完成时仅在末尾追加 <CGR_DONE>\n"
            + "2. 如果尚未完成，不要输出 <CGR_DONE>\n"
            + "3. 续写时不要重复已输出内容，要从上一次结尾处直接继续\n"
            + "4. 如果尚未完成，不要为了让当前片段看起来完整而提前补最终收尾的大括号、结束模块或结束文件\n"
            + "5. 除最终内容和 <CGR_DONE> 外，不要输出解释\n"
        )

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': initial_prompt}
        ]

        for round_index in range(1, max_rounds + 1):
            self.llm.set_request_label(f"{label or 'Rust 生成'} [round {round_index}]")
            response = self.llm.generate(messages)
            chunk_raw = response[0]
            chunk_without_marker, done = self._extract_done_marker(chunk_raw)
            chunk = self._extract_generated_content(chunk_without_marker, code_lang=code_lang)
            chunk = self._strip_outer_code_fences(chunk, code_lang)
            chunk, _ = self._extract_done_marker(chunk)

            if chunk:
                accumulated = self._merge_continuation_chunk(accumulated, chunk, code_lang, done)

            if done:
                self._last_generation_completed = True
                self._last_generation_rounds = round_index
                return accumulated.strip()

            if round_index == max_rounds:
                break

            continue_prompt = (
                "上一次输出尚未完成，请从刚才的最后位置直接继续，不要重复前文。\n"
                "只输出剩余内容；如果这次完成，请仅在末尾追加 <CGR_DONE>。\n"
                "如果尚未完成，不要提前补最终收尾的大括号、结束模块或结束文件。"
            )
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'assistant', 'content': accumulated},
                {'role': 'user', 'content': continue_prompt},
            ]

        self._last_generation_rounds = max_rounds
        return accumulated.strip()

    def _looks_like_invalid_cargo_toml(self, content: str) -> bool:
        """
        对 Cargo.toml 做轻量校验。
        如果明显混入 Rust 源码，或缺少最基本的 TOML 结构，则认为不可靠。
        """
        text = (content or "").strip()
        lower = text.lower()

        rust_markers = [
            "pub mod ",
            "pub struct ",
            "pub enum ",
            "pub fn ",
            "\nfn ",
            "\nimpl ",
            "\ntrait ",
            "use crate::",
            "mod ",
        ]
        if any(marker in lower for marker in rust_markers):
            return True

        if "[package]" not in lower:
            return True

        if "name =" not in lower or "version =" not in lower or "edition =" not in lower:
            return True

        return False

    def _build_fallback_cargo_toml(self) -> str:
        """
        当模型生成的 Cargo.toml 明显失真时，回退到最小可用配置。
        """
        return f"""[package]
name = "{self.project_name}"
version = "0.1.0"
edition = "2021"

[dependencies]
"""

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

    def _sanitize_cargo_toml_for_config(self, content: str) -> str:
        """
        按当前配置裁剪 Cargo.toml，避免引用未生成的 examples / benches。
        """
        sanitized = content

        if not getattr(self.config, "generate_examples", False):
            sanitized = self._remove_toml_array_table_blocks(sanitized, "example")

        if not getattr(self.config, "generate_benches", False):
            sanitized = self._remove_toml_array_table_blocks(sanitized, "bench")
            sanitized = re.sub(r'(?m)^\s*criterion\s*=\s*".*?"\s*\n?', '', sanitized)

        # 简单收缩多余空行，保持 Cargo.toml 可读。
        sanitized = re.sub(r'\n{3,}', '\n\n', sanitized).strip() + "\n"
        return sanitized

    def _get_file_specific_generation_requirements(self, file_path: str) -> str:
        """
        根据文件职责增加少量核心约束。
        这里故意保持克制，只保留全局收益高、且不容易让上下文膨胀的规则。
        """
        normalized = file_path.replace("\\", "/").lower()
        hints = []

        if normalized.endswith("lib.rs"):
            hints.extend([
                "- 如果这是 lib.rs，请确保把当前项目中实际生成的核心 src/*.rs 模块通过 mod / pub mod 引入，而不是只保留空壳入口。",
                "- lib.rs 的重导出应与已生成模块实际存在的类型和函数保持一致。",
            ])

        return "\n".join(hints)

    def _sanitize_file_content_before_write(self, file_path: str, content: str) -> str:
        """
        在本地对易错的包装/格式问题做轻量清洗，避免浪费模型上下文。
        """
        sanitized = content or ""
        normalized = file_path.replace("\\", "/").lower()

        if normalized.endswith(".rs"):
            sanitized = self._strip_outer_code_fences(sanitized, "rust")
            if not getattr(self.config, "generate_tests", False):
                sanitized = self._strip_inline_test_modules(sanitized)
        elif self._is_cargo_toml(file_path):
            sanitized = self._strip_outer_code_fences(sanitized, "toml")

        return sanitized.strip() + ("\n" if sanitized.strip() else "")

    def _looks_like_invalid_readme(self, content: str) -> bool:
        """
        对 README.md 做轻量校验。
        如果 README 主体明显退化为整段 Rust 实现/测试源码，则认为不可靠。
        """
        text = (content or "").strip()
        if not text:
            return True

        lowered = text.lower()
        rust_markers = [
            "pub struct ",
            "pub enum ",
            "pub trait ",
            "pub fn ",
            "\nfn ",
            "\nimpl ",
            "\nmod ",
            "use crate::",
            "#[test]",
            "assert_eq!",
        ]
        markdown_markers = [
            "# ",
            "## ",
            "- ",
            "* ",
            "```",
            "cargo ",
            "readme",
            "usage",
            "example",
            "示例",
            "用法",
            "构建",
            "测试",
        ]

        rust_hits = sum(1 for marker in rust_markers if marker in lowered)
        markdown_hits = sum(1 for marker in markdown_markers if marker in lowered)
        return rust_hits >= 4 and markdown_hits <= 2

    def _build_fallback_readme(self) -> str:
        """
        当 README.md 生成异常时，回退到最小说明文档。
        """
        return f"""# {self.project_name}

这是自动生成的 Rust 项目。

## 当前状态

README 由生成流程回退为最小版本，因为原始输出疑似混入了大量源码内容。

## 构建

```bash
cargo build
```

## 测试

```bash
cargo test
```

## 说明

- 具体 API 请查看 `src/` 目录源码
- 该文档可在代码稳定后再补充详细示例与设计说明
"""
    
    def create_rust_project(self, project_name: str, output_dir: str) -> str:
        """
        创建新的 Rust 项目
        
        Args:
            project_name: 项目名称
            output_dir: 输出目录
            
        Returns:
            项目路径
        """
        print(f"创建 Rust 项目：{project_name}")
        
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 切换到输出目录并创建项目
        project_path = os.path.join(output_dir, project_name)
        
        self.project_name = project_name
        self.project_path = project_path
        
        if os.path.exists(project_path):
            if self.continue_mode:
                print(f"项目已存在，按 --continue 续跑，复用旧项目目录：{project_path}")
                self.generation_plan = self._load_generation_plan()
                self.api_contract = self._load_api_contract()
                if not os.path.exists(os.path.join(project_path, "Cargo.toml")):
                    print("检测到项目目录缺少 Cargo.toml，重新初始化 cargo 项目")
                    # cmd = f"cd {output_dir} && cargo new {project_name} --lib"
                    cmd = f"cd {output_dir} && cargo new {project_name}"
                    print(f"执行命令：{cmd}")
                    result = run(cmd)
                    print(f"项目创建成功：{result}")
                return project_path

            print(f"项目已存在，默认执行全量重建，删除旧项目目录：{project_path}")
            shutil.rmtree(project_path)

        self.generation_plan = {}
        self.api_contract = {"project_name": project_name, "files": {}}

        # 使用 cargo new 创建项目
        # cmd = f"cd {output_dir} && cargo new {project_name} --lib"
        cmd = f"cd {output_dir} && cargo new {project_name}"
        print(f"执行命令：{cmd}")
        result = run(cmd)
        print(f"项目创建成功：{result}")
        
        return project_path
    
    def load_documents(self, doc_paths: List[str]) -> Dict[str, str]:
        """
        加载项目文档
        
        Args:
            doc_paths: 文档路径列表
            
        Returns:
            文档内容字典
        """
        print(f"加载项目文档：{doc_paths}")
        
        self.doc_paths = doc_paths
        self.doc_contents = {}
        
        for doc_path in doc_paths:
            if os.path.isfile(doc_path):
                # 单个文件
                try:
                    with open(doc_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    content = self._clip_document_content(doc_path, content)
                    self.doc_contents[doc_path] = content
                    print(f"加载文件：{doc_path} ({len(content)} 字符)")
                except Exception as e:
                    print(f"加载文件失败 {doc_path}: {e}")
            elif os.path.isdir(doc_path):
                # 目录，加载目录下所有 markdown 文件
                for root, dirs, files in os.walk(doc_path):
                    for file in files:
                        if file.endswith('.md'):
                            file_path = os.path.join(root, file)
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read()
                                content = self._clip_document_content(file_path, content)
                                self.doc_contents[file_path] = content
                                print(f"加载文件：{file_path} ({len(content)} 字符)")
                            except Exception as e:
                                print(f"加载文件失败 {file_path}: {e}")
            else:
                print(f"路径不存在：{doc_path}")
        
        return self.doc_contents

    def attach_existing_project(self, project_path: str, project_name: Optional[str] = None, doc_paths: Optional[List[str]] = None):
        """
        绑定到已存在的 Rust 项目，便于后续做定点续写或修复。
        """
        self.project_path = project_path
        self.project_name = project_name or os.path.basename(project_path.rstrip("\\/"))
        self.generation_plan = self._load_generation_plan()
        self.api_contract = self._load_api_contract()
        if doc_paths:
            self.load_documents(doc_paths)

    def build_project_generation_context(self, include_docs: bool = False, max_doc_chars: int = 12000) -> str:
        """
        构造现有项目的生成上下文，供定点续写使用。
        """
        parts = []
        project_structure = self.generation_plan.get("project_structure", "")
        implementation_plan = self.generation_plan.get("implementation_plan", "")

        if project_structure:
            parts.append(f"项目结构：\n{project_structure}")
        if implementation_plan:
            parts.append(f"实现计划：\n{implementation_plan}")

        api_contract_context = self._build_api_contract_context()
        if api_contract_context:
            parts.append(f"当前接口契约：\n{api_contract_context}")

        if self.source_context_summary:
            parts.append(f"原始 C 源码摘要：\n{self.source_context_summary}")

        if self.tool_interface_constraints:
            parts.append(f"工具接口保持约束：\n{self.tool_interface_constraints}")

        if include_docs and self.doc_contents:
            doc_parts = []
            current_len = 0
            for path, content in self.doc_contents.items():
                chunk = f"\n=== 文档：{path} ===\n{content}\n"
                if current_len + len(chunk) > max_doc_chars:
                    remaining = max_doc_chars - current_len
                    if remaining > 0:
                        doc_parts.append(chunk[:remaining])
                    break
                doc_parts.append(chunk)
                current_len += len(chunk)
            docs_text = "".join(doc_parts).strip()
            if docs_text:
                parts.append(f"项目文档摘要：\n{docs_text}")

        return "\n\n".join(part for part in parts if part).strip()

    def regenerate_existing_file(
        self,
        file_path: str,
        system_prompt: str,
        user_prompt: str,
        code_lang: str = "rust",
        max_rounds: int = 5,
        label: str = "",
        status_note: str = "",
    ) -> str:
        """
        对现有文件做定点续写，并复用现有写回、截断保护和计划状态管理逻辑。
        """
        if not self.project_path:
            raise ValueError("project_path 未设置，无法续写现有文件")

        full_path = os.path.join(self.project_path, file_path)
        self._mark_generation_status(file_path, "in_progress", status_note or "regenerating_existing_file")

        code = self._generate_with_continuation(
            system_prompt,
            user_prompt,
            code_lang=code_lang,
            max_rounds=max_rounds,
            label=label or f"续写文件 {file_path}",
        )

        if not code or not str(code).strip():
            self._mark_generation_status(file_path, "failed", "empty_regeneration")
            return ""

        generation_completed = getattr(self, "_last_generation_completed", False)
        if self._looks_like_truncated_rust_source(file_path, code) and not generation_completed:
            self._mark_generation_status(file_path, "failed", "truncated_regeneration")
            return ""

        if self._is_cargo_toml(file_path) and self._looks_like_invalid_cargo_toml(code):
            code = self._build_fallback_cargo_toml()
        if self._is_cargo_toml(file_path):
            code = self._sanitize_cargo_toml_for_config(code)
        if self._is_readme(file_path) and self._looks_like_invalid_readme(code):
            code = self._build_fallback_readme()

        self._write_file(full_path, code)
        if file_path.replace("\\", "/").lower().endswith(".rs"):
            self._update_api_contract_for_file(file_path, code)
        self._mark_generation_status(file_path, "completed", status_note or "regenerated_existing_file")
        return code
    
    def _generate_project_structure(self) -> str:
        """
        根据文档生成项目结构设计
        
        Returns:
            项目结构设计描述
        """
        print("生成项目结构设计...")
        
        # 构建文档内容
        all_docs = ""
        for path, content in self.doc_contents.items():
            all_docs += f"\n=== 文档：{path} ===\n"
            all_docs += content
            all_docs += "\n"

        if self.source_context_summary:
            all_docs += "\n=== 原始 C 源码摘要 ===\n"
            all_docs += self.source_context_summary
            all_docs += "\n"

        if self.source_interface_summary:
            all_docs += "\n=== 原始 C 对外接口事实 ===\n"
            all_docs += self.source_interface_summary
            all_docs += "\n"

        if self.tool_interface_constraints:
            all_docs += "\n=== 工具接口保持约束 ===\n"
            all_docs += self.tool_interface_constraints
            all_docs += "\n"
        
        # 构建提示
        prompt = prompt_manager.get('rust_agent', 'generate_project_structure_prompt',
                                   project_name=self.project_name,
                                   all_docs=all_docs)

        sys_prompt = prompt_manager.get('rust_agent', 'generate_project_structure_system_prompt')

        structure_result = self._generate_with_continuation(
            sys_prompt,
            prompt,
            code_lang="",
            max_rounds=4,
            label="项目结构设计",
        )
        
        print(f"原始设计结果：{structure_result}")
        # # 提取 project_structure 标签内容
        # if '<project_structure>' in structure_result:
        #     parts = structure_result.split('<project_structure>')
        #     structure = parts[1].split('</project_structure>')[0].strip()
        # else:
        #     structure = structure_result
 
        print("项目结构设计完成")
        return structure_result
    
    def _generate_implementation_plan(self, project_structure: str, files_to_generate: []) -> str:
        """
        生成详细的实现计划
        
        Args:
            project_structure: 项目结构设计
            
        Returns:
            实现计划
        """
        print("生成实现计划...")
        
        prompt = prompt_manager.get('rust_agent', 'generate_implementation_plan_prompt',
                                   project_structure=project_structure,
                                   files_to_generate=files_to_generate)

        if self.source_context_summary:
            prompt += f"\n\n补充的原始 C 源码摘要：\n{self.source_context_summary}\n"
        if self.source_interface_summary:
            prompt += f"\n\n补充的原始 C 对外接口事实：\n{self.source_interface_summary}\n"
        if self.tool_interface_constraints:
            prompt += f"\n\n必须遵守的工具接口保持约束：\n{self.tool_interface_constraints}\n"

        sys_prompt = prompt_manager.get('rust_agent', 'generate_implementation_plan_system_prompt')

        plan_result = self._generate_with_continuation(
            sys_prompt,
            prompt,
            code_lang="",
            max_rounds=4,
            label="实现计划",
        )
        
        # 提取 implementation_plan 标签内容
        if '<implementation_plan>' in plan_result:
            parts = plan_result.split('<implementation_plan>')
            plan = parts[1].split('</implementation_plan>')[0].strip()
        else:
            plan = plan_result
        
        print("实现计划制定完成")
        return plan
    
    def _generate_code(self, file_path: str, context: str, implementation_plan: str) -> str:
        """
        生成单个文件的代码
        
        Args:
            file_path: 文件路径
            file_type: 文件类型（lib.rs, main.rs, mod.rs 等）
            context: 上下文信息（项目结构、其他文件内容等）
            implementation_plan: 实现计划
            
        Returns:
            生成的代码
        """
        if self._is_cargo_toml(file_path):
            prompt = f"""请为当前 Rust 项目生成 Cargo.toml 文件内容。

要求：
1. 只输出最终的 Cargo.toml 内容，不要输出解释
2. 输出必须是合法的 TOML，而不是 Rust 代码
3. 必须包含 [package]、[dependencies]，按需要可包含 [features]、[dev-dependencies]
4. package.name 应与当前项目名称一致：{self.project_name}
5. edition 默认使用 2021
6. 不要生成 pub mod、fn、struct、impl 等 Rust 源码
7. 如果暂时不确定某些依赖，优先保持 dependencies 简洁、可编译

文件路径：
{file_path}

项目上下文：
{context}

实现计划：
{implementation_plan}
"""
            sys_prompt = "你是一个擅长生成 Rust 工程配置文件的助手。请只输出合法的 Cargo.toml 内容，不要输出解释。"
        elif self._is_readme(file_path):
            prompt = f"""请为当前 Rust 项目生成 README.md 文档内容。

要求：
1. 只输出最终的 Markdown 文档，不要输出解释
2. 输出必须是 README 文档，而不是 Rust 源码文件
3. 可以包含少量示例代码块，但不要粘贴整文件实现源码
4. 重点包含：项目简介、当前状态、构建方式、测试方式、最小使用示例
5. 不要把大量测试代码、trait/struct/impl、长段实现细节写进 README
6. 内容保持简洁，以项目说明为主

文件路径：
{file_path}

项目上下文：
{context}

实现计划：
{implementation_plan}
"""
            sys_prompt = "你是一个擅长编写 Rust 项目 README.md 的助手。请只输出 Markdown 文档，不要输出解释，也不要把 README 写成源码文件。"
        else:
            prompt = prompt_manager.get('rust_agent', 'generate_code_prompt',
                                       file_path=file_path,
                                       context=context,
                                       implementation_plan=implementation_plan)
            source_context = self._build_relevant_source_context_for_file(file_path)
            if source_context:
                prompt += f"\n\n最相关的原始 C 源码片段：\n{source_context}\n"
            if self.tool_interface_constraints:
                prompt += f"\n\n必须遵守的工具接口保持约束：\n{self.tool_interface_constraints}\n"
            extra_requirements = self._get_file_specific_generation_requirements(file_path)
            if extra_requirements:
                prompt += f"\n\n额外文件级要求：\n{extra_requirements}\n"

            sys_prompt = prompt_manager.get('rust_agent', 'generate_code_system_prompt')

        code = self._generate_with_continuation(
            sys_prompt,
            prompt,
            code_lang=(
                "toml"
                if self._is_cargo_toml(file_path)
                else ("" if self._is_readme(file_path) else "rust")
            ),
            max_rounds=5,
            label=f"代码生成 {file_path}",
        )
        return code

    def _generate_skeleton(self, file_path: str, context: str, implementation_plan: str) -> str:
        """
        先生成文件骨架，尽量只保留模块结构、类型定义和函数签名。
        
        Args:
            file_path: 文件路径
            context: 上下文信息
            implementation_plan: 实现计划
            
        Returns:
            生成的骨架代码
        """
        if self._is_cargo_toml(file_path):
            prompt = f"""请先为下面的 Cargo.toml 生成“配置骨架”，用于后续逐步补全依赖和特性。

要求：
1. 只输出最终的 Cargo.toml 内容，不要输出解释
2. 输出必须是合法的 TOML，而不是 Rust 代码
3. 至少包含 [package]、[dependencies]
4. package.name 应与当前项目名称一致：{self.project_name}
5. edition 默认使用 2021
6. 如果暂时不确定依赖版本，可以先保持 [dependencies] 为空表，但结构要完整
7. 不要生成 pub mod、fn、struct、impl 等 Rust 源码

文件路径：
{file_path}

项目上下文：
{context}

实现计划：
{implementation_plan}
"""
            return self._generate_with_continuation(
                '你是一个擅长生成 Cargo.toml 骨架的助手。请只输出合法的 TOML 配置，不要输出解释。',
                prompt,
                code_lang="toml",
                max_rounds=4,
                label=f"骨架生成 {file_path}",
            )

        extra_requirements = self._get_skeleton_extra_requirements(file_path)
        source_context = self._build_relevant_source_context_for_file(file_path, max_records=5, max_chars=9000)
        prompt = f"""请先为下面的 Rust 文件生成“代码骨架”，用于后续逐步补全实现。

要求：
1. 只输出最终代码，不要输出解释
2. 保留模块结构、use、struct、enum、trait、type alias、函数签名
3. 函数体可以先使用 todo!()、unimplemented!() 或最小占位实现
4. 尽量优先把结构体、类型定义、公开接口写完整
5. 不要省略必要的 mod/pub/use 声明
6. 输出必须是完整的单文件 Rust 代码
7. 对数据结构类文件，优先输出 struct/enum/type 等类型定义，再输出函数签名和实现占位

附加要求：
{extra_requirements}

文件路径：
{file_path}

项目上下文：
{context}

实现计划：
{implementation_plan}

最相关的原始 C 源码片段：
{source_context or '当前没有可用的源码片段，请至少严格遵循已有上下文与接口事实。'}
"""

        if self.tool_interface_constraints:
            prompt += f"\n必须遵守的工具接口保持约束：\n{self.tool_interface_constraints}\n"

        return self._generate_with_continuation(
            '你是一个擅长生成 Rust 工程骨架的代码助手。请只输出代码，不要输出解释。',
            prompt,
            code_lang="rust",
            max_rounds=4,
            label=f"骨架生成 {file_path}",
        )

    def _get_skeleton_extra_requirements(self, file_path: str) -> str:
        """
        根据文件路径生成骨架阶段的附加要求。
        对 node/type/data/error 等文件额外强调类型定义要尽量完整。
        """
        normalized = file_path.replace("\\", "/").lower()
        file_name = os.path.basename(normalized)
        hints = ["- 优先保证代码骨架完整、稳定、可继续补全。"]

        # 这几类文件通常承载核心数据结构和公共类型，骨架阶段尽量不要只留下空壳。
        if any(token in normalized for token in ["node", "type", "data", "error"]):
            hints.extend([
                "- 该文件优先补全结构体、类型别名、错误枚举和公开字段，不要只给空壳。",
                "- 生成顺序上，优先写类型定义，再写关联方法、辅助函数和实现占位。",
                "- 如果包含 struct，请尽量把字段写全；字段名、字段类型和可见性尽量一次写完整。",
                "- 如果包含 type alias，请尽量把类型别名写全，不要只保留占位名字。",
                "- 如果包含错误类型，请尽量把错误枚举分支写全，至少先把主要错误变体列完整。",
                "- 如果暂时无法确定具体实现，也优先把数据结构定义完整，再把函数体留作后续补全。",
            ])

        if "error" in normalized:
            hints.extend([
                "- 如果这是错误定义文件，优先给出统一的错误枚举、错误消息和必要的 From/Result 类型约定。",
                "- 错误类型骨架应尽量覆盖参数错误、状态错误、边界错误等主要失败场景。",
            ])

        if any(token in normalized for token in ["node", "data"]):
            hints.extend([
                "- 如果这是节点或数据文件，优先写清核心字段、所有权关系以及必要的构造接口。",
                "- 对树节点、链表节点或容器数据结构，先保证字段定义完整，再补辅助方法。",
            ])

        if "type" in normalized:
            hints.extend([
                "- 如果这是类型定义文件，优先给出公共类型别名、关键枚举和对外暴露的数据模型。",
                "- 类型定义尽量与后续模块共享，避免只生成临时占位类型。",
            ])

        return "\n".join(hints)

    def _implement_from_skeleton(self, file_path: str, skeleton_code: str, context: str, implementation_plan: str) -> str:
        """
        基于已有骨架继续补全具体实现。
        
        Args:
            file_path: 文件路径
            skeleton_code: 骨架代码
            context: 上下文信息
            implementation_plan: 实现计划
            
        Returns:
            补全后的代码
        """
        if self._is_cargo_toml(file_path):
            prompt = f"""下面已经有一个 Cargo.toml 配置骨架，请在保持整体结构稳定的前提下，继续补全其中的依赖和配置内容。

要求：
1. 只输出最终的 Cargo.toml 内容，不要输出解释
2. 输出必须是合法的 TOML，而不是 Rust 代码
3. 保留已有的 [package]、[dependencies] 等配置结构
4. 在此基础上补全缺失的依赖、features 或 dev-dependencies
5. 不要生成 pub mod、fn、struct、impl 等 Rust 源码
6. 如果上下文不足，优先保持配置简洁和可解析

文件路径：
{file_path}

当前配置骨架：
{skeleton_code}

项目上下文：
{context}

实现计划：
{implementation_plan}
"""
            return self._generate_with_continuation(
                '你是一个擅长补全 Cargo.toml 的助手。请只输出合法的 TOML 配置，不要输出解释。',
                prompt,
                code_lang="toml",
                max_rounds=5,
                label=f"补全实现 {file_path}",
            )

        source_context = self._build_relevant_source_context_for_file(file_path)
        prompt = f"""下面已经有一个 Rust 文件骨架，请在保持整体结构稳定的前提下，继续补全其中的实现内容。

要求：
1. 只输出最终完整代码，不要输出解释
2. 尽量保留已有结构体、类型定义、函数签名和模块结构
3. 在此基础上逐步补全函数实现
4. 如果某些内容暂时无法确定，可以保留少量占位实现，但应优先补全核心逻辑
5. 输出必须是完整的单文件 Rust 代码
6. 不要把骨架里已经写出的 struct 字段、type alias、enum 分支和公开接口回退成更空的版本
7. 如果骨架里已经有较完整的数据结构定义，补全实现时应尽量保持这些定义不变
8. 优先在现有骨架上增补实现，不要为了改写实现而删除已有类型信息

文件路径：
{file_path}

当前骨架代码：
{skeleton_code}

项目上下文：
{context}

实现计划：
{implementation_plan}

最相关的原始 C 源码片段：
{source_context or '当前没有可用的源码片段，请至少严格遵循已有上下文与接口事实。'}
"""
        extra_requirements = self._get_file_specific_generation_requirements(file_path)
        if extra_requirements:
            prompt += f"\n额外文件级要求：\n{extra_requirements}\n"
        if self.tool_interface_constraints:
            prompt += f"\n必须遵守的工具接口保持约束：\n{self.tool_interface_constraints}\n"

        return self._generate_with_continuation(
            '你是一个擅长在既有 Rust 骨架上逐步补全实现的代码助手。请只输出代码，不要输出解释。',
            prompt,
            code_lang="rust",
            max_rounds=5,
            label=f"补全实现 {file_path}",
        )

    def _sort_files_for_generation(self, file_paths: List[str]) -> List[str]:
        """
        对文件生成顺序做轻量排序：
        优先生成类型、结构体、节点和错误定义，再生成其他实现文件。
        
        Args:
            file_paths: 文件路径列表
            
        Returns:
            排序后的文件路径列表
        """
        def sort_key(file_path: str):
            normalized = file_path.replace("\\", "/").lower()
            file_name = os.path.basename(normalized)

            if file_name == "Cargo.toml":
                return (0, file_name)
            if file_name in {".gitignore", "README.md"}:
                return (1, file_name)
            # 优先生成核心数据结构和公共类型定义文件。
            if any(token in normalized for token in ["node", "type", "data", "error"]):
                return (2, normalized)
            if any(token in normalized for token in ["model", "struct"]):
                return (3, normalized)
            if file_name.endswith("mod.rs"):
                return (4, normalized)
            # lib.rs 往往依赖前面的模块、类型和导出关系，尽量靠后生成。
            if file_name == "lib.rs":
                return (6, normalized)
            return (5, normalized)

        return sorted(file_paths, key=sort_key)

    def _extract_project_file_block(self, project_structure: str) -> str:
        """
        尽量从模型输出中提取项目文件结构块。
        优先读取 <project_file> 标签；若缺失则回退为全文。
        """
        text = (project_structure or "").strip()
        if '<project_file>' in text and '</project_file>' in text:
            try:
                return text.split('<project_file>', 1)[1].split('</project_file>', 1)[0].strip()
            except Exception:
                return text
        return text

    def _normalize_tree_line(self, line: str) -> str:
        """
        清理 tree 连接符，保留缩进和文件名主体。
        """
        return (
            line.rstrip()
            .replace('│', ' ')
            .replace('├', ' ')
            .replace('└', ' ')
            .replace('─', ' ')
            .replace('•', ' ')
            .replace('·', ' ')
            .replace('\t', '    ')
        )

    def _looks_like_path_line(self, text: str) -> bool:
        """
        判断一行是否像真实文件/目录路径，避免把说明文字当成路径。
        """
        candidate = (text or "").strip()
        if not candidate:
            return False

        lowered = candidate.lower()
        rejected_tokens = [
            '<project_file>', '</project_file>',
            '<implementation_plan>', '</implementation_plan>',
            '里面有', '用于', '说明', '包含', '例如', '比如',
            '项目结构', '目录结构', '主要模块', '关键函数', '错误处理',
            '生成顺序', '实现计划', '设计步骤', 'important', 'note:'
        ]
        if any(token in candidate for token in rejected_tokens):
            return False
        if any(token in lowered for token in ['for example', 'must use', 'tree format']):
            return False

        if re.search(r'[\u4e00-\u9fff]', candidate):
            if '/' not in candidate and '\\' not in candidate and not re.search(r'\.\w+$', candidate):
                return False

        base = os.path.basename(candidate.rstrip('/'))
        allowed_names = {'cargo.toml', 'readme.md', '.gitignore', 'build.rs', 'lib.rs', 'main.rs', 'mod.rs'}
        if candidate.endswith('/'):
            return True
        if base.lower() in allowed_names:
            return True
        if '/' in candidate or '\\' in candidate:
            return True
        if re.search(r'\.(rs|toml|md|json|yml|yaml)$', candidate, re.IGNORECASE):
            return True
        return False

    def _clean_relative_project_path(self, path: str, root_name: str) -> Optional[str]:
        """
        规范化解析得到的项目内相对路径，并去掉根目录前缀。
        """
        candidate = (path or "").strip().strip('`').strip('"').strip("'")
        candidate = candidate.replace('\\', '/')
        candidate = re.sub(r'/+', '/', candidate).strip()
        candidate = candidate.rstrip('：:;；，,。. ')

        if not candidate:
            return None

        normalized_root = (root_name or "").rstrip('/')
        if normalized_root:
            prefix = normalized_root + '/'
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
            elif candidate == normalized_root:
                return None

        if not candidate or not self._looks_like_path_line(candidate):
            return None

        return candidate

    def _parse_new_files_to_generate(self, tag_content: str, fallback_files: List[str]) -> List[str]:
        """
        解析实现计划中的 <new_files_to_generate>。
        兼容 JSON 列表、Python 风格列表、编号列表和逐行列表，避免把整段编号列表当成单个文件名。
        """
        text = (tag_content or "").strip()
        if not text:
            return fallback_files

        parsed_files: List[str] = []

        # 优先尝试 JSON / Python 风格的引号列表。
        quoted_files = re.findall(r"['\"]([^'\"]+?)['\"]", text)
        if quoted_files:
            parsed_files.extend(quoted_files)
        else:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # 兼容：1. Cargo.toml / - src/lib.rs / * tests/foo.rs
                line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip()
                line = line.strip("[],'\"` ")

                # 如果一行里混有说明，只取第一个看起来像路径的片段。
                match = re.search(
                    r'(?:Cargo\.toml|README\.md|\.gitignore|build\.rs|\.cargo/[A-Za-z0-9_.-]+|(?:src|tests|examples|benches|include|\.github)(?:/[A-Za-z0-9_.-]+)+)',
                    line.replace("\\", "/"),
                    re.IGNORECASE,
                )
                candidate = match.group(0) if match else line
                parsed_files.append(candidate)

        cleaned_files: List[str] = []
        for item in parsed_files:
            cleaned = self._clean_relative_project_path(item, "")
            if cleaned:
                cleaned_files.append(cleaned)

        return self._sanitize_generation_file_list(cleaned_files or fallback_files)

    def _sanitize_generation_file_list(self, file_paths: List[str]) -> List[str]:
        """
        对最终生成文件列表做统一清洗：
        1. 过滤非法路径
        2. 应用 generate_tests/examples/benches 配置
        3. 去重保序
        """
        cleaned_paths: List[str] = []
        seen = set()

        for item in file_paths:
            cleaned = self._clean_relative_project_path(str(item), "")
            if not cleaned:
                continue

            normalized = cleaned.replace("\\", "/")
            lowered = normalized.lower()

            if "\n" in normalized or "\r" in normalized:
                continue
            if ":" in normalized and not re.match(r"^[A-Za-z]:", normalized):
                continue
            if normalized.startswith("../") or "/../" in normalized:
                continue

            if not self._is_supported_generation_file(normalized):
                print(f"跳过非核心生成文件：{normalized}")
                continue

            if lowered.startswith("tests/") and not getattr(self.config, "generate_tests", False):
                print(f"跳过测试文件生成：{normalized}")
                continue
            if lowered.startswith("examples/") and not getattr(self.config, "generate_examples", False):
                print(f"跳过示例文件生成：{normalized}")
                continue
            if lowered.startswith("benches/") and not getattr(self.config, "generate_benches", False):
                print(f"跳过 bench 文件生成：{normalized}")
                continue
            if lowered.startswith(".github/"):
                print(f"跳过 CI 配置文件生成：{normalized}")
                continue

            if normalized not in seen:
                seen.add(normalized)
                cleaned_paths.append(normalized)

        if not cleaned_paths:
            return ["Cargo.toml", "src/lib.rs", "README.md"]

        return cleaned_paths
    
    def _write_file(self, file_path: str, content: str):
        """
        写入文件内容
        
        Args:
            file_path: 文件路径
            content: 文件内容
        """
        content = self._sanitize_file_content_before_write(file_path, content)

        if content is None or not str(content).strip():
            print(f"跳过空内容文件写入：{file_path}")
            return

        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"写入文件：{file_path}")
        self.generated_files.append(file_path)
    
    def _update_cargo_toml(self, dependencies: Dict[str, str]):
        """
        更新 Cargo.toml 文件的依赖
        
        Args:
            dependencies: 依赖字典 {包名：版本}
        """
        cargo_toml_path = os.path.join(self.project_path, "Cargo.toml")
        
        if not os.path.exists(cargo_toml_path):
            print(f"Cargo.toml 不存在：{cargo_toml_path}")
            return
        
        with open(cargo_toml_path, 'r', encoding='utf-8') as f:
            content = f.read()

        content = self._ensure_dependencies_section(content)
        content, added = self._merge_dependencies_into_toml(content, dependencies)
        content = self._sanitize_cargo_toml_for_config(content)
        if not added:
            print("Cargo.toml 依赖无需更新")
            return

        # 写回前尽量做一次 TOML 解析校验；旧环境缺少 tomllib 时跳过。
        if tomllib is not None:
            tomllib.loads(content)

        with open(cargo_toml_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"更新 Cargo.toml 依赖")

    def _ensure_dependencies_section(self, content: str) -> str:
        """
        确保 Cargo.toml 中存在 [dependencies] 段。
        """
        if re.search(r'(?m)^\[dependencies\]\s*$', content):
            return content

        suffix = "" if content.endswith("\n") else "\n"
        return f"{content}{suffix}\n[dependencies]\n"

    def _merge_dependencies_into_toml(self, content: str, dependencies: Dict[str, str]) -> tuple[str, bool]:
        """
        只在 [dependencies] 段内补充缺失依赖，避免全文件模糊匹配。
        """
        section_match = re.search(r'(?m)^\[dependencies\]\s*$', content)
        if not section_match:
            return content, False

        section_start = section_match.end()
        next_section_match = re.search(r'(?m)^\[[^\]]+\]\s*$', content[section_start:])
        section_end = section_start + next_section_match.start() if next_section_match else len(content)

        before = content[:section_start]
        section_body = content[section_start:section_end]
        after = content[section_end:]

        existing_deps = set()
        for line in section_body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            match = re.match(r'^([A-Za-z0-9_-]+)\s*=', stripped)
            if match:
                existing_deps.add(match.group(1))

        additions = []
        for pkg, version in dependencies.items():
            if pkg not in existing_deps:
                additions.append(f'{pkg} = "{version}"')

        if not additions:
            return content, False

        normalized_body = section_body
        if normalized_body and not normalized_body.endswith("\n"):
            normalized_body += "\n"
        normalized_body += "\n".join(additions) + "\n"
        return before + normalized_body + after, True

    def _should_detect_dependencies(self, file_path: str) -> bool:
        """
        仅对 Rust 源文件做依赖检测，避免 README 或配置文件误触发。
        """
        normalized = file_path.replace("\\", "/").lower()
        if self._is_cargo_toml(normalized) or self._is_readme(normalized):
            return False
        return normalized.endswith(".rs")
    
    def _detect_dependencies(self, context: str) -> Dict[str, str]:
        """
        从生成的代码中检测需要的依赖
        
        Args:
            context: 代码上下文
            
        Returns:
            依赖字典
        """
        # 常见的 Rust crate 依赖
        common_deps = {
            "serde": "1.0",
            "serde_json": "1.0",
            "thiserror": "1.0",
            "anyhow": "1.0",
            "log": "0.4",
            "env_logger": "0.10",
            "clap": "4.0",
            "tokio": "1.0",
            "async-std": "1.0",
            "futures": "0.3",
            "rand": "0.8",
            "regex": "1.0",
            "lazy_static": "1.4",
        }
        
        detected_deps = {}
        
        # 简单的检测逻辑（可以根据 use 语句判断）
        for crate_name in common_deps.keys():
            if f"use {crate_name}" in context or f"extern crate {crate_name}" in context:
                detected_deps[crate_name] = common_deps[crate_name]
        
        return detected_deps
    
    def generate_code(self) -> List[str]:
        """
        根据文档生成 Rust 代码
        
        Returns:
            生成的文件列表
        """
        print("开始生成 Rust 代码...")
        
        # 1. 生成项目结构设计
        project_structure = self._generate_project_structure()
        # 打印项目结构
        print("\n项目结构:")
        print(project_structure)
        # pause = input("按任意键继续...")
        # files_to_generate = self._parse_file_list(project_structure)
        # 打印解析后的文件列表
        # print("\n解析后的文件列表:")
        # for file in files_to_generate:
            # print(f"  {file['path']} ({file['type']})")
        # pause = input("按任意键继续...")

        # 2. 解析项目结构，生成文件列表
        files_to_generate = self._parse_file_list(project_structure)
        print(f"files_to_generate: {files_to_generate}")
        # pause = input("按任意键继续...")

        # 3. 生成实现计划
        implementation_plan = self._generate_implementation_plan(project_structure, files_to_generate)
        print(f"implementation_plan: {implementation_plan}")
        # pause = input("按任意键继续...")

        # 4. 提取新的文件列表顺序 (增加容错逻辑)
        if '<new_files_to_generate>' in implementation_plan and '</new_files_to_generate>' in implementation_plan:
            try:
                parts = implementation_plan.split('<new_files_to_generate>')
                tag_content = parts[1].split('</new_files_to_generate>')[0].strip()
                
                new_files_to_generate = self._parse_new_files_to_generate(tag_content, files_to_generate)
                
                print(f"成功从计划中提取新顺序: {new_files_to_generate}")
            except Exception as e:
                print(f"解析新文件列表失败，使用原始顺序。错误: {e}")
                new_files_to_generate = self._sanitize_generation_file_list(files_to_generate)
        else:
            print("模型未提供 <new_files_to_generate> 标签，使用原始文件顺序。")
            new_files_to_generate = self._sanitize_generation_file_list(files_to_generate)

        # 对生成顺序做轻量调整：优先结构体、类型和错误定义
        new_files_to_generate = self._sort_files_for_generation(new_files_to_generate)
        print(f"最终生成顺序: {new_files_to_generate}")
        # pause = input("按任意键继续...")

        # 5. 初始化持久化生成计划
        self._initialize_generation_plan(project_structure, implementation_plan, new_files_to_generate)
        self._ensure_api_contract_loaded()
        
        # 6. 逐个生成文件
        all_generated_code = {}
        context_parts = [
            f"项目结构：\n{project_structure}",
            f"实现计划：\n{implementation_plan}",
        ]
        if self.source_context_summary:
            context_parts.append(f"原始 C 源码摘要：\n{self.source_context_summary}")
        if self.source_interface_summary:
            context_parts.append(f"原始 C 对外接口事实：\n{self.source_interface_summary}")
        if self.tool_interface_constraints:
            context_parts.append(f"工具接口保持约束：\n{self.tool_interface_constraints}")
        context = "\n\n".join(context_parts) + "\n"
        
        for file_path in new_files_to_generate:
            plan_state = self.generation_plan.get("files", {}).get(file_path, {})
            if self.continue_mode and plan_state.get("status") == "completed" and self._is_completed_file_still_valid(file_path, plan_state):
                print(f"跳过已完成文件：{file_path}")
                context = self._append_existing_file_to_context(file_path, context)
                continue

            if self.continue_mode and self._is_nonempty_existing_file(file_path):
                print(f"检测到已有非空文件，标记为已完成并跳过：{file_path}")
                self._mark_generation_status(file_path, "completed", "existing_nonempty_file")
                context = self._append_existing_file_to_context(file_path, context)
                continue

            # file_type = file_info['type']
            # description = file_info.get('description', '')
            
            print(f"生成文件：{file_path}")
            self._mark_generation_status(file_path, "in_progress")
            
            # 生成代码
            api_contract_context = self._build_api_contract_context()
            file_context = context
            if api_contract_context:
                file_context += f"\n\n=== 当前接口契约 ===\n{api_contract_context}\n"
            skeleton_code = ""
            if getattr(self.config, "skeleton_first", False):
                print(f"先生成骨架：{file_path}")
                skeleton_code = self._generate_skeleton(file_path, file_context, implementation_plan)
                print(f"再基于骨架补全实现：{file_path}")
                code = self._implement_from_skeleton(file_path, skeleton_code, file_context, implementation_plan)
                if (not code or not str(code).strip()) and skeleton_code and str(skeleton_code).strip():
                    print(f"实现阶段返回空内容，保留骨架版本：{file_path}")
                    code = skeleton_code
            else:
                code = self._generate_code(file_path, file_context, implementation_plan)

            if not code or not str(code).strip():
                print(f"模型未生成有效内容，跳过该文件：{file_path}")
                self._mark_generation_status(file_path, "failed", "empty_generation")
                continue

            generation_completed = getattr(self, "_last_generation_completed", False)
            if self._looks_like_truncated_rust_source(file_path, code) and not generation_completed:
                if getattr(self.config, "skeleton_first", False) and skeleton_code and not self._looks_like_truncated_rust_source(file_path, skeleton_code):
                    print(f"检测到实现结果疑似截断，回退到骨架版本：{file_path}")
                    code = skeleton_code
                else:
                    print(f"检测到文件疑似截断，跳过写入：{file_path}")
                    self._mark_generation_status(file_path, "failed", "truncated_generation")
                    continue
            elif self._looks_like_truncated_rust_source(file_path, code) and generation_completed:
                print(f"检测到文件疑似截断，但续写已显式完成，保留写入：{file_path}")

            if file_path.replace("\\", "/").lower() == "src/lib.rs" and self._looks_like_doc_only_lib_rs(code):
                print("检测到 lib.rs 仅包含注释或文档，回退为最小模块入口")
                code = self._build_fallback_lib_rs(new_files_to_generate)

            # 对 Cargo.toml 做写入前保护，避免把 Rust 源码误写成配置文件。
            if self._is_cargo_toml(file_path) and self._looks_like_invalid_cargo_toml(code):
                print("检测到生成的 Cargo.toml 内容异常，回退到最小可用配置")
                code = self._build_fallback_cargo_toml()
            if self._is_cargo_toml(file_path):
                code = self._sanitize_cargo_toml_for_config(code)
            if self._is_readme(file_path) and self._looks_like_invalid_readme(code):
                print("检测到生成的 README.md 内容异常，回退到最小说明文档")
                code = self._build_fallback_readme()
            
            # 仅对 Rust 源文件做依赖检测，避免 README/文档示例污染 Cargo.toml。
            if self._should_detect_dependencies(file_path):
                deps = self._detect_dependencies(code)
                if deps:
                    print(f"检测到依赖：{deps}")
                    self._update_cargo_toml(deps)
            
            # 保存生成的代码
            all_generated_code[file_path] = code
            
            # 写入文件
            full_path = os.path.join(self.project_path, file_path)
            self._write_file(full_path, code)
            if file_path.replace("\\", "/").lower().endswith(".rs"):
                self._update_api_contract_for_file(file_path, code)
            self._mark_generation_status(file_path, "completed")
            
            # 更新上下文
            context += f"\n\n=== 已生成文件：{file_path} ===\n{code}\n"
        
        print(f"代码生成完成，共生成 {len(self.generated_files)} 个文件")
        return self.generated_files
    
    def _parse_file_list(self, project_structure: str) -> List[str]:
        """
        从项目结构描述中解析文件列表
        
        Args:
            project_structure: 项目结构描述
            
        Returns:
            文件信息列表
        """
        current_path = []
        paths = []

        tree_structure = self._extract_project_file_block(project_structure)
        print(tree_structure)

        lines_list = [self._normalize_tree_line(line) for line in tree_structure.splitlines() if line.strip()]
        if not lines_list:
            print("未解析到项目目录树，回退到最小文件集合。")
            return ["Cargo.toml", "src/lib.rs", "README.md"]

        first_line = lines_list[0].strip()
        root_name = first_line.rstrip('/')
        current_path = [root_name] if root_name else []

        for line in lines_list[1:]:
            if not line.strip():
                continue

            indent_level = len(line) - len(line.lstrip())
            original_name = line.strip()
            is_directory = original_name.endswith('/')
            name = original_name.rstrip('/')

            if not self._looks_like_path_line(original_name):
                continue

            current_path = current_path[:indent_level // 4]
            current_path.append(name)

            if not is_directory:
                full_path = '/'.join(current_path)
                cleaned_path = self._clean_relative_project_path(full_path, root_name)
                if cleaned_path:
                    paths.append(cleaned_path)

        if not paths:
            print("目录树解析为空，尝试从全文兜底提取路径。")
            fallback_candidates = re.findall(
                r'(?im)\b(?:Cargo\.toml|README\.md|\.gitignore|build\.rs|(?:src|tests|examples|benches)(?:/[A-Za-z0-9_.-]+)+)\b',
                tree_structure.replace('\\', '/')
            )
            for candidate in fallback_candidates:
                cleaned_path = self._clean_relative_project_path(candidate, root_name)
                if cleaned_path:
                    paths.append(cleaned_path)

        deduped_paths = []
        seen = set()
        for path in paths:
            if path not in seen:
                seen.add(path)
                deduped_paths.append(path)

        if not deduped_paths:
            print("项目结构解析失败，回退到最小文件集合。")
            return ["Cargo.toml", "src/lib.rs", "README.md"]

        return self._sanitize_generation_file_list(deduped_paths)
    
    def build_project(self) -> bool:
        """
        编译 Rust 项目
        
        Returns:
            是否编译成功
        """
        print(f"编译项目：{self.project_path}")
        
        cmd = f"cd {self.project_path} && cargo build"
        
        try:
            result = run(cmd)
            print(f"编译成功：{result}")
            return True
        except Exception as e:
            print(f"编译失败：{e}")
            return False
    
    def test_project(self) -> bool:
        """
        测试 Rust 项目
        
        Returns:
            是否测试通过
        """
        print(f"测试项目：{self.project_path}")
        
        cmd = f"cd {self.project_path} && cargo test"
        
        try:
            result = run(cmd)
            print(f"测试成功：{result}")
            return True
        except Exception as e:
            print(f"测试失败：{e}")
            return False
    
    def fmt_project(self):
        """格式化 Rust 项目代码"""
        print(f"格式化项目：{self.project_path}")
        
        cmd = f"cd {self.project_path} && cargo fmt"
        
        try:
            result = run(cmd)
            print(f"格式化完成：{result}")
        except Exception as e:
            print(f"格式化失败：{e}")
    
    def check_project(self) -> bool:
        """
        检查 Rust 项目
        
        Returns:
            是否检查通过
        """
        print(f"检查项目：{self.project_path}")
        
        cmd = f"cd {self.project_path} && cargo check"
        
        try:
            result = run(cmd)
            print(f"检查通过：{result}")
            return True
        except Exception as e:
            print(f"检查失败：{e}")
            return False
    
    def generate_from_docs(
        self,
        project_name: str,
        output_dir: str,
        doc_paths: List[str],
        c_project_path: str = "",
        source_json_path: str = "",
    ) -> bool:
        """
        根据文档生成完整的 Rust 项目（主入口方法）
        
        Args:
            project_name: 项目名称
            output_dir: 输出目录
            doc_paths: 文档路径列表
            c_project_path: 原始 C 项目路径
            source_json_path: 解析后的源码 JSON 路径
            
        Returns:
            是否成功
        """
        print("=" * 60)
        print("开始根据文档生成 Rust 项目")
        print("=" * 60)
        
        # 1. 创建 Rust 项目
        project_path = self.create_rust_project(project_name, output_dir)
        
        # 2. 加载项目文档
        self.load_documents(doc_paths)

        # 2.5. 绑定原始源码上下文，避免仅凭摘要文档猜测实现
        self.configure_source_context(
            c_project_path=c_project_path,
            source_json_path=source_json_path,
        )
        if self.source_json_path:
            print(f"已加载源码 JSON：{self.source_json_path}")
        if self.tool_interface_constraints:
            print("检测到工具/CLI 接口约束，后续生成将强制保持外部使用方式一致")
        
        # 3. 生成代码
        self.generate_code()
        
        print("=" * 60)
        print("Rust 项目生成完成")
        print("=" * 60)
        
        return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="根据项目文档生成 Rust 代码")
    parser.add_argument("project_name", help="项目名称")
    parser.add_argument("output_dir", help="输出目录")
    parser.add_argument("doc_paths", nargs="+", help="文档路径列表")
    parser.add_argument("--model_size", default="7", help="模型 size")
    
    args = parser.parse_args()
    
    model_name = f"Qwen2.5-Coder-{args.model_size}B-Instruct"
    
    # 初始化 agent
    agent = RustAgent(model_name=model_name)
    
    # 生成项目
    success = agent.generate_from_docs(
        project_name=args.project_name,
        output_dir=args.output_dir,
        doc_paths=args.doc_paths
    )
    
    if success:
        print(f"\n项目生成成功：{os.path.join(args.output_dir, args.project_name)}")
    else:
        print("\n项目生成失败")
        sys.exit(1)
