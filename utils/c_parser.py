import os
import re

class CProjectParser:
    def __init__(self):
        pass
    
    def parse(self, project_path):
        """解析C项目结构"""
        project_name = os.path.basename(project_path)
        files = []
        modules = []
        
        # 遍历项目目录，收集所有C/C++文件
        for root, dirs, filenames in os.walk(project_path):
            for filename in filenames:
                if filename.endswith(('.c', '.h', '.cpp', '.hpp')):
                    file_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(file_path, project_path)
                    
                    # 解析文件内容
                    file_info = self._parse_file(file_path)
                    file_info['path'] = relative_path
                    file_info['name'] = filename
                    files.append(file_info)
        
        # 根据文件组织模块
        modules = self._organize_modules(files)
        
        return {
            'name': project_name,
            'path': project_path,
            'files': files,
            'modules': modules
        }
    
    def _parse_file(self, file_path):
        """解析单个C文件"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 提取函数
        functions = self._extract_functions(content)
        
        # 提取结构体
        structs = self._extract_structs(content)
        
        # 提取全局变量
        globals = self._extract_globals(content)
        
        # 提取宏定义
        macros = self._extract_macros(content)
        
        return {
            'functions': functions,
            'structs': structs,
            'globals': globals,
            'macros': macros,
            'content': content
        }
    
    def _extract_functions(self, content):
        """提取函数定义"""
        # 增强的函数提取正则表达式，支持更多C语言特性
        # 支持函数指针、typedef函数、inline函数等
        function_pattern = r'\b(?:static\s+|extern\s+|inline\s+|typedef\s+)?\w+\s+\w+\s*\([^\)]*\)\s*(?:const\s*)?\{[\s\S]*?\}'
        functions = []
        
        for match in re.finditer(function_pattern, content):
            function_code = match.group(0)
            # 提取函数名
            name_match = re.search(r'\b(?:static\s+|extern\s+|inline\s+|typedef\s+)?\w+\s+(\w+)\s*\(', function_code)
            if name_match:
                function_name = name_match.group(1)
                # 提取函数签名
                signature_match = re.search(r'\b(?:static\s+|extern\s+|inline\s+|typedef\s+)?\w+\s+\w+\s*\([^\)]*\)', function_code)
                signature = signature_match.group(0) if signature_match else ''
                functions.append({
                    'name': function_name,
                    'signature': signature,
                    'code': function_code
                })
        
        return functions
    
    def _extract_structs(self, content):
        """提取结构体定义"""
        # 增强的结构体提取正则表达式，支持typedef结构体、结构体嵌套等
        struct_pattern = r'\b(?:typedef\s+)?struct\s+(?:\w+\s+)?\{[\s\S]*?\}(?:\s+\w+)?;'
        structs = []
        
        for match in re.finditer(struct_pattern, content):
            struct_code = match.group(0)
            # 提取结构体名
            name_match = re.search(r'\b(?:typedef\s+)?struct\s+(\w+)?\s*\{', struct_code)
            if name_match and name_match.group(1):
                struct_name = name_match.group(1)
            else:
                # 尝试从typedef中提取名称
                typedef_match = re.search(r'\btypedef\s+struct\s+\{[\s\S]*?\}\s+(\w+);', struct_code)
                struct_name = typedef_match.group(1) if typedef_match else 'anonymous'
            
            structs.append({
                'name': struct_name,
                'code': struct_code
            })
        
        return structs
    
    def _extract_globals(self, content):
        """提取全局变量"""
        # 增强的全局变量提取正则表达式，支持初始化、数组、指针等
        global_pattern = r'\b(?:static\s+|extern\s+|const\s+)?(?:\w+\s+)+\w+(?:\[\d*\])*\s*(?:=\s*[^;]*)?;'
        globals = []
        
        for match in re.finditer(global_pattern, content):
            global_code = match.group(0)
            # 提取变量名
            # 处理数组和指针
            name_match = re.search(r'\b(?:static\s+|extern\s+|const\s+)?(?:\w+\s+)+(\w+)(?:\[\d*\])*\s*(?:=\s*[^;]*)?;', global_code)
            if name_match:
                global_name = name_match.group(1)
                globals.append({
                    'name': global_name,
                    'code': global_code
                })
        
        return globals
    
    def _extract_macros(self, content):
        """提取宏定义"""
        # 增强的宏定义提取正则表达式，支持带参数的宏、多行宏等
        macro_pattern = r'#define\s+\w+(?:\s*\([^\)]*\))?\s+[^\n]*(?:\\\n[^\n]*)*'
        macros = []
        
        for match in re.finditer(macro_pattern, content):
            macro_code = match.group(0)
            # 提取宏名
            name_match = re.search(r'#define\s+(\w+)', macro_code)
            if name_match:
                macro_name = name_match.group(1)
                macros.append({
                    'name': macro_name,
                    'code': macro_code
                })
        
        return macros
    
    def _organize_modules(self, files):
        """根据文件组织模块"""
        modules = []
        module_dict = {}
        
        for file in files:
            # 根据文件路径组织模块
            path_parts = file['path'].split(os.sep)
            if len(path_parts) > 1:
                module_name = path_parts[0]
            else:
                module_name = 'root'
            
            if module_name not in module_dict:
                module_dict[module_name] = {
                    'name': module_name,
                    'files': []
                }
            
            module_dict[module_name]['files'].append(file)
        
        # 转换为列表
        for module_name, module in module_dict.items():
            modules.append(module)
        
        return modules
