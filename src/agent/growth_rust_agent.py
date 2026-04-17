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

        prompt = f"""下面是当前 Rust 项目的候选结构和文件列表，请你为“逐步生长式生成”制定计划。

项目结构：
{project_structure}

候选文件列表：
{files_to_generate}

目标：
1. 先找出“最小可编译集”
2. 再从中选择一条主树干文件路径，优先生成
3. 树干之外的依赖先允许使用占位实现，避免一次生成过多内容
4. 后续再逐步扩展树枝文件

请严格使用下面标签输出：

<trunk_files>
每行一个文件路径
</trunk_files>

<branch_files>
每行一个文件路径
</branch_files>

<growth_strategy>
用简短中文说明：
- 最小可编译集是什么
- 为什么这样选主树干
- 哪些依赖允许先占位
</growth_strategy>

要求：
1. trunk_files 只保留首轮真正关键的核心文件，数量尽量少
2. branch_files 放剩余文件
3. 优先保证 Cargo.toml、src/lib.rs、核心类型文件、核心模块文件进入 trunk_files
4. 不要输出解释性前言，只输出上述三个标签
"""

        messages = [
            {
                "role": "system",
                "content": "你是一个擅长把大型代码生成任务拆成最小可编译集并逐步扩展的 Rust 架构助手。请严格按标签输出。"
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

        stage_text = "主树干" if stage == "trunk" else "树枝扩展"
        prompt = f"""请为下面的 Rust 文件生成代码，当前阶段是“{stage_text}”。

当前文件：
{file_path}

主树干文件：
{trunk_files}

后续树枝文件：
{branch_files}

生长式规划：
{growth_plan}

当前上下文：
{context}

生成要求：
1. 只输出最终 Rust 代码，不要输出解释
2. 当前阶段优先保证项目尽快可编译
3. 如果当前文件需要依赖尚未生成的树枝函数或树枝模块，可以先使用最小占位实现
4. 占位实现优先使用：
   - `todo!()`
   - `unimplemented!()`
   - 明确的占位错误返回
5. 不要为了追求完整而引入大量未实现依赖
6. 如果是 trunk 文件，优先保证类型定义、模块边界、公开接口、主调用路径可工作
7. 如果是 branch 文件，尽量补全 trunk 中已经留下的占位依赖
"""

        messages = [
            {
                "role": "system",
                "content": "你是一个擅长逐步生长式生成 Rust 项目的代码助手。你的首要目标是先保证最小可编译集成立，再逐步扩展功能。请只输出代码。"
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

        context = f"项目结构：\n{project_structure}\n\n生长式规划：\n{growth_plan}\n"

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
            context += f"\n\n=== 已生成文件：{file_path} ===\n{code}\n"
            self._check_after_growth_step(file_path)

        for index, file_path in enumerate(branch_files, start=1):
            print(f"生成树枝文件：{file_path}")
            code = self._generate_growth_code(file_path, context, growth_plan, trunk_files, branch_files, "branch")
            if not code or not str(code).strip():
                print(f"树枝文件生成为空，跳过：{file_path}")
                continue

            full_path = os.path.join(self.project_path, file_path)
            self._write_file(full_path, code)
            context += f"\n\n=== 已生成文件：{file_path} ===\n{code}\n"

            if index % 2 == 0:
                self._check_after_growth_step(file_path)

        print(f"GrowthRustAgent 代码生成完成，共生成 {len(self.generated_files)} 个文件")
        return self.generated_files
