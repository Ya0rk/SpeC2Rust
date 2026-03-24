#!/usr/bin/env python3
"""
C 到 Rust 项目转换 Agent 主程序

完整流程：
1. 根据 C 项目生成项目文档
2. 根据文档生成 Rust 代码
3. 对生成的 Rust 代码进行编译和测试修复
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent.c_doc_agent import CDocAgent
from agent.rust_agent import RustAgent
from agent.code_fixer_agent import CodeFixer, TestFixer
from utils.fmtpr import prGreen, prRed, prBlue, prYellow


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description="C 到 Rust 项目转换 Agent - 从 C 项目自动生成 Rust 实现"
    )
    parser.add_argument(
        "--c_project_path",
        help="C 项目路径"
    )
    parser.add_argument(
        "--output_dir",
        help="输出目录（文档和 Rust 项目保存位置）"
    )
    parser.add_argument(
        "--rust-project-name",
        default="rust_implementation",
        help="Rust 项目名称（默认：rust_implementation）"
    )
    parser.add_argument(
        "--model-name",
        default="qwen32",
        choices=["qwen7", "qwen14", "qwen32", "oai", "deepseek"],
        help="模型名称（默认：qwen32）"
    )
    parser.add_argument(
        "--skip-c-analysis",
        action="store_true",
        help="跳过 C 项目分析步骤"
    )
    parser.add_argument(
        "--skip-code-fix",
        action="store_true",
        help="跳过代码修复步骤"
    )
    parser.add_argument(
        "--skip-test-fix",
        action="store_true",
        help="跳过测试修复步骤"
    )
    parser.add_argument(
        "--max-fix-iterations",
        type=int,
        default=5,
        help="最大修复迭代次数（默认：5）"
    )
    
    args = parser.parse_args()

    # 构建模型名称
    model_name = args.model_name
    
    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)
    
    # 创建临时目录存放 C 项目文档
    c_doc_dir = os.path.join(args.output_dir, "c_docs")
    os.makedirs(c_doc_dir, exist_ok=True)
    
    prBlue("\n" + "=" * 80)
    prYellow("C 到 Rust 项目转换 Agent")
    prBlue("=" * 80)
    prYellow(f"C 项目路径：{args.c_project_path}")
    prYellow(f"输出目录：{args.output_dir}")
    prYellow(f"Rust 项目名称：{args.rust_project_name}")
    prYellow(f"使用模型：{model_name}")
    prYellow(f"最大修复迭代次数：{args.max_fix_iterations}")
    prBlue("=" * 80)
    
    # 加载配置
    from config.config import Config
    config = Config(config_path=None, model_name=model_name)
    
    # =========================================================================
    # 步骤 1: 分析 C 项目并生成文档
    # =========================================================================
    if not args.skip_c_analysis:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 1: 分析 C 项目并生成文档")
        prBlue("=" * 80)
        
        c_agent = CDocAgent(config=config)
        c_agent.analyze_project(args.c_project_path, c_doc_dir)
        
        prGreen("\n✓ C 项目分析完成")
        prGreen(f"  文档保存在：{c_doc_dir}")
    else:
        prRed("\n⊘ 跳过 C 项目分析步骤")
    
    # 收集生成的文档路径
    doc_paths = []
    # 收集目标文档文件
    target_doc_name = ["final_project_overview.md"]
    for doc_file in target_doc_name:
        doc_path = os.path.join(c_doc_dir, doc_file)
        if os.path.exists(doc_path):
            doc_paths.append(doc_path)
    
    if not doc_paths:
        prRed("\n✗ 错误：未找到 C 项目文档")
        sys.exit(1)
    
    prGreen(f"\n✓ 找到 {len(doc_paths)} 个文档文件")
    
    # =========================================================================
    # 步骤 2: 根据 C 项目文档生成 Rust 代码
    # =========================================================================
    prBlue("\n" + "=" * 80)
    prYellow("步骤 2: 根据文档生成 Rust 代码")
    prBlue("=" * 80)
    
    rust_agent = RustAgent(config=config)
    success = rust_agent.generate_from_docs(
        project_name=args.rust_project_name,
        output_dir=args.output_dir,
        doc_paths=doc_paths
    )
    
    if not success:
        prRed("\n✗ Rust 代码生成失败")
        sys.exit(1)
    
    rust_project_path = os.path.join(args.output_dir, args.rust_project_name)
    prGreen(f"\n✓ Rust 代码生成完成")
    prGreen(f"  项目路径：{rust_project_path}")
    
    # =========================================================================
    # 步骤 3: 对生成的 Rust 代码进行编译修复: 格式化 + check + build
    # =========================================================================
    if not args.skip_code_fix:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 3: 编译修复 Rust 代码")
        prBlue("=" * 80)
        
        code_fixer = CodeFixer(
            config=config,
            project_path=rust_project_path,
            max_iterations=args.max_fix_iterations
        )
        
        success = code_fixer.fix()
        
        if success:
            prGreen("\n✓ 代码编译修复成功")
        else:
            prRed("\n⚠ 代码编译修复失败，但项目可能仍可使用")
    else:
        prRed("\n⊘ 跳过代码编译修复步骤")
    
    # =========================================================================
    # 步骤 4: 对生成的 Rust 代码进行测试修复
    # =========================================================================
    if not args.skip_test_fix:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 4: 测试修复 Rust 代码")
        prBlue("=" * 80)
        
        test_fixer = TestFixer(
            config=config,
            project_path=rust_project_path,
            max_iterations=args.max_fix_iterations
        )
        
        success = test_fixer.fix()
        
        if success:
            prGreen("\n✓ 所有测试通过")
        else:
            prRed("\n⚠ 测试修复失败，但项目可能仍可使用")
    else:
        prRed("\n⊘ 跳过测试修复步骤")
    
    # =========================================================================
    # 完成
    # =========================================================================
    prBlue("\n" + "=" * 80)
    prGreen("C 到 Rust 项目转换完成！")
    prBlue("=" * 80)
    prGreen(f"Rust 项目路径：{rust_project_path}")
    prGreen("\n生成的文件:")
    prGreen(f"  - C 项目文档：{c_doc_dir}/")
    prGreen(f"  - Rust 项目：{rust_project_path}/")
    prBlue("=" * 80 + "\n")


if __name__ == "__main__":
    main()
