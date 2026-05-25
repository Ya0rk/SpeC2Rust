import os

class DocumentGenerator:
    def __init__(self, llm):
        self.llm = llm
    
    def generate(self, project_path, module_analyses, output_dir):
        """生成项目文档"""
        project_name = os.path.basename(project_path)
        
        # 创建项目文档目录
        project_doc_dir = os.path.join(output_dir, project_name)
        os.makedirs(project_doc_dir, exist_ok=True)
        
        # 生成各个模块的文档
        module_docs = []
        for analysis in module_analyses:
            module_doc = self._generate_module_doc(analysis)
            module_docs.append(module_doc)
            
            # 保存模块文档
            module_doc_path = os.path.join(project_doc_dir, f"{analysis['module_name']}_module.md")
            with open(module_doc_path, 'w', encoding='utf-8') as f:
                f.write(module_doc)
        
        # 生成项目总文档
        project_doc = self._generate_project_doc(project_name, module_analyses)
        project_doc_path = os.path.join(project_doc_dir, "project_overview.md")
        with open(project_doc_path, 'w', encoding='utf-8') as f:
            f.write(project_doc)
        
        # 生成README文件
        # readme_content = self._generate_readme(project_name, module_analyses)
        # readme_path = os.path.join(project_doc_dir, "README.md")
        # with open(readme_path, 'w', encoding='utf-8') as f:
        #     f.write(readme_content)
        
        print(f"Project documentation has been generated and saved at: {project_doc_dir}")
    
    def _generate_module_doc(self, analysis):
        """生成模块文档"""
        module_name = analysis['module_name']
        analysis_result = analysis['analysis']
        
        # 构建模块文档
        doc = f"# {module_name} Module Documentation\n\n"
        doc += "## Module Analysis\n\n"
        doc += analysis_result
        doc += "\n"
        
        # 添加文件列表
        doc += "## File List\n\n"
        for file in analysis['files']:
            doc += f"- {file['path']}\n"
        doc += "\n"
        
        return doc
    
    def _generate_project_doc(self, project_name, module_analyses):
        """生成项目总文档"""
        # 收集所有模块的分析结果
        module_analyses_text = ""
        for analysis in module_analyses:
            module_analyses_text += f"### Module {analysis['module_name']}\n"
            module_analyses_text += analysis['analysis']
            module_analyses_text += "\n\n"
        
        # 构建项目总文档提示
        prompt = f"Based on the analysis results of each module in the following C project, generate a complete project overview document, including the project's overall functionality, design intent, and relationships between modules.\n\n"
        prompt += "Project name: " + project_name + "\n\n"
        prompt += "Module analysis results:\n"
        prompt += module_analyses_text
        prompt += "\nPlease generate a detailed project overview document that includes:\n"
        prompt += "1. Project overview\n"
        prompt += "2. Project functionality\n"
        prompt += "3. Project architecture\n"
        prompt += "4. Module relationships\n"
        prompt += "5. Technical characteristics\n"
        # prompt += "6. 使用说明\n"
        prompt += "\n"
        prompt += "Please add source location information for each function and data structure in the document, in the format: [file_path:line_number].\n"
        prompt += "For example: function foo() is defined at [src/foo.c:42].\n"
        prompt += "This will provide clearer understanding for future code generation.\n"
        prompt += "\n"
        prompt += "Do not generate a 'summary and outlook' section, because it is not useful for the next steps.\n"
        
        # 调用LLM生成项目总文档
        messages = [
            {'role': 'system', 'content': 'You are an expert in C project analysis, skilled at generating detailed project documentation.'},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        project_doc = response[0]
        
        return project_doc
    
    # def _generate_readme(self, project_name, module_analyses):
    #     """生成README文件"""
    #     # 构建README提示
    #     prompt = f"请为以下C项目生成一个简洁的README文件，包括项目简介、功能特性、安装使用方法等。\n\n"
    #     prompt += "项目名称: " + project_name + "\n\n"
    #     prompt += "请生成一个标准的README文件，包括以下内容：\n"
    #     prompt += "1. 项目简介\n"
    #     prompt += "2. 功能特性\n"
    #     prompt += "3. 安装与使用\n"
    #     prompt += "4. 示例\n"
    #     prompt += "5. 许可证\n"
        
    #     # 调用LLM生成README
    #     messages = [
    #         {'role': 'system', 'content': '你是一个C项目分析专家，擅长生成简洁明了的README文件。'},
    #         {'role': 'user', 'content': prompt}
    #     ]
        
    #     response = self.llm.get_response(messages)
    #     readme_content = response[0]
        
    #     return readme_content
