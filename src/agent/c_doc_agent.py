import os
import sys
import json
from pathlib import Path
from typing import List, Dict
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent))

from parse.c_ast import CCodeAnalyzer
from utils.code_analyzer import CodeAnalyzer
from utils.document_generator import DocumentGenerator
from config.config import Config
from config.prompt import prompt_manager
from llm.model import Model


class CDocAgent:
    """C 项目 Spec 文档生成 Agent - 使用多轮迭代方式分析 C 项目并生成 spec-kit 格式的 spec 文档"""
    
    def __init__(self, config: Config = None):
        """
        初始化 CDocAgent
        
        Args:
            config: 配置对象
        """
        # 加载配置
        self.llm = Model(config)
        
        # 初始化工具
        self.parser = CCodeAnalyzer()
        self.analyzer = CodeAnalyzer(self.llm)
        self.doc_generator = DocumentGenerator(self.llm)
        
        # 存储历史对话和分析结果
        self.history = []
        self.analysis_plan = None
        self.doc_skeleton = None
        self.current_iteration = 0
        self.max_iterations = 3  # 默认最大迭代次数
        
        # spec-kit 模板路径
        self.spec_kit_path = Path(__file__).parent.parent / "utils" / "spec-kit" / "templates"
    
    def _create_analysis_plan(self, project_path: str, project_analysis: Dict, output_dir: str) -> str:
        """
        制定详细的项目分析计划并生成文档骨架
        
        Args:
            project_path: 项目路径
            project_analysis: 项目分析结果
            output_dir: 输出目录
            
        Returns:
            分析计划字符串
        """
        print("制定项目分析计划并生成文档骨架...")
        
        # 收集项目信息
        project_name = Path(project_path).name
        files = list(project_analysis['file_path_map'].keys())
        functions_count = len(project_analysis['functions'])
        structs_count = len(project_analysis['structs'])
        
        # 尝试读取项目根目录的 README 文件
        readme_content = ""
        readme_path = Path(project_path) / "README.md"
        if readme_path.exists():
            try:
                with open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                    readme_content = f.read()
            except Exception as e:
                print(f"读取 README 文件 {readme_path} 时出错：{e}")
        
        # 构建计划提示
        prompt = prompt_manager.get('c_doc', 'create_analysis_plan',
                                   project_name=project_name,
                                   files=files,
                                   functions_count=functions_count,
                                   structs_count=structs_count,
                                   readme_content=readme_content)
        
        sys_prompt = prompt_manager.get('c_doc', 'create_analysis_plan_system_prompt')
        
        # 调用 LLM 生成分析计划和文档骨架
        messages = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        result = response[0]
        
        # 分离分析计划和文档骨架
        if '<doc_skeleton>' in result:
            parts = result.split('<analysis_plan>')
            analysis_plan = '<analysis_plan>' + parts[1].split('</analysis_plan>')[0].strip() + '</analysis_plan>'
            parts = result.split('<doc_skeleton>')
            doc_skeleton = '<doc_skeleton>' + parts[1].split('</doc_skeleton>')[0].strip() + '</doc_skeleton>'
        else:
            # 如果没有明确的分隔符，报错退出
            exit("没有生成文档骨架")
        
        # 保存分析计划和文档骨架
        self.analysis_plan = analysis_plan
        self.doc_skeleton = doc_skeleton
        self.history.append({
            'round': 0,
            'type': 'plan',
            'content': analysis_plan
        })
        self.history.append({
            'round': 0,
            'type': 'skeleton',
            'content': doc_skeleton
        })
        
        # 保存文档骨架到文件
        skeleton_path = os.path.join(output_dir, "doc_skeleton.md")
        with open(skeleton_path, 'w', encoding='utf-8') as f:
            f.write(doc_skeleton)
        
        print("分析计划已制定:")
        print(analysis_plan)
        print("\n文档骨架已生成:")
        print(doc_skeleton)
        return analysis_plan
    
    def _perform_iteration(self, project_path: str, module_analyses: List[Dict], 
                          output_dir: str, iteration: int) -> str:
        """
        执行一轮迭代分析
        
        Args:
            project_path: 项目路径
            module_analyses: 模块分析结果列表
            output_dir: 输出目录
            iteration: 当前迭代次数
            
        Returns:
            迭代分析结果
        """
        print(f"\n执行第 {iteration} 轮迭代分析...")
        
        # 收集当前分析结果
        current_analysis = ""
        for analysis in module_analyses:
            current_analysis += f"### Module {analysis['module_name']}\n"
            current_analysis += analysis['analysis']
            current_analysis += "\n\n"
        
        # 构建迭代提示
        prompt = prompt_manager.get('c_doc', 'perform_iteration',
                                   project_name=Path(project_path).name,
                                   current_analysis=current_analysis,
                                   analysis_plan=self.analysis_plan,
                                   doc_skeleton=self.doc_skeleton,
                                   iteration=iteration)
        
        # 调用 LLM 进行迭代分析
        messages = [
            {'role': 'system', 'content': prompt_manager.get('c_doc', 'perform_iteration_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        # 添加历史对话信息
        for item in self.history:
            if item['round'] > 0 and item['round'] < iteration:
                messages.append({'role': 'assistant', 'content': item['content']})
        
        response = self.llm.generate(messages)
        iteration_result = response[0]

        # 提取新的文档骨架并保存文档骨架
        if '<doc_skeleton>' in iteration_result:
            parts = iteration_result.split('<doc_skeleton>')
            self.doc_skeleton = parts[1].split('</doc_skeleton>')[0].strip()
            # 重写文档骨架文件
            with open(os.path.join(output_dir, "doc_skeleton.md"), 'w', encoding='utf-8') as f:
                f.write(self.doc_skeleton)

        # 保存迭代结果
        self.history.append({
            'round': iteration,
            'type': 'iteration',
            'content': iteration_result
        })
        
        # 保存迭代结果到文件
        iteration_path = os.path.join(output_dir, f"iteration_{iteration}.md")
        with open(iteration_path, 'w', encoding='utf-8') as f:
            f.write(iteration_result)
        
        print(f"第 {iteration} 轮迭代分析完成:")
        print(iteration_result)
        return iteration_result
    
    def _generate_final_document(self, project_path: str, module_analyses: List[Dict], 
                                output_dir: str) -> str:
        """
        生成最终版文档（保留原有功能）
        
        Args:
            project_path: 项目路径
            module_analyses: 模块分析结果列表
            output_dir: 输出目录
            
        Returns:
            最终文档内容
        """
        print("\n生成最终版文档...")
        
        # 收集所有分析结果和迭代历史
        all_analyses = ""
        for analysis in module_analyses:
            all_analyses += f"### Module {analysis['module_name']}\n"
            all_analyses += analysis['analysis']
            all_analyses += "\n\n"
        
        # 收集迭代历史
        iteration_history = ""
        for item in self.history:
            if item['type'] == 'iteration':
                iteration_history += f"### Iteration Round {item['round']}\n"
                iteration_history += item['content']
                iteration_history += "\n\n"
        
        # 构建最终文档提示
        prompt = prompt_manager.get('c_doc', 'generate_final_document',
                                   project_name=Path(project_path).name,
                                   doc_skeleton=self.doc_skeleton,
                                   all_analyses=all_analyses,
                                   iteration_history=iteration_history,
                                   analysis_plan=self.analysis_plan)
        
        # 调用 LLM 生成最终文档
        messages = [
            {'role': 'system', 'content': prompt_manager.get('c_doc', 'generate_final_document_system_prompt')},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        final_document = response[0]
        
        # 保存最终文档
        final_doc_path = os.path.join(output_dir, "final_project_overview.md")
        with open(final_doc_path, 'w', encoding='utf-8') as f:
            f.write(final_document)
        
        # 保存历史对话
        history_path = os.path.join(output_dir, "analysis_history.json")
        with open(history_path, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
        
        print("最终版文档已生成:")
        print(final_document)
        return final_document
    
    def _generate_spec_documents(self, project_path: str, project_analysis: Dict,
                                module_analyses: List[Dict], output_dir: str) -> None:
        """
        生成 spec-kit 格式的 spec 文档，用于指导 Rust 项目生成
        
        Args:
            project_path: C 项目路径
            project_analysis: 项目分析结果
            module_analyses: 模块分析结果列表
            output_dir: 输出目录
        """
        print("\n生成 spec-kit 格式的 spec 文档...")
        
        project_name = Path(project_path).name
        feature_name = project_name.replace('-', '_').replace(' ', '_')
        branch_name = f"001-{feature_name}-rust-port"
        today = datetime.now().strftime("%Y-%m-%d")
        
        specs_dir = Path(output_dir) / "specs" / branch_name
        specs_dir.mkdir(parents=True, exist_ok=True)
        
        all_analyses = ""
        for analysis in module_analyses:
            all_analyses += f"### Module {analysis['module_name']}\n"
            all_analyses += analysis['analysis']
            all_analyses += "\n\n"
        
        files_info = ""
        for file_path in project_analysis['file_path_map']:
            files_info += f"- {Path(file_path).name}: {file_path}\n"
        
        functions_info = ""
        for func in project_analysis['functions'][:20]:
            functions_info += f"- {func.get('name', 'unknown')}: {func.get('file', 'unknown')}:{func.get('line', '?')}\n"
        
        structs_info = ""
        for struct in project_analysis['structs'][:20]:
            structs_info += f"- {struct.get('name', 'unknown')}: {struct.get('file', 'unknown')}:{struct.get('line', '?')}\n"
        
        spec_prompt = prompt_manager.get('c_doc', 'generate_spec',
                                        project_name=project_name,
                                        branch_name=branch_name,
                                        today=today,
                                        files_info=files_info,
                                        functions_info=functions_info,
                                        structs_info=structs_info,
                                        all_analyses=all_analyses)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('c_doc', 'generate_spec_system_prompt')},
            {'role': 'user', 'content': spec_prompt}
        ]
        
        response = self.llm.generate(messages)
        spec_content = response[0]
        
        spec_path = specs_dir / "spec.md"
        with open(spec_path, 'w', encoding='utf-8') as f:
            f.write(spec_content)
        
        print(f"✓ spec.md 已生成：{spec_path}")
        
        plan_prompt = prompt_manager.get('c_doc', 'generate_plan',
                                        project_name=project_name,
                                        branch_name=branch_name,
                                        all_analyses=all_analyses)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('c_doc', 'generate_plan_system_prompt')},
            {'role': 'user', 'content': plan_prompt}
        ]
        
        response = self.llm.generate(messages)
        plan_content = response[0]
        
        plan_path = specs_dir / "plan.md"
        with open(plan_path, 'w', encoding='utf-8') as f:
            f.write(plan_content)
        
        print(f"✓ plan.md 已生成：{plan_path}")
        
        tasks_prompt = prompt_manager.get('c_doc', 'generate_tasks',
                                         project_name=project_name,
                                         branch_name=branch_name,
                                         all_analyses=all_analyses)
        
        messages = [
            {'role': 'system', 'content': prompt_manager.get('c_doc', 'generate_tasks_system_prompt')},
            {'role': 'user', 'content': tasks_prompt}
        ]
        
        response = self.llm.generate(messages)
        tasks_content = response[0]
        
        tasks_path = specs_dir / "tasks.md"
        with open(tasks_path, 'w', encoding='utf-8') as f:
            f.write(tasks_content)
        
        print(f"✓ tasks.md 已生成：{tasks_path}")
        
        constitution_path = specs_dir / "constitution.md"
        constitution_content = f"""# Constitution: {project_name} Rust Port

## Core Principles

### Article I: Safety First
- All code MUST be memory-safe and thread-safe
- Unsafe code MUST be minimized and well-documented
- Leverage Rust's type system to prevent errors at compile time

### Article II: Idiomatic Rust
- Follow Rust best practices and conventions
- Use modern Rust features (Rust 2021 edition or later)
- Prefer composition over inheritance
- Use enums for state machines and variant data

### Article III: C to Rust Mapping
- C structs → Rust structs or enums (when variants needed)
- C functions → Rust functions with proper error handling (Result/Option)
- C pointers → Rust references, Box, Rc, or Arc as appropriate
- C arrays → Rust Vec or slices
- C strings (char*) → Rust String or &str
- C unions → Rust unions (in unsafe blocks) or enums with proper tagging

### Article IV: Testing Requirements
- Unit tests for all public APIs
- Integration tests for module interactions
- Property-based tests for critical algorithms
- Fuzzing for input processing functions

### Article V: Performance
- Rust version MUST match or exceed C version performance
- Zero-cost abstractions preferred
- Profile-guided optimization for performance-critical code

### Article VI: Documentation
- All public APIs must have rustdoc documentation
- Examples for complex usage patterns
- Migration guide from C API to Rust API

## Quality Gates

- [ ] All tests pass (cargo test)
- [ ] No warnings (cargo clippy -- -D warnings)
- [ ] Documentation complete (cargo doc)
- [ ] Performance benchmarks meet targets
- [ ] Memory safety verified (Miri for critical code)
"""
        
        with open(constitution_path, 'w', encoding='utf-8') as f:
            f.write(constitution_content)
        
        print(f"✓ constitution.md 已生成：{constitution_path}")
        
        readme_path = specs_dir / "README.md"
        readme_content = f"""# {project_name} Rust Port

## Overview

This directory contains the specification and implementation plan for porting the C project **{project_name}** to Rust.

## Documents

- [`spec.md`](./spec.md) - Feature specification describing what the C project does and what the Rust version needs to implement
- [`plan.md`](./plan.md) - Technical implementation plan with architecture decisions and Rust-specific considerations
- [`tasks.md`](./tasks.md) - Detailed task list for implementing the Rust version
- [`constitution.md`](./constitution.md) - Governing principles for the Rust port

## C Project Analysis Summary

- **Files**: {len(project_analysis['file_path_map'])}
- **Functions**: {len(project_analysis['functions'])}
- **Structs**: {len(project_analysis['structs'])}

## Next Steps

1. Review the spec.md to understand the C project functionality
2. Review the plan.md for technical approach
3. Use tasks.md to guide implementation
4. Follow constitution.md for quality standards

## Usage

These documents are designed to be used with AI agents (like Cursor, Claude Code, etc.) to generate the Rust implementation.
"""
        
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)
        
        print(f"✓ README.md 已生成：{readme_path}")
        print(f"\n✓ spec-kit 文档集已生成在：{specs_dir}")
    
    def analyze_project(self, project_path: str, output_dir: str, generate_spec: bool = True) -> None:
        """
        分析 C 项目并生成文档（多轮迭代）
        
        Args:
            project_path: 项目路径
            output_dir: 输出目录
            generate_spec: 是否生成 spec-kit 格式的 spec 文档（默认 True）
        """
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 解析项目结构
        input_dir_name = Path(project_path).name
        print(f"输入目录的最后一层文件夹名：{input_dir_name}")
        
        # 确保输出目录是相对于当前脚本所在位置的
        # 由于代码在 src 目录下运行，需要确保 parse/res 目录存在
        output_dir_path = Path(__file__).parent.parent / "parse" / "res"
        output_dir_path.mkdir(parents=True, exist_ok=True)
        output_file = str(output_dir_path / f"{input_dir_name}.json")
        
        # 使用 CCodeAnalyzer 分析项目
        self.parser.analyze_directory(project_path, output_file)
        
        # 获取完整的项目分析结果
        project_analysis = self.parser.get_project_analysis()
        
        # 构建模块结构
        modules = []
        # 将所有文件视为一个根模块
        # TODO: 这里只有一个根模块，需要划分为更细致的模块
        root_module = {
            'name': 'root',
            'files': []
        }
        
        # 收集所有文件的信息
        for file_path in project_analysis['file_path_map']:
            file_info = {
                'name': os.path.basename(file_path),
                'path': file_path,
                'content': ''
            }
            # 读取文件内容
            full_path = project_analysis['file_path_map'][file_path]
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    file_info['content'] = f.read()
            except Exception as e:
                print(f"读取文件 {full_path} 时出错：{e}")
            
            root_module['files'].append(file_info)
        
        modules.append(root_module)
        
        # 制定分析计划并生成文档骨架
        self._create_analysis_plan(project_path, project_analysis, output_dir)
        
        # 分析各个模块
        module_analyses = []
        for module in modules:
            print(f"分析模块：{module['name']}")
            analysis = self.analyzer.analyze_module(module)
            module_analyses.append(analysis)
        
        # 执行多轮迭代
        for i in range(1, self.max_iterations + 2):
            self._perform_iteration(project_path, module_analyses, output_dir, i)
        
        # 生成最终版文档
        self._generate_final_document(project_path, module_analyses, output_dir)
        
        # 生成初始文档（保留原有功能）
        print("\n生成初始项目文档...")
        self.doc_generator.generate(project_path, module_analyses, output_dir)
        
        # 生成 spec-kit 格式的 spec 文档（新功能）
        if generate_spec:
            self._generate_spec_documents(project_path, project_analysis, module_analyses, output_dir)
        
        print(f"\n分析完成，文档保存在：{output_dir}")
