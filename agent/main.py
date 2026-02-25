import os
import sys
import json

# 添加父目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.c_parser import CProjectParser
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
        self.parser = CProjectParser()
        self.analyzer = CodeAnalyzer(self.llm)
        self.doc_generator = DocumentGenerator(self.llm)
    
    def analyze_project(self, project_path, output_dir):
        """分析C项目并生成文档"""
        # 解析项目结构
        project_structure = self.parser.parse(project_path)
        
        # 分析各个模块
        module_analyses = []
        for module in project_structure['modules']:
            print(f"分析模块: {module['name']}")
            analysis = self.analyzer.analyze_module(module)
            module_analyses.append(analysis)
        
        # 生成文档
        print("生成项目文档...")
        self.doc_generator.generate(project_path, module_analyses, output_dir)
        
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
