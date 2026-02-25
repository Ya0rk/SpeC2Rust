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
    def __init__(self, config_path=None):
        # 加载配置
        self.config = Config(config_path)
        
        # 初始化LLM
        self.llm = QwenLocalGen(
            api_key=self.config.api_key,
            model=self.config.model
        )
        
        # 初始化工具
        self.parser = CCodeAnalyzer()
        self.analyzer = CodeAnalyzer(self.llm)
        self.doc_generator = DocumentGenerator(self.llm)
    
    def analyze_project(self, project_path, output_dir):
        """分析C项目并生成文档"""
        # 解析项目结构
        # import tempfile
        # with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as temp_file:
        #     temp_output = temp_file.name
        
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
        
        # 分析各个模块
        module_analyses = []
        for module in modules:
            print(f"分析模块: {module['name']}")
            analysis = self.analyzer.analyze_module(module)
            module_analyses.append(analysis)
        
        # 生成文档
        print("生成项目文档...")
        self.doc_generator.generate(project_path, module_analyses, output_dir)
        
        # 清理临时文件
        # try:
        #     os.unlink(temp_output)
        # except:
        #     pass
        
        print(f"分析完成，文档保存在: {output_dir}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python main.py <项目路径> <输出目录>")
        sys.exit(1)
    
    project_path = sys.argv[1]
    output_dir = sys.argv[2]
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 初始化并运行agent
    agent = CProjectAgent()
    agent.analyze_project(project_path, output_dir)
