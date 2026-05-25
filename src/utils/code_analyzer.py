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
            {'role': 'system', 'content': 'You are an expert in C code analysis, skilled at analyzing the functionality and design intent of C projects.'},
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
        prompt = f"Analyze the functionality and design intent of the following C project module '{module_name}' and produce a detailed analysis report.\n\n"
        
        for file in module_code:
            prompt += f"=== File: {file['path']} ===\n"
            prompt += file['content']
            prompt += "\n\n"
        
        prompt += "Please analyze this module in detail from the following aspects:\n"
        prompt += "1. What is the module's main functionality? Describe the module's purpose and use in detail.\n"
        prompt += "2. Which key functions and data structures does the module contain? List function signatures, parameter meanings, return value meanings, and the fields and purpose of each data structure.\n"
        prompt += "3. What is the module's design intent? Analyze the module's design approach and implementation strategy.\n"
        prompt += "4. How does the module interact with other modules? Analyze the dependency and call relationships between modules.\n"
        # prompt += "5. How are the module's code style and quality? Analyze readability, maintainability, and performance.\n"
        # prompt += "6. What problems or improvement opportunities might the module have? Analyze potential issues and optimization directions.\n"
        prompt += "\n"
        prompt += "In the analysis report, please add source location information for each function and data structure in the format: [file_path:line_number].\n"
        prompt += "For example: function foo() is defined at [src/foo.c:42].\n"
        prompt += "This will provide clearer understanding for future code generation.\n"
        
        return prompt
