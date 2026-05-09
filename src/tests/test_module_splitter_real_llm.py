import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

split_spec = importlib.util.spec_from_file_location(
    "module_splitter_under_test",
    Path(__file__).parent.parent / "agent" / "split.py",
)
split_module = importlib.util.module_from_spec(split_spec)
split_spec.loader.exec_module(split_module)
ModuleSplitter = split_module.ModuleSplitter


CONTROL_WORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "case",
}


def _normalize_path(path: str) -> str:
    return str(path).replace("\\", "/")


def _collect_source_files(project_root: Path):
    ignored_dirs = {".git", "build", "dist", "bin", "obj", "bak"}
    c_files = []
    h_files = []
    other_files = []

    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(project_root).parts)
        if rel_parts & ignored_dirs:
            continue
        rel = _normalize_path(path.relative_to(project_root))
        if path.suffix == ".c":
            c_files.append(rel)
        elif path.suffix == ".h":
            h_files.append(rel)
        else:
            other_files.append(rel)

    return sorted(c_files), sorted(h_files), sorted(other_files)


def _extract_function_name(signature: str) -> str:
    before_args = signature.split("(", 1)[0]
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", before_args)
    if not names:
        return ""
    name = names[-1]
    return "" if name in CONTROL_WORDS else name


def _mask_c_comments_and_literals_preserve_layout(text: str) -> str:
    """Blank comments and literals while preserving line/column positions."""
    result = []
    index = 0
    state = "code"
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if state == "code":
            if char == "/" and next_char == "*":
                result.extend([" ", " "])
                index += 2
                state = "block_comment"
                continue
            if char == "/" and next_char == "/":
                result.extend([" ", " "])
                index += 2
                state = "line_comment"
                continue
            if char == '"':
                result.append(" ")
                index += 1
                state = "string"
                continue
            if char == "'":
                result.append(" ")
                index += 1
                state = "char"
                continue
            result.append(char)
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                result.extend([" ", " "])
                index += 2
                state = "code"
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if state == "line_comment":
            result.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "code"
            index += 1
            continue

        if state in {"string", "char"}:
            if char == "\\":
                result.append(" ")
                if next_char:
                    result.append("\n" if next_char == "\n" else " ")
                    index += 2
                else:
                    index += 1
                continue
            delimiter = '"' if state == "string" else "'"
            result.append("\n" if char == "\n" else " ")
            if char == delimiter or char == "\n":
                state = "code"
            index += 1
            continue

    return "".join(result)


def _looks_like_function_signature(lines, start_index: int):
    first = lines[start_index].strip()
    if not first or first.startswith(("#", "//", "/*", "*")):
        return None
    if "(" not in first:
        return None

    signature_lines = []
    brace_line = None
    for index in range(start_index, min(len(lines), start_index + 8)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        signature_lines.append(stripped)
        if ";" in stripped and "{" not in stripped:
            return None
        if "{" in stripped:
            brace_line = index
            break
        if ")" in stripped:
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and lines[next_index].strip().startswith("{"):
                signature_lines.append(lines[next_index].strip())
                brace_line = next_index
                break

    if brace_line is None:
        return None

    signature = " ".join(signature_lines).split("{", 1)[0].strip()
    name = _extract_function_name(signature)
    if not name:
        return None
    return name, signature, brace_line


def _extract_functions_from_file(project_root: Path, rel_path: str):
    path = project_root / rel_path
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    masked_lines = _mask_c_comments_and_literals_preserve_layout(text).splitlines()
    functions = []
    index = 0

    while index < len(masked_lines):
        match = _looks_like_function_signature(masked_lines, index)
        if not match:
            index += 1
            continue

        name, _signature, brace_line = match
        brace_count = 0
        end_index = brace_line
        for body_index in range(brace_line, len(masked_lines)):
            brace_count += masked_lines[body_index].count("{")
            brace_count -= masked_lines[body_index].count("}")
            if brace_count == 0:
                end_index = body_index
                break

        source = "\n".join(lines[index:end_index + 1])
        functions.append(
            {
                "func_defid": f"{rel_path}:{index + 1}:{name}",
                "span": f"{rel_path}:{index + 1}:1:{end_index + 1}:1",
                "num_lines": end_index - index + 1,
                "source": source,
                "calls": [],
                "chunks": [],
                "imports": [],
                "globals": [],
                "sub_chunks": [],
                "pieces": [f"{rel_path}:{index + 1}:1:{end_index + 1}:1"],
            }
        )
        index = end_index + 1

    return functions


def _extract_structs_from_file(project_root: Path, rel_path: str):
    path = project_root / rel_path
    text = path.read_text(encoding="utf-8", errors="ignore")
    structs = []

    for match in re.finditer(r"(typedef\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)?[\s\S]*?\}\s*([A-Za-z_][A-Za-z0-9_]*)?\s*;", text):
        name = match.group(3) or match.group(2) or "anonymous"
        start_line = text[:match.start()].count("\n") + 1
        end_line = text[:match.end()].count("\n") + 1
        structs.append(
            {
                "name": name,
                "filename": rel_path,
                "span": f"{rel_path}:{start_line}:1:{end_line}:1",
                "source": match.group(0),
            }
        )
    return structs


def _build_dependency_graph(functions, structs, c_files, h_files, project_root: Path):
    function_names = {
        item["func_defid"].rsplit(":", 1)[-1]
        for item in functions
    }
    call_graph = {}
    struct_usage = {}
    include_graph = {}
    struct_names = {item["name"] for item in structs if item.get("name")}

    for rel_path in c_files + h_files:
        text = (project_root / rel_path).read_text(encoding="utf-8", errors="ignore")
        includes = []
        for match in re.finditer(r'^\s*#include\s+[<"]([^>"]+)[>"]', text, re.MULTILINE):
            includes.append(match.group(1))
        if includes:
            include_graph[rel_path] = sorted(set(includes))

    for func in functions:
        name = func["func_defid"].rsplit(":", 1)[-1]
        source = func.get("source", "")
        callees = sorted({
            candidate
            for candidate in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source)
            if candidate in function_names and candidate != name
        })
        if callees:
            call_graph[name] = callees
        used_structs = sorted({struct_name for struct_name in struct_names if re.search(rf"\b{re.escape(struct_name)}\b", source)})
        if used_structs:
            struct_usage[name] = used_structs

    return {
        "include_graph": include_graph,
        "call_graph": call_graph,
        "struct_usage": struct_usage,
    }


def build_lightweight_project_analysis(project_root: Path):
    c_files, h_files, other_files = _collect_source_files(project_root)
    functions = []
    structs = []

    for rel_path in c_files:
        functions.extend(_extract_functions_from_file(project_root, rel_path))
    for rel_path in c_files + h_files:
        structs.extend(_extract_structs_from_file(project_root, rel_path))

    dependency_graph = _build_dependency_graph(functions, structs, c_files, h_files, project_root)
    project_info = {
        "project_name": project_root.name,
        "c_files": c_files,
        "h_files": h_files,
        "other_files": other_files,
        "entry_files": [path for path in c_files if "main" in path.lower() or "example" in path.lower() or "test" in path.lower()],
        "build_system": "Makefile" if (project_root / "Makefile").exists() else "unknown",
    }
    project_analysis = {
        "functions": functions,
        "structs": structs,
        "global_vars": [],
        "macros": [],
        "file_path_map": {
            rel: str(project_root / rel)
            for rel in c_files + h_files
        },
    }
    return project_info, project_analysis, dependency_graph


class RealModuleSplitterLlmTests(unittest.TestCase):
    def test_lightweight_parser_ignores_comment_pseudo_functions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sample.c"
            source.write_text(
                """
/* Example text that used to be misread as a function.
   BUF (of size BUFSIZE) is the I/O buffer.
   void
   append_int (int value)
   {
     ignored();
   }
*/

static int
real_function (int value)
{
  char const *text = "{ not a brace }";
  return value + (text != 0);
}
""",
                encoding="utf-8",
            )

            functions = _extract_functions_from_file(root, "sample.c")

        self.assertEqual([item["func_defid"].rsplit(":", 1)[-1] for item in functions], ["real_function"])

    def test_lightweight_parser_cat_dataset_has_unique_real_function_ids(self):
        repo_root = Path(__file__).resolve().parents[2]
        dataset_root = repo_root / "datasets" / "cat"
        if not dataset_root.exists():
            self.skipTest(f"missing dataset: {dataset_root}")

        _project_info, project_analysis, _dependency_graph = build_lightweight_project_analysis(dataset_root)
        functions = project_analysis["functions"]
        func_ids = [item["func_defid"] for item in functions]
        names = {item["func_defid"].rsplit(":", 1)[-1] for item in functions}

        self.assertEqual(len(func_ids), len(set(func_ids)))
        self.assertFalse({"BUF", "MIN", "COMMAND_NAME", "tools", "users"} & names)

    @unittest.skipUnless(
        os.environ.get("TCODE_RUN_REAL_SPLIT_LLM") == "1",
        "set TCODE_RUN_REAL_SPLIT_LLM=1 to call the real LLM from local_config.json",
    )
    def test_real_llm_split_avl_tree_from_local_config(self):
        from config.config import Config
        from llm.model import Model

        repo_root = Path(__file__).resolve().parents[2]
        config_path = repo_root / "local_config.json"
        dataset_root = repo_root / "datasets" / os.environ.get("TCODE_REAL_SPLIT_DATASET", "avl-tree")

        self.assertTrue(config_path.exists(), f"missing config: {config_path}")
        self.assertTrue(dataset_root.exists(), f"missing dataset: {dataset_root}")
        self.assertNotEqual(dataset_root.name, "bak")

        project_info, project_analysis, dependency_graph = build_lightweight_project_analysis(dataset_root)
        self.assertGreater(len(project_analysis["functions"]), 0)

        config = Config(config_path=str(config_path))
        config.round_log_enabled = False
        model = Model(config)
        splitter = ModuleSplitter(
            llm=model,
            llm_context_functions=int(os.environ.get("TCODE_REAL_SPLIT_CONTEXT_FUNCTIONS", "12")),
            llm_context_chars=int(os.environ.get("TCODE_REAL_SPLIT_CONTEXT_CHARS", "6000")),
            llm_max_rounds=int(os.environ.get("TCODE_REAL_SPLIT_MAX_ROUNDS", "6")),
        )

        modules, clusters = splitter.split(project_info, project_analysis, dependency_graph)

        llm_modules = [module for module in modules if module.get("coverage_method") == "llm_dynamic_expansion"]
        self.assertTrue(llm_modules, "LLM did not produce any accepted module; check API output/schema")
        self.assertTrue(clusters)

        report = {
            "dataset": dataset_root.name,
            "function_count": len(project_analysis["functions"]),
            "module_count": len(modules),
            "cluster_count": len(clusters),
            "modules": [
                {
                    "name": module.get("name"),
                    "coverage_method": module.get("coverage_method", "static"),
                    "llm_guided": bool(module.get("llm_guided")),
                    "function_count": len(module.get("functions", [])),
                    "functions": [func.get("name") for func in module.get("functions", [])[:12]],
                    "summary": module.get("summary", ""),
                    "responsibilities": module.get("static_summary", {}).get("responsibilities", []),
                    "files": module.get("files", [])[:8],
                }
                for module in modules
            ],
        }
        print("\nREAL_SPLIT_LLM_REPORT_START")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("REAL_SPLIT_LLM_REPORT_END")


if __name__ == "__main__":
    unittest.main()
