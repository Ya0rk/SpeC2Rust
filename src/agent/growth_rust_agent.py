import os
import re
from typing import List

from agent.rust_agent import RustAgent


class GrowthRustAgent(RustAgent):
    """
    可选的新型 Rust 代码生成 Agent。

    设计思路来自 tips.md：
    1. 先规划最小可编译集
    2. 选择一条“主树干”文件路径，优先生成主链路
    3. 对树枝依赖允许暂时使用 todo!/unimplemented! 等占位
    4. 边生成边 cargo check，让项目逐步“生长”
    """

    def _generate_growth_plan(self, project_structure: str, files_to_generate: List[str]) -> str:
        print("生成生长式代码规划...")

        prompt = f"""Below are the candidate structure and file list for the current Rust project. Create a plan for "incremental growth-style generation".

Project structure:
{project_structure}

Candidate file list:
{files_to_generate}

Goals:
1. First identify the "minimum compilable set".
2. Then select one main trunk file path from it and generate it first.
3. Allow dependencies outside the trunk to use placeholder implementations first, avoiding too much content in one generation.
4. Gradually expand branch files later.

Strictly use the following tags for output:

<trunk_files>
One file path per line
</trunk_files>

<branch_files>
One file path per line
</branch_files>

<growth_strategy>
Briefly explain in Chinese:
- What the minimum compilable set is.
- Why the main trunk is selected this way.
- Which dependencies may be placeholders first.
</growth_strategy>

Requirements:
1. trunk_files should keep only the truly critical core files for the first round, with as few files as possible.
2. Put the remaining files in branch_files.
3. Prioritize putting Cargo.toml, src/lib.rs, core type files, and core module files into trunk_files.
4. Do not output an explanatory preface; output only the three tags above.
"""

        messages = [
            {
                "role": "system",
                "content": "You are a Rust architecture assistant skilled at splitting large code generation tasks into a minimum compilable set and expanding them incrementally. Strictly output according to the tags."
            },
            {"role": "user", "content": prompt},
        ]

        response = self.llm.generate(messages)
        return response[0]

    def _extract_tag_block(self, text: str, tag_name: str) -> str:
        start_tag = f"<{tag_name}>"
        end_tag = f"</{tag_name}>"
        if start_tag in text and end_tag in text:
            return text.split(start_tag, 1)[1].split(end_tag, 1)[0].strip()
        return ""

    def _fallback_trunk_files(self, files_to_generate: List[str]) -> List[str]:
        prioritized = []
        preferred = [
            "Cargo.toml",
            "src/error.rs",
            "src/types.rs",
            "src/node.rs",
            "src/tree/node.rs",
            "src/tree/mod.rs",
            "src/utils/mod.rs",
            "src/lib.rs",
        ]
        file_set = set(files_to_generate)
        for item in preferred:
            if item in file_set and item not in prioritized:
                prioritized.append(item)

        for item in self._sort_files_for_generation(files_to_generate):
            if item not in prioritized:
                prioritized.append(item)
            if len(prioritized) >= 6:
                break

        if not prioritized:
            prioritized = ["Cargo.toml", "src/lib.rs", "README.md"]
        return self._sanitize_generation_file_list(prioritized)

    def _generate_growth_code(
        self,
        file_path: str,
        context: str,
        growth_plan: str,
        trunk_files: List[str],
        branch_files: List[str],
        stage: str,
    ) -> str:
        if self._is_cargo_toml(file_path):
            return self._generate_code(file_path, context, growth_plan)

        stage_text = "main trunk" if stage == "trunk" else "branch expansion"
        prompt = f"""Generate code for the following Rust file. The current stage is "{stage_text}".

Current file:
{file_path}

Main trunk files:
{trunk_files}

Later branch files:
{branch_files}

Growth-style plan:
{growth_plan}

Current context:
{context}

Generation requirements:
1. Output only the final Rust code, with no explanation.
2. At the current stage, prioritize making the project compile as soon as possible.
3. If the current file needs to depend on branch functions or branch modules that have not yet been generated, you may use minimal placeholder implementations first.
4. Prefer these placeholder implementations:
   - `todo!()`
   - `unimplemented!()`
   - explicit placeholder error returns
5. Do not introduce many unimplemented dependencies in pursuit of completeness.
6. If this is a trunk file, prioritize making type definitions, module boundaries, public interfaces, and the main call path work.
7. If this is a branch file, complete placeholder dependencies already left in the trunk as much as possible.
"""

        messages = [
            {
                "role": "system",
                "content": "You are a code assistant skilled at incremental growth-style generation of Rust projects. Your primary goal is to make the minimum compilable set work first, then gradually expand functionality. Output only code."
            },
            {"role": "user", "content": prompt},
        ]

        response = self.llm.generate(messages)
        code_result = response[0]
        return self._extract_generated_content(code_result, code_lang="rust")

    def _check_after_growth_step(self, file_path: str):
        print(f"生长式检查：写入 {file_path} 后执行 cargo check")
        self.check_project()

    def generate_code(self) -> List[str]:
        print("开始使用 GrowthRustAgent 生成 Rust 代码...")

        project_structure = self._generate_project_structure()
        print("\n项目结构:")
        print(project_structure)

        files_to_generate = self._parse_file_list(project_structure)
        print(f"候选文件列表: {files_to_generate}")

        growth_plan = self._generate_growth_plan(project_structure, files_to_generate)
        print(f"growth_plan: {growth_plan}")

        trunk_block = self._extract_tag_block(growth_plan, "trunk_files")
        branch_block = self._extract_tag_block(growth_plan, "branch_files")

        trunk_files = self._parse_new_files_to_generate(trunk_block, self._fallback_trunk_files(files_to_generate))
        branch_files = self._parse_new_files_to_generate(
            branch_block,
            [item for item in files_to_generate if item not in trunk_files],
        )

        if not trunk_files:
            trunk_files = self._fallback_trunk_files(files_to_generate)

        branch_files = [item for item in branch_files if item not in trunk_files]

        print(f"主树干文件: {trunk_files}")
        print(f"树枝文件: {branch_files}")

        context = f"Project structure:\n{project_structure}\n\nGrowth-style plan:\n{growth_plan}\n"

        for file_path in trunk_files:
            print(f"生成主树干文件：{file_path}")
            code = self._generate_growth_code(file_path, context, growth_plan, trunk_files, branch_files, "trunk")
            if not code or not str(code).strip():
                print(f"主树干文件生成为空，跳过：{file_path}")
                continue

            if self._is_cargo_toml(file_path) and self._looks_like_invalid_cargo_toml(code):
                print("检测到生成的 Cargo.toml 内容异常，回退到最小可用配置")
                code = self._build_fallback_cargo_toml()

            full_path = os.path.join(self.project_path, file_path)
            self._write_file(full_path, code)
            context += f"\n\n=== Generated file: {file_path} ===\n{code}\n"
            self._check_after_growth_step(file_path)

        for index, file_path in enumerate(branch_files, start=1):
            print(f"生成树枝文件：{file_path}")
            code = self._generate_growth_code(file_path, context, growth_plan, trunk_files, branch_files, "branch")
            if not code or not str(code).strip():
                print(f"树枝文件生成为空，跳过：{file_path}")
                continue

            full_path = os.path.join(self.project_path, file_path)
            self._write_file(full_path, code)
            context += f"\n\n=== Generated file: {file_path} ===\n{code}\n"

            if index % 2 == 0:
                self._check_after_growth_step(file_path)

        print(f"GrowthRustAgent 代码生成完成，共生成 {len(self.generated_files)} 个文件")
        return self.generated_files
