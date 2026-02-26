import os
import sys
import json
from pathlib import Path

# 添加父目录到Python路径
sys.path.append(str(Path(__file__).parent.parent))

from parse.c_ast import CCodeAnalyzer
from utils.code_analyzer import CodeAnalyzer
from utils.document_generator import DocumentGenerator
from llm.qianwen.qianwen_gen import QwenLocalGen
from config.config import Config

class CProjectAgent:
    def __init__(self, model_name, config_path=None):
        # 加载配置
        self.config = Config(config_path, model_name)
        
        # 初始化LLM
        self.llm = QwenLocalGen(
            api_key=self.config.api_key,
            model=self.config.model
        )
        
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
    
    def _create_analysis_plan(self, project_path, project_analysis, output_dir):
        """制定详细的项目分析计划并生成文档骨架"""
        print("制定项目分析计划并生成文档骨架...")
        
        # 收集项目信息
        project_name = Path(project_path).name
        files = list(project_analysis['file_path_map'].keys())
        functions_count = len(project_analysis['functions'])
        structs_count = len(project_analysis['structs'])
        
        # 构建计划提示
        prompt = f"请为以下C项目制定一个详细的分析计划并生成项目文档骨架，目标是完全理解这个项目。\n\n"
        prompt += f"项目名称: {project_name}\n"
        prompt += f"项目文件数量: {len(files)}\n"
        prompt += f"函数数量: {functions_count}\n"
        prompt += f"结构体数量: {structs_count}\n"
        prompt += f"文件列表: {', '.join(files)}\n\n"
        prompt += "请制定一个详细的分析计划，包括：\n"
        prompt += "1. 整体分析策略\n"
        prompt += "2. 分阶段分析步骤\n"
        prompt += "3. 重点关注的模块和功能\n"
        prompt += "4. 如何验证分析的完整性\n"
        prompt += "5. 每轮迭代的具体任务\n\n"
        prompt += "同时，请生成项目文档骨架，包括以下内容的详细标题结构：\n"
        prompt += "1. 项目概述\n"
        prompt += "2. 项目功能\n"
        prompt += "3. 项目架构\n"
        prompt += "4. 模块关系\n"
        prompt += "5. 代码结构和关键组件\n"
        prompt += "6. 关键函数分析\n"
        prompt += "7. 数据结构分析\n"
        prompt += "8. 核心算法分析\n"
        prompt += "\n请为每个部分提供详细的子标题结构，后续轮次将基于此骨架进行内容完善。"
        
        sys_prompt = "你是一个C项目分析专家，擅长制定详细的项目分析计划和文档结构。"
        sys_prompt += "**注意事项：**"
        sys_prompt += "1. 分析计划必须详细，包括每个模块的分析任务和验证方法\n"
        sys_prompt += "2. 每轮迭代的任务必须基于上一轮的分析结果，不能独立进行\n"
        sys_prompt += "6. 完全专注于正在研究的特定主题，不要偏离到相关主题\n"
        
        # 调用LLM生成分析计划和文档骨架
        messages = [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.get_response(messages)
        result = response[0]
        
        # 分离分析计划和文档骨架
        # 假设分析计划和文档骨架之间有明确的分隔符
        if '文档骨架' in result:
            parts = result.split('文档骨架')
            analysis_plan = parts[0].strip()
            doc_skeleton = '文档骨架' + parts[1].strip()
        else:
            # 如果没有明确的分隔符，将整个结果作为分析计划
            analysis_plan = result
            doc_skeleton = "# 项目文档骨架\n\n## 1. 项目概述\n### 1.1 项目目的\n\n## 2. 项目功能\n### 2.1 核心功能\n### 2.2 辅助功能\n\n## 3. 项目架构\n### 3.1 整体架构\n### 3.2 模块划分\n\n## 4. 模块关系\n### 4.1 依赖关系\n### 4.2 调用关系\n\n## 5. 代码结构和关键组件\n### 5.1 目录结构\n### 5.2 关键文件\n\n## 6. 关键函数分析\n### 6.1 核心函数\n### 6.2 辅助函数\n\n## 7. 数据结构分析\n### 7.1 主要数据结构\n### 7.2 数据流程\n\n"
        
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
    
    def _perform_iteration(self, project_path, module_analyses, output_dir, iteration):
        """执行一轮迭代分析"""
        print(f"\n执行第 {iteration} 轮迭代分析...")
        
        # 收集当前分析结果
        current_analysis = ""
        for analysis in module_analyses:
            current_analysis += f"### {analysis['module_name']} 模块\n"
            current_analysis += analysis['analysis']
            current_analysis += "\n\n"
        
        # 构建迭代提示
        prompt = f"请基于以下C项目的当前分析结果和文档骨架，进行第 {iteration} 轮迭代分析和文档完善。\n\n"
        prompt += f"项目名称: {Path(project_path).name}\n\n"
        prompt += "当前分析结果:\n"
        prompt += current_analysis
        prompt += "\n"
        prompt += f"分析计划:\n{self.analysis_plan}\n\n"
        prompt += f"文档骨架:\n{self.doc_skeleton}\n\n"
        prompt += "请在本轮迭代中：\n"
        prompt += "1. 基于上一轮的分析结果进行深入分析\n"
        prompt += "2. 检查文档骨架的准确性，如有需要可以修复骨架中不准确的地方\n"
        prompt += "3. 为文档骨架中的各个部分填充详细内容\n"
        prompt += "4. 补充缺失的信息\n"
        prompt += "5. 修正错误的理解\n"
        prompt += "6. 完善文档的细节\n"
        prompt += "7. 不要将过长的代码写到分析报告中，而是使用代码定位方式，例如：'a.c [开始行:结束行]'\n"
        prompt += "8. 不需要生成项目的使用安装说明和代码风格质量说明，只需要生成项目的分析报告\n"
        prompt += "9. 不需要测试项目的性能，只需要分析项目的代码结构和功能\n"
        prompt += "10. 完全专注于正在研究的特定主题，不要偏离到相关主题\n"
        
        # 调用LLM进行迭代分析
        messages = [
            {'role': 'system', 'content': '你是一个C项目分析专家，擅长基于现有分析和文档骨架进行迭代完善。'},
            {'role': 'user', 'content': prompt}
        ]
        
        # 添加历史对话信息
        for item in self.history:
            if item['round'] > 0 and item['round'] < iteration:
                messages.append({'role': 'assistant', 'content': item['content']})
        
        response = self.llm.get_response(messages)
        iteration_result = response[0]
        
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
    
    def _generate_final_document(self, project_path, module_analyses, output_dir):
        """生成最终版文档"""
        print("\n生成最终版文档...")
        
        # 收集所有分析结果和迭代历史
        all_analyses = ""
        for analysis in module_analyses:
            all_analyses += f"### {analysis['module_name']} 模块\n"
            all_analyses += analysis['analysis']
            all_analyses += "\n\n"
        
        # 收集迭代历史
        iteration_history = ""
        for item in self.history:
            if item['type'] == 'iteration':
                iteration_history += f"### 第 {item['round']} 轮迭代\n"
                iteration_history += item['content']
                iteration_history += "\n\n"
        
        # 构建最终文档提示
        prompt = f"请基于以下C项目的文档骨架、所有分析结果和迭代历史，生成一个准确详细的最终版文档。\n\n"
        prompt += f"项目名称: {Path(project_path).name}\n\n"
        prompt += "分析计划:\n{self.analysis_plan}\n\n"
        prompt += f"文档骨架:\n{self.doc_skeleton}\n\n"
        prompt += "所有分析结果:\n"
        prompt += all_analyses
        prompt += "\n"
        prompt += "迭代历史:\n"
        prompt += iteration_history
        prompt += "\n"
        prompt += "请严格按照文档骨架的结构生成最终文档，同时整合所有分析结果和迭代历史中的信息。\n"
        prompt += "文档应该详细、准确，包含源代码位置信息，并严格按照分析计划执行的结果生成。\n"
        
        # 调用LLM生成最终文档
        messages = [
            {'role': 'system', 'content': '你是一个C项目分析专家，擅长基于文档骨架和分析结果生成详细准确的项目文档。'},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.get_response(messages)
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
    
    def analyze_project(self, project_path, output_dir):
        """分析C项目并生成文档（多轮迭代）"""
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 解析项目结构
        input_dir_name = Path(project_path).name
        print(f"输入目录的最后一层文件夹名: {input_dir_name}")
        output_file = "parse/res/{}.json".format(input_dir_name)
        
        # 使用CCodeAnalyzer分析项目
        self.parser.analyze_directory(project_path, output_file)
        
        # 获取完整的项目分析结果
        project_analysis = self.parser.get_project_analysis()
        
        # 构建模块结构
        modules = []
        # 将所有文件视为一个根模块
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
                print(f"读取文件 {full_path} 时出错: {e}")
            
            root_module['files'].append(file_info)
        
        modules.append(root_module)
        
        # 制定分析计划并生成文档骨架
        self._create_analysis_plan(project_path, project_analysis, output_dir)
        
        # 分析各个模块
        module_analyses = []
        for module in modules:
            print(f"分析模块: {module['name']}")
            analysis = self.analyzer.analyze_module(module)
            module_analyses.append(analysis)
        
        # 执行多轮迭代
        for i in range(1, self.max_iterations + 1):
            self._perform_iteration(project_path, module_analyses, output_dir, i)
        
        # 生成最终版文档
        self._generate_final_document(project_path, module_analyses, output_dir)
        
        # 生成初始文档（保留原有功能）
        print("\n生成初始项目文档...")
        self.doc_generator.generate(project_path, module_analyses, output_dir)
        
        print(f"\n分析完成，文档保存在: {output_dir}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="C项目分析代理")
    parser.add_argument("project_path", help="项目路径")
    parser.add_argument("output_dir", help="输出目录")
    parser.add_argument("--model_size", default="7", help="模型size")
    
    args = parser.parse_args()
    project_path = args.project_path
    output_dir = args.output_dir
    model_name = f"Qwen2.5-Coder-{args.model_size}B-Instruct"
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 初始化并运行agent
    agent = CProjectAgent(model_name)
    agent.analyze_project(project_path, output_dir)
