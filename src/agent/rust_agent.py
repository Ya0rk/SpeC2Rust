import os
import sys
import json
from pathlib import Path
from typing import List, Dict, Optional

sys.path.append(str(Path(__file__).parent.parent))

from utils.cmd import run
from config.config import Config
from config.prompt import prompt_manager
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
        
        # 如果项目已存在，先删除
        if os.path.exists(project_path):
            print(f"项目已存在，删除旧项目：{project_path}")
            import shutil
            shutil.rmtree(project_path)
        
        # 使用 cargo new 创建项目
        cmd = f"cd {output_dir} && cargo new {project_name} --lib"
        print(f"执行命令：{cmd}")
        result = run(cmd)
        print(f"项目创建成功：{result}")
        
        self.project_name = project_name
        self.project_path = project_path
        
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
                                self.doc_contents[file_path] = content
                                print(f"加载文件：{file_path} ({len(content)} 字符)")
                            except Exception as e:
                                print(f"加载文件失败 {file_path}: {e}")
            else:
                print(f"路径不存在：{doc_path}")
        
        return self.doc_contents
    
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
        
        # 构建提示
        prompt = prompt_manager.get('rust_agent', 'generate_project_structure_prompt',
                                   project_name=self.project_name,
                                   all_docs=all_docs)

        sys_prompt = prompt_manager.get('rust_agent', 'generate_project_structure_system_prompt')

        messages = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        structure_result = response[0]
        
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

        sys_prompt = prompt_manager.get('rust_agent', 'generate_implementation_plan_system_prompt')

        messages = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        plan_result = response[0]
        
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
        prompt = prompt_manager.get('rust_agent', 'generate_code_prompt',
                                   file_path=file_path,
                                   context=context,
                                   implementation_plan=implementation_plan)

        sys_prompt = prompt_manager.get('rust_agent', 'generate_code_system_prompt')

        messages = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        code_result = response[0]
        
        # 提取代码块
        if '```rust' in code_result:
            parts = code_result.split('```rust')
            code = parts[1].split('```')[0].strip()
        elif '```' in code_result:
            parts = code_result.split('```')
            code = parts[1].strip()
        else:
            code = code_result
        
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
        extra_requirements = self._get_skeleton_extra_requirements(file_path)
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
"""

        messages = [
            {'role': 'system', 'content': '你是一个擅长生成 Rust 工程骨架的代码助手。请只输出代码，不要输出解释。'},
            {'role': 'user', 'content': prompt}
        ]

        response = self.llm.generate(messages)
        skeleton_result = response[0]

        if '```rust' in skeleton_result:
            parts = skeleton_result.split('```rust')
            skeleton = parts[1].split('```')[0].strip()
        elif '```' in skeleton_result:
            parts = skeleton_result.split('```')
            skeleton = parts[1].strip()
        else:
            skeleton = skeleton_result

        return skeleton

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
"""

        messages = [
            {'role': 'system', 'content': '你是一个擅长在既有 Rust 骨架上逐步补全实现的代码助手。请只输出代码，不要输出解释。'},
            {'role': 'user', 'content': prompt}
        ]

        response = self.llm.generate(messages)
        code_result = response[0]

        if '```rust' in code_result:
            parts = code_result.split('```rust')
            code = parts[1].split('```')[0].strip()
        elif '```' in code_result:
            parts = code_result.split('```')
            code = parts[1].strip()
        else:
            code = code_result

        return code

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
    
    def _write_file(self, file_path: str, content: str):
        """
        写入文件内容
        
        Args:
            file_path: 文件路径
            content: 文件内容
        """
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
        
        # 读取现有内容
        with open(cargo_toml_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 添加依赖
        if '[dependencies]' not in content:
            content += "\n[dependencies]\n"
        
        for pkg, version in dependencies.items():
            if pkg not in content:
                content += f"{pkg} = \"{version}\"\n"
        
        # 写回文件
        with open(cargo_toml_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"更新 Cargo.toml 依赖")
    
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
                
                # 尝试更鲁棒的解析方式
                # 如果模型输出的是 ['a', 'b'] 这种格式
                import re
                # 使用正则提取引号内的文件名
                found_files = re.findall(r"['\"](.*?)['\"]", tag_content)
                if found_files:
                    new_files_to_generate = found_files
                else:
                    # 如果正则没抓到，尝试原来的暴力分割法
                    new_files_to_generate = tag_content.strip('[]').replace("'", "").replace('"', "").split(', ')
                
                print(f"成功从计划中提取新顺序: {new_files_to_generate}")
            except Exception as e:
                print(f"解析新文件列表失败，使用原始顺序。错误: {e}")
                new_files_to_generate = files_to_generate
        else:
            print("模型未提供 <new_files_to_generate> 标签，使用原始文件顺序。")
            new_files_to_generate = files_to_generate

        # 对生成顺序做轻量调整：优先结构体、类型和错误定义
        new_files_to_generate = self._sort_files_for_generation(new_files_to_generate)
        print(f"最终生成顺序: {new_files_to_generate}")
        # pause = input("按任意键继续...")
        
        # 4. 逐个生成文件
        all_generated_code = {}
        context = f"项目结构：\n{project_structure}\n\n实现计划：\n{implementation_plan}\n"
        
        for file_path in new_files_to_generate:
            # file_type = file_info['type']
            # description = file_info.get('description', '')
            
            print(f"生成文件：{file_path}")
            
            # 生成代码
            file_context = context
            if getattr(self.config, "skeleton_first", False):
                print(f"先生成骨架：{file_path}")
                skeleton_code = self._generate_skeleton(file_path, file_context, implementation_plan)
                print(f"再基于骨架补全实现：{file_path}")
                code = self._implement_from_skeleton(file_path, skeleton_code, file_context, implementation_plan)
            else:
                code = self._generate_code(file_path, file_context, implementation_plan)
            
            # 检测依赖，更新 Cargo.toml
            deps = self._detect_dependencies(code)
            if deps:
                print(f"检测到依赖：{deps}")
                self._update_cargo_toml(deps)
            
            # 保存生成的代码
            all_generated_code[file_path] = code
            
            # 写入文件
            full_path = os.path.join(self.project_path, file_path)
            self._write_file(full_path, code)
            
            # 更新上下文
            context += f"\n\n=== 已生成文件：{file_path} ===\n{code}\n"
        
        print(f"代码生成完成，共生成 {len(self.generated_files)} 个文件")
        return self.generated_files
    
    def _parse_file_list(self, project_structure: str) -> List[Dict]:
        """
        从项目结构描述中解析文件列表
        
        Args:
            project_structure: 项目结构描述
            
        Returns:
            文件信息列表
        """
        project_structure.strip().split('\n')
        current_path = []
        paths = []
        
        parts = project_structure.split('<project_file>')   
        tree_structure = parts[1].split('</project_file>')[0].strip()

        tree_structure_refactored = tree_structure.replace('├──', ' ').replace('└──', ' ').replace('│', ' ')
        
        print(tree_structure_refactored)
        # pause = input("按任意键继续...")

        lines_list = tree_structure_refactored.splitlines()
 
        first_line = lines_list[0].strip()
        root_name = first_line.rstrip('/')
        current_path = [root_name]
        # print(f"current_path: {current_path}")
        
        for line in lines_list[1:]:
            if not line.strip():
                continue
            
            indent_level = len(line) - len(line.lstrip())
            original_name = line.strip()
            is_directory = original_name.endswith('/')
            name = original_name.rstrip('/')
            
            current_path = current_path[:indent_level // 4]
            current_path.append(name)
            # print(f"current_path: {current_path}")
            
            if not is_directory:
                full_path = '/'.join(current_path)
                paths.append(full_path)

        # print(paths)
        # pause = input("按任意键继续...")

        return paths
    
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
    
    def generate_from_docs(self, project_name: str, output_dir: str, doc_paths: List[str]) -> bool:
        """
        根据文档生成完整的 Rust 项目（主入口方法）
        
        Args:
            project_name: 项目名称
            output_dir: 输出目录
            doc_paths: 文档路径列表
            
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
