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
        self.llm = Model(config)
        
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

        # 4. 生成新的文件列表顺序，根据依赖关系确定的文件生成顺序
        parts = implementation_plan.split('<new_files_to_generate>')
        new_files_to_generate = parts[1].split('</new_files_to_generate>')[0].strip()
        print(f"new_files_to_generate: {new_files_to_generate}")
        # ['Cargo.toml', 'src/avl_data.rs', 'src/avl_bf.rs', 'src/lib.rs', 'src/example.rs', 'src/tests/avl_test.rs', 'README.md']
        # 字符串转化为列表，去掉开头结尾的[]
        new_files_to_generate = new_files_to_generate[1:-1].split(', ')
        # 去掉每个元素多余的''
        new_files_to_generate = [file[1:-1] for file in new_files_to_generate]
        print(f"new_files_to_generate: {new_files_to_generate}")

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
