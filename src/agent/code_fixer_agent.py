import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple


sys.path.append(str(Path(__file__).parent.parent))

from config.config import Config
from utils.cmd import run
from config.prompt import prompt_manager
from llm.model import Model


class Fixer:
    """代码修复父类 - 提供通用的修复功能"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 5):
        """
        初始化修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        self.llm = Model(config)

        self.project_path = project_path
        self.max_iterations = max_iterations
        self.fix_history = []
    
    def _run_command(self, cmd: str) -> Tuple[bool, str]:
        """
        运行命令并返回结果
        
        Args:
            cmd: 命令字符串
            
        Returns:
            (是否成功，输出/错误信息)
        """
        result = run(cmd)
        success = result is None
        output = result if result is None else result.strip()
        return success, output
    
    def _extract_code(self, code: str) -> str:
        """
        从 LLM 响应中提取代码（去除 markdown 标记）
        
        Args:
            code: 包含 markdown 标记的代码字符串
            
        Returns:
            纯代码字符串
        """
        if '```rust' in code:
            code = code.split('```rust')[1].split('```')[0].strip()
        elif '```' in code:
            code = code.split('```')[1].split('```')[0].strip()
        return code
    
    def _fix_file(self, file_path: str, error_type: str, error_message: str) -> bool:
        """
        修复单个文件
        
        Args:
            file_path: 文件路径
            error_type: 错误类型
            error_message: 错误信息
            
        Returns:
            是否成功修复
        """
        # 读取文件内容
        # TODO: 使用函数级代码读取
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except Exception as e:
            print(f"读取文件失败：{e}")
            return False
        
        prompt = self._generate_fix_prompt(error_type, error_message, file_content)
        
        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        fixed_code = response[0]
        
        fixed_code = self._extract_code(fixed_code)
        
        # TODO: 使用函数级代码写入
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(fixed_code)
            return True
        except Exception as e:
            print(f"写入文件失败：{e}")
            return False
    
    def _generate_fix_prompt(self, error_type: str, error_message: str, file_content: str = "") -> str:
        """
        生成修复提示（子类重写）
        
        Args:
            error_type: 错误类型
            error_message: 错误信息
            file_content: 文件内容
            
        Returns:
            提示字符串
        """
        return prompt_manager.get('code_fixer', 'generate_fix_prompt',
                                 error_type=error_type,
                                 error_message=error_message,
                                 file_content=file_content)
    
    def _get_system_prompt(self) -> str:
        """
        获取系统提示（子类重写）
        
        Returns:
            系统提示字符串
        """
        return prompt_manager.get('code_fixer', 'system_prompt')
    
    def fix(self) -> bool:
        """
        执行修复流程（子类重写）
        
        Returns:
            是否成功修复
        """
        raise NotImplementedError("子类必须实现此方法")


class CodeFixer(Fixer):
    """代码修复模块 - 根据格式化、检查、编译错误进行多轮代码修复"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 5):
        """
        初始化代码修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        super().__init__(config, project_path, max_iterations)
    
    def _format_code(self) -> Tuple[bool, str]:
        """
        格式化代码
        
        Returns:
            (是否成功，cargo fmt 的输出)
        """
        cmd = f"cd {self.project_path} && cargo fmt"
        return self._run_command(cmd)
    
    def _check_code(self) -> Tuple[bool, str]:
        """
        检查代码
        
        Returns:
            (是否成功，cargo check 的输出)
        """
        cmd = f"cd {self.project_path} && cargo check"
        return self._run_command(cmd)
    
    def _build_code(self) -> Tuple[bool, str]:
        """
        编译代码
        
        Returns:
            (是否成功，cargo build 的输出)
        """
        cmd = f"cd {self.project_path} && cargo build"
        return self._run_command(cmd)
    
    def _parse_error_to_file(self, error_message: str) -> Optional[str]:
        """
        从错误信息中解析出有问题的文件路径
        
        Args:
            error_message: 错误信息（来自 cargo fmt/check/build 的输出）
                        例如："error: expected `;`, found `}`\n
                             --> src/lib.rs:10:5"
            
        Returns:
            文件路径或 None
        """
        # 匹配 Rust 错误信息中的文件路径模式：--> src/lib.rs:10:5
        match = re.search(r'--> ([^:]+):(\d+):(\d+)', error_message)
        if match:
            file_path = match.group(1)
            # 如果是相对路径，转换为绝对路径
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)
            return file_path
        return None
    
    def fix(self) -> bool:
        """
        执行代码修复流程
        
        Returns:
            是否成功修复所有错误
        """
        print(f"开始代码修复，最大迭代次数：{self.max_iterations}")
        format_success = False
        check_success = False
        build_success = False
        
        print("1. 格式化代码...")
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮格式化 ===")
            
            format_success, format_output = self._format_code()
            print(f"格式化输出：{format_output}")
            pause = input("按任意键继续...")
            if format_success:
                print("代码格式化通过")
                break
            else :
                print(f"格式化失败：{format_output}")
                file_path = self._parse_error_to_file(format_output)
                # print(f"解析到的文件路径：{file_path}")
                # pause = input("按任意键继续...")
                if file_path and os.path.exists(file_path):
                    if self._fix_file(file_path, "format", format_output):
                        continue  # 修复后继续下一轮
                else:
                    print("无法定位需要格式化修复的文件")
                    self.fix_history.append({
                        'iteration': iteration,
                        'type': 'format',
                        'error': format_output,
                        'success': False
                    })
                    continue
        
        pause = input("格式化修复完成，按任意键继续...")

        if not format_success:
            print("格式化代码失败，无法进行后续修复")
            return False

        print("2. 检查代码...")
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮check代码 ===")
            # 2. 检查代码
            check_success, check_output = self._check_code()
            print(f"检查输出：{check_output}")
            pause = input("按任意键继续...")
            if check_success:
                print("代码检查通过")
                break
            else:
                print(f"代码检查失败：{check_output}")
                # TODO: 修复逻辑有问题，不应该自己解析文件路径，而是将编译器报错信息传递给llm
                # TODO: llm思考后决定需要读取和修改哪些文件
                file_path = self._parse_error_to_file(check_output)
                print(f"解析到的文件路径：{file_path}")
                pause = input("按任意键继续...")
                if file_path and os.path.exists(file_path):
                    if self._fix_file(file_path, "check", check_output):
                        continue  # 修复后继续下一轮
                else:
                    print("无法定位需要check修复的文件")
        
        if not check_success:
            print("检查代码失败，无法进行后续修复")
            return False
        
        pause = input("check修复完成，按任意键继续...")


        print("3. 编译代码...")
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮编译 ===")
            build_success, build_output = self._build_code()
            if build_success:
                print("代码编译通过")
                self.fix_history.append({
                    'iteration': iteration,
                    'type': 'build',
                    'success': True
                })
                return True
            else:
                print(f"编译失败：{build_output}")
                file_path = self._parse_error_to_file(build_output)
                if file_path and os.path.exists(file_path):
                    if self._fix_file(file_path, "build", build_output):
                        continue  # 修复后继续下一轮
                else:
                    print("无法定位需要build修复的文件")
                    self.fix_history.append({
                        'iteration': iteration,
                        'type': 'build',
                        'error': build_output,
                        'success': False
                    })
            
            # # 保存修复历史
            # self.fix_history.append({
            #     'iteration': iteration,
            #     'format_success': format_success,
            #     'check_success': check_success,
            #     'build_success': build_success
            # })
        
        print("\n达到最大迭代次数，build修复失败")
        return False


class TestFixer(Fixer):
    """代码测试修复模块 - 根据测试失败信息进行多轮修复"""
    
    def __init__(self, config: Config, project_path: str, max_iterations: int = 5):
        """
        初始化测试修复器
        
        Args:
            config: 配置对象
            project_path: 项目路径
            max_iterations: 最大迭代次数
        """
        super().__init__(config, project_path, max_iterations)
    
    def _run_tests(self) -> Tuple[bool, str]:
        """运行测试"""
        cmd = f"cd {self.project_path} && cargo test"
        return self._run_command(cmd)
    
    def _generate_fix_prompt(self, test_error: str, test_name: str, file_content: str = "") -> str:
        """
        生成测试修复提示
        
        Args:
            test_error: 测试错误信息
            test_name: 失败的测试名称
            file_content: 文件内容
            
        Returns:
            提示字符串
        """
        return prompt_manager.get('test_fixer', 'generate_fix_prompt',
                                 test_error=test_error,
                                 test_name=test_name,
                                 file_content=file_content)
    
    def _get_system_prompt(self) -> str:
        """获取系统提示"""
        return prompt_manager.get('test_fixer', 'system_prompt')
    
    def _parse_test_error(self, error_message: str) -> Tuple[Optional[str], str]:
        """
        从测试错误信息中解析出测试名称和文件路径
        
        Args:
            error_message: 错误信息
            
        Returns:
            (文件路径，测试名称)
        """
        # 匹配测试名称
        test_name_match = re.search(r'test (\S+) \.\.\. FAILED', error_message)
        test_name = test_name_match.group(1) if test_name_match else "unknown"
        
        # 匹配文件路径
        match = re.search(r'--> ([^:]+):(\d+):(\d+)', error_message)
        if match:
            file_path = match.group(1)
            if not os.path.isabs(file_path):
                file_path = os.path.join(self.project_path, file_path)
            return file_path, test_name
        
        # 尝试从 src/lib.rs 或 src/main.rs 中查找
        default_paths = [
            os.path.join(self.project_path, 'src', 'lib.rs'),
            os.path.join(self.project_path, 'src', 'main.rs')
        ]
        
        for path in default_paths:
            if os.path.exists(path):
                return path, test_name
        
        return None, test_name
    
    def _extract_test_code(self, file_content: str, test_name: str) -> str:
        """
        从文件内容中提取测试相关代码
        
        Args:
            file_content: 文件内容
            test_name: 测试名称
            
        Returns:
            测试相关代码
        """
        # 查找测试函数
        pattern = r'#\[test\]\s*(?:fn\s+' + re.escape(test_name.split('::')[-1]) + r'[\s\S]*?\})'
        match = re.search(pattern, file_content)
        if match:
            return match.group(0)
        
        # 如果没有找到特定测试，返回整个文件
        return file_content
    
    def _fix_file(self, file_path: str, test_error: str, test_name: str) -> bool:
        """
        修复单个文件
        
        Args:
            file_path: 文件路径
            test_error: 测试错误信息
            test_name: 测试名称
            
        Returns:
            是否成功修复
        """
        # 读取文件内容
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except Exception as e:
            print(f"读取文件失败：{e}")
            return False
        
        # 提取测试相关代码
        test_code = self._extract_test_code(file_content, test_name)
        
        prompt = self._generate_fix_prompt(test_error, test_name, test_code)
        
        messages = [
            {'role': 'system', 'content': self._get_system_prompt()},
            {'role': 'user', 'content': prompt}
        ]
        
        response = self.llm.generate(messages)
        fixed_code = response[0]
        
        fixed_code = self._extract_code(fixed_code)
        
        # 写入修复后的代码
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(fixed_code)
            return True
        except Exception as e:
            print(f"写入文件失败：{e}")
            return False
    
    def fix(self) -> bool:
        """
        执行测试修复流程
        
        Returns:
            是否所有测试通过
        """
        print(f"开始测试修复，最大迭代次数：{self.max_iterations}")
        
        for iteration in range(1, self.max_iterations + 1):
            print(f"\n=== 第 {iteration} 轮测试修复 ===")
            
            # 运行测试
            test_success, test_output = self._run_tests()
            
            if test_success:
                print("所有测试通过！")
                self.fix_history.append({
                    'iteration': iteration,
                    'success': True
                })
                return True
            
            print(f"测试失败：\n{test_output}")
            
            # 解析测试错误
            file_path, test_name = self._parse_test_error(test_output)
            
            if file_path and os.path.exists(file_path):
                print(f"定位到失败测试：{test_name}，文件：{file_path}")
                if self._fix_file(file_path, test_output, test_name):
                    self.fix_history.append({
                        'iteration': iteration,
                        'test_name': test_name,
                        'file': file_path,
                        'error': test_output,
                        'fixed': True
                    })
                    continue  # 修复后继续下一轮测试
            else:
                print(f"无法定位测试文件，测试名称：{test_name}")
            
            self.fix_history.append({
                'iteration': iteration,
                'test_name': test_name,
                'file': file_path,
                'error': test_output,
                'fixed': False
            })
        
        print("\n达到最大迭代次数，测试修复失败")
        return False
