import json
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List
import sys
# 添加项目根目录到导入路径
sys.path.append(str(Path(__file__).parent.parent))
from tree_sitter import Language, Parser
import networkx as nx
from utils.fmtpr import prRed
from utils.cmd import run

Language.build_library(
    "build/c-language.so",
    ['vendor/tree-sitter-c']
)

class CCodeAnalyzer:
    """C代码分析器类，使用tree-sitter解析C代码并提取函数信息"""
    
    def __init__(self):
        """初始化分析器"""
        print("初始化C代码分析器...")
                     
        self.parser = Parser()
        c_language = Language("build/c-language.so", "c")
        self.parser.set_language(c_language)

        
        # 存储分析结果
        self.functions = []
        self.global_vars = []
        self.structs = []
        self.macros = []
        self.file_path_map = {}  # 文件路径映射
        
    def analyze_directory(self, c_code_dir: str, output_file: str) -> None:
        """
        分析整个C代码目录
        
        参数:
            c_code_dir: C代码目录路径
            output_file: 输出JSON文件路径
        """
        c_path = Path(c_code_dir)
        if not c_path.exists():
            raise FileNotFoundError(f"目录不存在: {c_code_dir}")
        
        # 查找所有C文件和头文件
        c_files = list(c_path.glob("**/*.c")) + list(c_path.glob("**/*.h"))
        
        print(f"找到 {len(c_files)} 个C文件，开始分析...")
        # 打印所有找到的C文件，检查是否包含xmalloc.c
        print("找到的C文件列表：")
        for c_file in c_files:
            print(f"  {c_file}")
        
        # 分析每个文件
        for c_file in c_files:
            print(f"正在分析: {c_file.relative_to(c_path)}")
            self._analyze_single_file(c_file, c_path)
        
        # 构建函数调用关系
        print("构建函数调用关系...")
        self._build_call_relationships()
        
        # 保存结果到JSON文件
        print("保存分析结果...")
        self._save_results(output_file)
        
        print(f"分析完成，结果已保存到: {output_file}")
        print(f"共分析 {len(self.functions)} 个函数")
    
    def _analyze_single_file(self, file_path: Path, root_dir: Path) -> None:
        """
        分析单个C文件
        
        参数:
            file_path: C文件路径
            root_dir: 代码根目录
        """
        try:
            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                source_code = f.read()
            
            # 创建tree-sitter树
            tree = self.parser.parse(bytes(source_code, 'utf-8'))
            
            rel_path = str(file_path.relative_to(root_dir))
            self.file_path_map[rel_path] = str(file_path)
            
            # 遍历AST查找函数定义
            root_node = tree.root_node
            self._extract_functions(root_node, source_code, rel_path, root_dir)
            
            # 查找全局变量
            self._extract_global_variables(root_node, source_code, rel_path, root_dir)
            
            # 查找结构体定义
            self._extract_structs(root_node, source_code, rel_path, root_dir)
            
            # 查找宏定义
            self._extract_macros(source_code, rel_path, root_dir)
            
            # 专门处理内联函数（对于tree-sitter无法正确解析的情况）
            if str(file_path).endswith('.h'):
                self._extract_inline_functions(source_code, rel_path, root_dir)
            
        except Exception as e:
            print(f"分析文件 {file_path} 时出错: {e}")
            import traceback
            traceback.print_exc()
    
    def _extract_functions(self, node, source_code: str, file_path: str, root_dir: Path, depth: int = 0) -> None:
        """
        递归提取函数定义，包括普通函数定义和包含函数体的声明（如内联函数）
        
        参数:
            node: 当前AST节点
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
            depth: 递归深度，用于避免无限递归
        """
        # 避免无限递归
        # if depth > 1000:
        #     print(f"警告: 递归深度达到 {depth}，停止递归")
        #     return
            
        # 调试ialloc.h文件的declaration节点
        if file_path.endswith('ialloc.h') and node.type == 'declaration':
            print(f"ialloc.h declaration节点内容: {source_code[node.start_byte:node.end_byte].strip()[:150]}...")
            print(f"子节点类型: {[child.type for child in node.children]}")
            # 查找是否有compound_statement
            has_body = any(child.type == 'compound_statement' for child in node.children)
            print(f"是否包含函数体: {has_body}")
            
        # 检查是否是函数定义节点或包含函数体的声明（用于内联函数）
        if node.type == 'function_definition':
            func_info = self._create_function_info(node, source_code, file_path, root_dir)
            if func_info:
                self.functions.append(func_info)
        elif node.type == 'declaration':
            # 检查声明是否包含函数体（用于内联函数）
            # 递归检查所有后代节点，而不仅仅是直接子节点
            has_body = False
            def check_for_compound_statement(n):
                nonlocal has_body
                if n.type == 'compound_statement':
                    has_body = True
                    return True
                for child in n.children:
                    if check_for_compound_statement(child):
                        return True
                return False
            
            check_for_compound_statement(node)
            
            if has_body:
                func_info = self._create_function_info(node, source_code, file_path, root_dir)
                if func_info:
                    self.functions.append(func_info)
        
        # 递归处理子节点
        for child in node.children:
            self._extract_functions(child, source_code, file_path, root_dir, depth + 1)
    
    def _create_function_info(self, node, source_code: str, file_path: str, root_dir: Path) -> Optional[Dict[str, Any]]:
        """
        创建函数信息字典，与标准slices.json格式一致
        
        参数:
            node: 函数定义AST节点（function_definition或declaration类型）
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
            
        返回:
            函数信息字典
        """
        try:
            # 获取函数名
            declarator = None
            body = None
            
            if node.type == 'function_definition':
                # 标准函数定义节点
                declarator = node.child_by_field_name('declarator')
                body = node.child_by_field_name('body')
            elif node.type == 'declaration':
                # 内联函数的declaration节点
                # 遍历子节点找到declarator和body
                for child in node.children:
                    if child.type in ['function_declarator', 'pointer_declarator']:
                        declarator = child
                    elif child.type == 'compound_statement':
                        body = child
            
            if not declarator or not body:
                return None
                
            function_name = self._extract_function_name(declarator)
            if not function_name:
                return None
            
            # 计算行号和列号 (tree-sitter使用0-index)
            start_line = node.start_point[0] + 1
            start_col = node.start_point[1] + 1
            end_line = node.end_point[0] + 1
            end_col = node.end_point[1] + 1
            
            # 提取函数源代码
            func_source = source_code[node.start_byte:node.end_byte]
            
            # 创建函数信息字典（与标准格式一致）
            func_id = f"{file_path}:{function_name}"
            span_info = f"{file_path}:{start_line}:{start_col}:{end_line}:{end_col}"
            
            func_info = {
                "func_defid": func_id,
                "func_name": function_name,  # 保留用于内部处理，最终输出前会移除
                "span": span_info,
                "pieces": [span_info],
                "sub_chunks": [],
                "num_lines": end_line - start_line + 1,
                "source": func_source,
                "calls": [],  # 存储调用该函数的调用者信息
                "callees": [],  # 临时存储该函数调用了哪些函数
                "globals": [],  # 将在后续步骤中填充
                "imports": [],  # C语言通常没有imports
                "chunks": []  # 主函数对应的代码块
            }
            
            # 提取函数调用（存储到callees中）
            self._extract_function_calls(body, source_code, func_info, file_path, root_dir)
            
            return func_info
            
        except Exception as e:
            print(f"提取函数信息时出错: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _extract_function_name(self, declarator) -> Optional[str]:
        """
        从声明器中提取函数名
        
        参数:
            declarator: 函数声明器节点
            
        返回:
            函数名字符串或None
        """
        # 函数名通常是function_declarator类型
        if declarator.type == 'function_declarator':
            # 函数名是function_declarator的第一个子节点
            if len(declarator.children) > 0:
                name_node = declarator.children[0]
                if name_node.type == 'identifier':
                    return name_node.text.decode('utf-8')
        elif declarator.type == 'identifier':
            return declarator.text.decode('utf-8')
        elif declarator.type == 'pointer_declarator':
            # 处理指针函数，如 int *func(int x)
            # pointer_declarator的结构: [*符号, function_declarator]
            # 需要递归处理最后一个子节点（实际的函数声明器）
            if len(declarator.children) > 0:
                return self._extract_function_name(declarator.children[-1])
        
        return None
    
    def _extract_function_calls(self, node, source_code: str, func_info: Dict[str, Any], 
                               file_path: str, root_dir: Path) -> None:
        """
        提取函数调用信息
        
        参数:
            node: AST节点
            source_code: 源代码
            func_info: 函数信息字典
            file_path: 文件相对路径
            root_dir: 代码根目录
        """
        # 检查是否是函数调用节点
        if node.type == 'call_expression':
            # 提取被调用函数名
            function_node = node.child_by_field_name('function')
            if function_node and function_node.type == 'identifier':
                callee_name = function_node.text.decode('utf-8')
                
                # 获取调用位置
                call_start_line = node.start_point[0] + 1
                call_start_col = node.start_point[1] + 1
                call_end_line = node.end_point[0] + 1
                call_end_col = node.end_point[1] + 1
                
                # 提取完整的代码行而不仅仅是函数调用表达式
                # 找到当前行的开始和结束位置
                lines = source_code.split('\n')
                # 获取调用所在的行
                line_content = lines[node.start_point[0]]
                # 去掉前后空格
                call_source = line_content.strip()
                
                # 创建调用信息（存储到callees中）
                call_info = {
                    "callee": callee_name,
                    "caller": func_info["func_defid"],
                    "span": f"{file_path}:{call_start_line}:{call_start_col}:{call_end_line}:{call_end_col}",
                    "source": call_source
                }
                
                func_info["callees"].append(call_info)
        
        # 递归处理子节点
        for child in node.children:
            self._extract_function_calls(child, source_code, func_info, file_path, root_dir)
    
    def _extract_global_variables(self, node, source_code: str, file_path: str, root_dir: Path, depth: int = 0) -> None:
        """
        提取全局变量信息
        
        参数:
            node: AST节点
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
            depth: 递归深度，用于避免无限递归
        """
        # 避免无限递归
        # if depth > 1000:
        #     print(f"警告: 递归深度达到 {depth}，停止递归")
        #     return
            
        # 检查是否是声明节点，并且在顶层(函数外部)
        if node.type == 'declaration':
            # 检查是否在函数外部
            parent = node.parent
            is_global = True
            while parent:
                if parent.type == 'function_definition':
                    is_global = False
                    break
                parent = parent.parent
            
            if is_global:
                # 提取变量信息
                self._extract_variable_info(node, source_code, file_path, root_dir)
        
        # 递归处理子节点
        for child in node.children:
            self._extract_global_variables(child, source_code, file_path, root_dir, depth + 1)
    
    def _extract_variable_info(self, node, source_code: str, file_path: str, root_dir: Path) -> None:
        """
        提取变量信息
        
        参数:
            node: 声明节点
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
        """
        # 遍历声明中的所有声明器
        for child in node.children:
            if child.type == 'declarator':
                # 提取变量名
                var_name = None
                if len(child.children) > 0 and child.children[0].type == 'identifier':
                    var_name = child.children[0].text.decode('utf-8')
                
                if var_name:
                    # 获取变量位置
                    var_start_line = child.start_point[0] + 1
                    var_start_col = child.start_point[1] + 1
                    var_end_line = child.end_point[0] + 1
                    var_end_col = child.end_point[1] + 1
                    
                    # 提取变量声明源代码
                    var_source = source_code[child.start_byte:child.end_byte]
                    
                    # 创建变量信息
                    var_info = {
                        "var_name": var_name,
                        "filename": file_path,
                        "startLine": var_start_line,
                        "startCol": var_start_col,
                        "endLine": var_end_line,
                        "endCol": var_end_col,
                        "span": f"{file_path}:{var_start_line}:{var_start_col}:{var_end_line}:{var_end_col}",
                        "source": var_source
                    }
                    
                    self.global_vars.append(var_info)

    def _extract_structs(self, node, source_code: str, file_path: str, root_dir: Path, depth: int = 0) -> None:
        """
        提取结构体定义
        
        参数:
            node: AST节点
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
            depth: 递归深度
        """
        # 检查是否是结构体定义节点
        if node.type == 'struct_specifier':
            # 提取结构体名
            struct_name = None
            for child in node.children:
                if child.type == 'identifier':
                    struct_name = child.text.decode('utf-8')
                    break
            
            if not struct_name:
                struct_name = 'anonymous'
            
            # 计算行号和列号
            start_line = node.start_point[0] + 1
            start_col = node.start_point[1] + 1
            end_line = node.end_point[0] + 1
            end_col = node.end_point[1] + 1
            
            # 提取结构体源代码
            struct_source = source_code[node.start_byte:node.end_byte]
            
            # 创建结构体信息
            struct_info = {
                "name": struct_name,
                "filename": file_path,
                "startLine": start_line,
                "startCol": start_col,
                "endLine": end_line,
                "endCol": end_col,
                "span": f"{file_path}:{start_line}:{start_col}:{end_line}:{end_col}",
                "source": struct_source
            }
            
            self.structs.append(struct_info)
        
        # 递归处理子节点
        for child in node.children:
            self._extract_structs(child, source_code, file_path, root_dir, depth + 1)
    
    def _extract_macros(self, source_code: str, file_path: str, root_dir: Path) -> None:
        """
        提取宏定义
        
        参数:
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
        """
        import re
        
        # 匹配宏定义，支持带参数的宏和多行宏
        macro_pattern = r'#define\s+\w+(?:\s*\([^\)]*\))?\s+[^\n]*(?:\\\n[^\n]*)*'
        
        lines = source_code.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('#define'):
                # 提取宏定义
                macro_line = line
                j = i + 1
                # 处理多行宏
                while j < len(lines) and lines[j-1].strip().endswith('\\'):
                    macro_line += '\n' + lines[j]
                    j += 1
                
                # 提取宏名
                macro_match = re.search(r'#define\s+(\w+)', macro_line)
                if macro_match:
                    macro_name = macro_match.group(1)
                    
                    # 计算行号（从1开始）
                    start_line = i + 1
                    end_line = j
                    
                    # 创建宏信息
                    macro_info = {
                        "name": macro_name,
                        "filename": file_path,
                        "startLine": start_line,
                        "endLine": end_line,
                        "source": macro_line
                    }
                    
                    self.macros.append(macro_info)
    
    def _extract_inline_functions(self, source_code: str, file_path: str, root_dir: Path) -> None:
        """
        直接从源代码文本中提取内联函数（对于tree-sitter无法正确解析的情况）
        
        参数:
            source_code: 源代码
            file_path: 文件相对路径
            root_dir: 代码根目录
        """
        import re
        
        lines = source_code.split('\n')
        inline_keywords = ['IALLOC_INLINE', 'C_CTYPE_INLINE']
        
        for i in range(len(lines)):
            line = lines[i]
            
            # 查找包含内联关键字的行
            for keyword in inline_keywords:
                if keyword in line:
                    # 找到内联函数的开始位置
                    func_start = i
                    
                    # 查找函数体的开始
                    brace_count = 0
                    func_end = None
                    found_func_start = False
                    
                    # 从当前行开始寻找函数体
                    j = func_start
                    while j < len(lines):
                        current_line = lines[j]
                        
                        if '{' in current_line:
                            # 找到函数体开始
                            brace_count = current_line.count('{') - current_line.count('}')
                            found_func_start = True
                            
                            # 开始跟踪函数体结束
                            k = j + 1
                            while k < len(lines):
                                brace_count += lines[k].count('{')
                                brace_count -= lines[k].count('}')
                                
                                if brace_count == 0:
                                    # 找到函数体结束
                                    func_end = k
                                    break
                                k += 1
                            break
                        j += 1
                    
                    if found_func_start and func_end is not None:
                        # 提取函数定义
                        func_lines = lines[func_start:func_end+1]
                        func_source = '\n'.join(func_lines)
                        
                        # 使用更精确的正则表达式提取函数名
                        # 匹配函数声明模式，例如: IALLOC_INLINE void * func_name(...)
                        func_name_match = re.search(r'\b(\w+)\s*\(', func_source)
                        if func_name_match:
                            function_name = func_name_match.group(1)
                            
                            # 计算行号（从1开始）
                            start_line = func_start + 1
                            end_line = func_end + 1
                            
                            # 创建函数信息
                            func_id = f"{file_path}:{function_name}"
                            span_info = f"{file_path}:{start_line}:1:{end_line}:{len(lines[func_end])}"
                            
                            func_info = {
                                "func_defid": func_id,
                                "func_name": function_name,
                                "span": span_info,
                                "pieces": [span_info],
                                "sub_chunks": [],
                                "num_lines": end_line - start_line + 1,
                                "source": func_source,
                                "calls": [],
                                "callees": [],
                                "globals": [],
                                "imports": [],
                                "chunks": []
                            }
                            
                            # 添加到函数列表
                            self.functions.append(func_info)
                            
                            # 跳过已处理的函数体
                            i = func_end
                            break
    
    def _build_call_relationships(self) -> None:
        """
        构建函数调用关系，填充每个函数的调用者信息
        calls标签存储的是调用该函数的调用者信息，source是调用该函数那一行代码
        """
        # 创建函数名到函数信息的映射
        func_name_map = {func["func_name"]: func for func in self.functions}
        
        # 初始化所有函数的calls为空列表
        for func in self.functions:
            func["calls"] = []
        
        # 遍历所有函数，建立反向调用关系
        for caller_func in self.functions:
            caller_name = caller_func["func_name"]
            
            # 遍历该函数调用的所有函数（存储在callees中）
            for call_info in caller_func["callees"]:
                callee_name = call_info["callee"]
                
                # 如果被调用函数在函数映射中存在
                if callee_name in func_name_map:
                    callee_func = func_name_map[callee_name]
                    
                    # 创建调用者信息，添加到被调用函数的calls中（与标准格式一致）
                    caller_info = {
                        "caller": caller_func["func_defid"],
                        "span": call_info["span"],
                        "source": call_info["source"]  # 这是调用该函数那一行代码
                    }
                    
                    callee_func["calls"].append(caller_info)
        
        # 删除临时的callees字段
        for func in self.functions:
            if "callees" in func:
                del func["callees"]
    
    def _save_results(self, output_file: str) -> None:
        """
        保存分析结果到JSON文件，移除内部使用的字段，确保与标准格式一致
        
        参数:
            output_file: 输出文件路径
        """
        # 准备输出数据，移除内部使用的字段
        output_functions = []
        for func in self.functions:
            # 创建一个副本，避免修改原始数据
            output_func = func.copy()
            
            # 移除内部使用的字段
            if "func_name" in output_func:
                del output_func["func_name"]
            
            output_functions.append(output_func)
        
        # 写入JSON文件，只写入函数列表，格式与slice.json一致
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_functions, f, indent=2, ensure_ascii=False)
    
    def get_project_analysis(self) -> Dict[str, Any]:
        """
        获取完整的项目分析结果
        
        返回:
            包含项目分析结果的字典
        """
        # 准备输出数据，移除内部使用的字段
        output_functions = []
        for func in self.functions:
            # 创建一个副本，避免修改原始数据
            output_func = func.copy()
            
            # 移除内部使用的字段
            if "func_name" in output_func:
                del output_func["func_name"]
            
            output_functions.append(output_func)
        
        # 构建项目分析结果
        project_analysis = {
            "functions": output_functions,
            "global_vars": self.global_vars,
            "structs": self.structs,
            "macros": self.macros,
            "file_path_map": self.file_path_map
        }
        
        return project_analysis


def draw_network(output_file: str):
    functions = json.loads(Path(output_file).read_text())

    # Build call graph of functions
    call_graph = nx.DiGraph()
    for func in functions:
        call_graph.add_node('"{}"'.format(func['func_defid']))
        for call in func['calls']:
            call_graph.add_edge('"{}"'.format(call['caller']), '"{}"'.format(func['func_defid']))

    nx.drawing.nx_pydot.write_dot(call_graph, Path(output_file).with_suffix('.dot'))
    try:
        run('dot -Tpdf {} -o {}'.format(Path(output_file).with_suffix('.dot'), Path(output_file).with_suffix('.pdf')))
    except:
        prRed('Warning - failed to generate callgraph PDF')
        pass

def main():
    parser = argparse.ArgumentParser(description='C代码分析器，使用tree-sitter分析C代码仓库')
    parser.add_argument('--input_dir', help='C代码目录路径')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')

    args = parser.parse_args()
    
    input_dir_name = Path(args.input_dir).name
    print(f"输入目录的最后一层文件夹名: {input_dir_name}")
    output_file = "parse/res/{}.json".format(input_dir_name)
    
    try:
        analyzer = CCodeAnalyzer()
        analyzer.analyze_directory(args.input_dir, output_file)
        draw_network(output_file)
    except Exception as e:
        print(f"分析过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()