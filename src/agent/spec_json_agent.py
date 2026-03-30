import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config
from llm.model import Model


class SpecJsonAgent:
    """
    将 SpecAgent 产出的 markdown 文档压缩为机器更友好的 JSON 上下文。

    设计目标：
    1. 尽量不改动现有 SpecAgent / RustAgent 主流程
    2. 作为可选中间层插入：SpecAgent -> SpecJsonAgent -> RustAgent
    3. 即使模型压缩失败，也提供一个可用的回退 JSON
    """

    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.llm = Model(self.config)

    def compress_spec_docs(self, spec_output_dir: str, output_dir: Optional[str] = None) -> str:
        """
        压缩 SpecAgent 产出的文档，生成单个 JSON 文件。

        Args:
            spec_output_dir: SpecAgent 输出目录，通常是 output/c_docs
            output_dir: JSON 输出目录，默认写入 spec_output_dir/spec_json

        Returns:
            生成的 JSON 文件路径
        """
        spec_output_dir = os.path.abspath(spec_output_dir)
        output_dir = output_dir or os.path.join(spec_output_dir, "spec_json")
        os.makedirs(output_dir, exist_ok=True)

        docs = self._collect_spec_docs(spec_output_dir)
        if not docs:
            raise FileNotFoundError(f"未找到可用于压缩的 Spec 文档：{spec_output_dir}")

        print(f"SpecJsonAgent：共收集到 {len(docs)} 个 Spec 文档，开始压缩为 JSON...")

        machine_context = self._compress_with_llm(docs, spec_output_dir)
        if machine_context is None:
            print("SpecJsonAgent：模型压缩失败，使用回退 JSON 结构。")
            machine_context = self._build_fallback_json(docs, spec_output_dir)

        output_path = os.path.join(output_dir, "spec_context.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(machine_context, f, ensure_ascii=False, indent=2)

        print(f"SpecJsonAgent：已生成机器友好的 JSON 上下文：{output_path}")
        return output_path

    def _collect_spec_docs(self, spec_output_dir: str) -> List[Dict[str, str]]:
        """
        收集 SpecAgent 产出的关键 markdown 文档。
        """
        candidate_dirs = [
            os.path.join(spec_output_dir, "docs", "rewrite-context"),
            os.path.join(spec_output_dir, ".specify", "memory"),
        ]

        docs: List[Dict[str, str]] = []
        for base_dir in candidate_dirs:
            if not os.path.isdir(base_dir):
                continue

            for root, _, files in os.walk(base_dir):
                for file_name in sorted(files):
                    if not file_name.endswith(".md"):
                        continue

                    file_path = os.path.join(root, file_name)
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                    except Exception as e:
                        print(f"读取 Spec 文档失败 {file_path}: {e}")
                        continue

                    docs.append({
                        "path": os.path.relpath(file_path, spec_output_dir).replace("\\", "/"),
                        "category": self._infer_category(file_path),
                        "content": content,
                    })

        return docs

    def _infer_category(self, file_path: str) -> str:
        normalized = file_path.replace("\\", "/").lower()
        if "01_subsystems" in normalized:
            return "subsystem"
        if "02_interfaces" in normalized:
            return "interface"
        if "03_behaviors" in normalized:
            return "behavior"
        if "constitution" in normalized:
            return "constitution"
        if "manifest" in normalized:
            return "manifest"
        return "general"

    def _compress_with_llm(self, docs: List[Dict[str, str]], spec_output_dir: str) -> Optional[Dict[str, Any]]:
        """
        使用模型把 markdown 文档压缩为一个结构化 JSON。
        如果返回内容不可解析，则返回 None。
        """
        docs_text = []
        for doc in docs:
            # 对单个文档做轻量截断，控制上下文体积，但保留开头结构信息。
            clipped = doc["content"][:6000]
            docs_text.append(
                f"=== 文档路径：{doc['path']} ===\n"
                f"文档类别：{doc['category']}\n"
                f"{clipped}\n"
            )

        project_name = Path(spec_output_dir).name
        prompt = f"""请把下面这些由 C 项目分析阶段产出的 Spec 文档，压缩整理为一个“机器友好的 JSON”。

目标：
1. 这个 JSON 将直接提供给 Rust 代码生成阶段使用
2. 要尽量保留对代码生成真正重要的信息，减少冗长自然语言
3. 输出必须是严格合法的 JSON，不要输出 markdown，不要输出解释
4. 如果某类信息缺失，请使用空字符串、空数组或空对象，不要编造

请严格使用以下 JSON 结构：
{{
  "project_name": "",
  "global_summary": "",
  "global_constraints": [],
  "subsystems": [
    {{
      "name": "",
      "responsibilities": [],
      "key_types": [],
      "key_functions": [],
      "dependencies": [],
      "notes": []
    }}
  ],
  "interfaces": [
    {{
      "name": "",
      "summary": "",
      "inputs": [],
      "outputs": [],
      "constraints": []
    }}
  ],
  "behaviors": [
    {{
      "name": "",
      "summary": "",
      "preconditions": [],
      "postconditions": [],
      "invariants": [],
      "error_cases": []
    }}
  ],
  "rust_generation_hints": {{
    "module_order": [],
    "priority_types": [],
    "priority_errors": [],
    "ownership_notes": [],
    "safety_notes": []
  }},
  "source_docs": [
    {{
      "path": "",
      "category": "",
      "title": "",
      "summary": ""
    }}
  ]
}}

补充要求：
1. 尽量把类型、接口、约束、错误场景抽成短列表
2. 尽量把对 Rust 生成最关键的结构体、类型别名、错误类型、模块边界提炼出来
3. `source_docs` 只保留简短摘要，不要重复全文
4. `global_summary` 控制在 200 字以内

项目名称：
{project_name}

原始 Spec 文档：
{chr(10).join(docs_text)}
"""

        messages = [
            {
                "role": "system",
                "content": "你是一个擅长把程序分析文档压缩成机器可消费 JSON 的代码助手。请严格输出 JSON。"
            },
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.llm.generate(messages)
            content = response[0] if isinstance(response, (list, tuple)) else response
            json_text = self._extract_json_text(content)
            return json.loads(json_text)
        except Exception as e:
            print(f"SpecJsonAgent：模型压缩失败：{e}")
            return None

    def _extract_json_text(self, content: str) -> str:
        """
        从模型返回中提取 JSON 文本。
        """
        content = content.strip()
        if "```json" in content:
            return content.split("```json", 1)[1].split("```", 1)[0].strip()
        if "```" in content:
            return content.split("```", 1)[1].split("```", 1)[0].strip()

        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return match.group(0).strip()
        return content

    def _build_fallback_json(self, docs: List[Dict[str, str]], spec_output_dir: str) -> Dict[str, Any]:
        """
        当模型压缩失败时，使用启发式方式生成一个可用 JSON。
        这个回退版本不追求完美，但要保证结构稳定。
        """
        constitution_doc = next((doc for doc in docs if doc["category"] == "constitution"), None)
        manifest_doc = next((doc for doc in docs if doc["category"] == "manifest"), None)

        fallback = {
            "project_name": Path(spec_output_dir).name,
            "global_summary": self._first_non_empty_heading(manifest_doc["content"]) if manifest_doc else "",
            "global_constraints": self._extract_bullets(constitution_doc["content"]) if constitution_doc else [],
            "subsystems": [],
            "interfaces": [],
            "behaviors": [],
            "rust_generation_hints": {
                "module_order": [],
                "priority_types": [],
                "priority_errors": [],
                "ownership_notes": [],
                "safety_notes": [],
            },
            "source_docs": [],
        }

        for doc in docs:
            title = self._first_non_empty_heading(doc["content"]) or os.path.basename(doc["path"])
            summary = self._summarize_excerpt(doc["content"])

            fallback["source_docs"].append({
                "path": doc["path"],
                "category": doc["category"],
                "title": title,
                "summary": summary,
            })

            if doc["category"] == "subsystem":
                fallback["subsystems"].append({
                    "name": title,
                    "responsibilities": self._extract_bullets(doc["content"]),
                    "key_types": [],
                    "key_functions": [],
                    "dependencies": [],
                    "notes": [summary] if summary else [],
                })
            elif doc["category"] == "interface":
                fallback["interfaces"].append({
                    "name": title,
                    "summary": summary,
                    "inputs": [],
                    "outputs": [],
                    "constraints": self._extract_bullets(doc["content"]),
                })
            elif doc["category"] == "behavior":
                fallback["behaviors"].append({
                    "name": title,
                    "summary": summary,
                    "preconditions": [],
                    "postconditions": [],
                    "invariants": [],
                    "error_cases": [],
                })

        return fallback

    def _first_non_empty_heading(self, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    def _extract_bullets(self, content: str, max_items: int = 8) -> List[str]:
        results: List[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ", "* ", "+ ")):
                results.append(stripped[2:].strip())
            elif re.match(r"^\d+\.\s+", stripped):
                results.append(re.sub(r"^\d+\.\s+", "", stripped).strip())

            if len(results) >= max_items:
                break
        return results

    def _summarize_excerpt(self, content: str, max_len: int = 240) -> str:
        cleaned = " ".join(line.strip() for line in content.splitlines() if line.strip())
        return cleaned[:max_len]
