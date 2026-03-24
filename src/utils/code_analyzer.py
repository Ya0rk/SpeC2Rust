class CodeAnalyzer:
    def __init__(self, llm):
        self.llm = llm
    
    def analyze_module(self, module):
        """分析模块功能和设计意图"""
        # 收集模块中的所有代码
        module_code = self._collect_module_code(module)
        
        # 构建分析提示
        prompt = self._build_analysis_prompt(module_code, module['name'])
        
        # 调用LLM进行分析
        messages = [
            {'role': 'system', 'content': '你是一个C代码分析专家，擅长分析C项目的功能和设计意图。'},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        analysis_result = response[0]
        
        # 解析分析结果
        analysis = {
            'module_name': module['name'],
            'files': module['files'],
            'analysis': analysis_result
        }
        
        return analysis
    
    def _collect_module_code(self, module):
        """收集模块中的所有代码"""
        module_code = []
        
        for file in module['files']:
            file_info = {
                'name': file['name'],
                'path': file['path'],
                'content': file['content']
            }
            module_code.append(file_info)
        
        return module_code
    
    def _build_analysis_prompt(self, module_code, module_name):
        prompt = f"请分析以下C项目模块 '{module_name}' 的功能和设计意图，生成详细的分析报告。\n\n"
        
        for file in module_code:
            prompt += f"=== 文件: {file['path']} ===\n"
            prompt += file['content']
            prompt += "\n\n"
        
        prompt += "请从以下几个方面详细分析这个模块：\n"
        prompt += "1. 模块的主要功能是什么？请详细描述模块的功能和用途。\n"
        prompt += "2. 模块包含哪些关键函数和数据结构？请列出函数签名、参数含义、返回值含义，以及数据结构的字段和用途。\n"
        prompt += "3. 模块的设计意图是什么？请分析模块的设计思路和实现方式。\n"
        prompt += "4. 模块是如何与其他模块交互的？请分析模块间的依赖关系和调用关系。\n"
        # prompt += "5. 模块的代码风格和质量如何？请分析代码的可读性、可维护性和性能。\n"
        # prompt += "6. 模块可能存在的问题或改进空间？请分析潜在的问题和优化方向。\n"
        prompt += "\n"
        prompt += "在分析报告中，请为每个函数和数据结构添加源代码位置信息，格式为：[文件路径:行号]。\n"
        prompt += "例如：函数 foo() 定义在 [src/foo.c:42]。\n"
        prompt += "这样可以为以后的代码生成提供更加清晰的理解。\n"
        
        return prompt
