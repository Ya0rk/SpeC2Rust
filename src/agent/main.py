#!/usr/bin/env python3
"""
C 到 Rust 项目转换 Agent 主程序

完整流程：
1. 根据 C 项目生成项目文档
2. 根据文档生成 Rust 代码
3. 对生成的 Rust 代码进行编译和测试修复
"""

import argparse
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
repo_root = project_root.parent
sys.path.insert(0, str(project_root))

from agent.c_doc_agent import CDocAgent
from agent.error_organizer_agent import ErrorOrganizerAgent
from agent.pointer_agent import PointerAgent
from agent.spec_agent import SpecAgent
from agent.spec_json_agent import SpecJsonAgent
from agent.rust_agent import RustAgent
from agent.code_fixer_agent import CodeFixer, TestFixer
from config.config import Config
from utils.fmtpr import prGreen, prRed, prBlue, prYellow


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="C 到 Rust 项目转换 Agent - 从 C 项目自动生成 Rust 实现"
    )
    parser.add_argument("--c_project_path", help="C 项目路径")
    parser.add_argument("--output_dir", help="输出目录（文档和 Rust 项目保存位置）")
    parser.add_argument(
        "--rust-project-name",
        default="rust_implementation",
        help="Rust 项目名称（默认：rust_implementation）"
    )
    parser.add_argument(
        "--config-file",
        default=str(repo_root / "local_config.json"),
        help="配置文件路径（默认：仓库根目录 local_config.json）"
    )
    parser.add_argument(
        "--skip-c-analysis",
        action="store_true",
        help="跳过 C 项目分析步骤"
    )
    parser.add_argument(
        "--use-spec-agent",
        action="store_true",
        help="使用 SpecAgent 作为可选分析路径"
    )
    parser.add_argument(
        "--use-spec-json-agent",
        action="store_true",
        help="在 SpecAgent 之后增加 JSON 压缩中间层，生成机器友好的 spec_context.json"
    )
    parser.add_argument(
        "--use-pointer-agent",
        action="store_true",
        help="可选开启指针分析中间层，生成 C 指针到 Rust 的翻译指导文档"
    )
    parser.add_argument(
        "--use-error-organizer-agent",
        action="store_true",
        help="可选开启错误梳理中间层，先归类并分批整理错误，再交给修复器处理"
    )
    parser.add_argument(
        "--error-batch-size",
        type=int,
        default=10,
        help="错误梳理分批大小（默认：10）"
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

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)

    # 创建临时目录存放 C 项目文档
    c_doc_dir = os.path.join(args.output_dir, "c_docs")
    os.makedirs(c_doc_dir, exist_ok=True)

    # 加载配置
    config = Config(config_path=args.config_file)

    prBlue("\n" + "=" * 80)
    prYellow("C 到 Rust 项目转换 Agent")
    prYellow(f"ErrorOrganizerAgent：{'开启' if args.use_error_organizer_agent else '关闭'}")
    prYellow(f"错误分批大小：{args.error_batch_size}")
    prBlue("=" * 80)

    error_organizer_agent = None
    if args.use_error_organizer_agent:
        error_organizer_agent = ErrorOrganizerAgent(batch_size=args.error_batch_size)
    prYellow(f"C 项目路径：{args.c_project_path}")
    prYellow(f"输出目录：{args.output_dir}")
    prYellow(f"Rust 项目名称：{args.rust_project_name}")
    prYellow(f"配置文件：{args.config_file}")
    prYellow(f"模型名称：{config.model_name}")
    prYellow(f"远程模型：{config.api_model or '(default)'}")
    prYellow(f"分析路径：{'SpecAgent' if args.use_spec_agent else 'CDocAgent'}")
    prYellow(f"Spec JSON 中间层：{'开启' if args.use_spec_json_agent else '关闭'}")
    prYellow(f"PointerAgent：{'开启' if args.use_pointer_agent else '关闭'}")
    prYellow(f"最大修复迭代次数：{args.max_fix_iterations}")
    prBlue("=" * 80)

    if args.use_spec_json_agent and not args.use_spec_agent:
        prYellow("提示：Spec JSON 中间层仅对 SpecAgent 路径生效，当前将忽略该开关。")

    # =========================================================================
    # 步骤 1: 分析 C 项目并生成文档
    # =========================================================================
    if not args.skip_c_analysis:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 1: 分析 C 项目并生成文档")
        prBlue("=" * 80)

        if args.use_spec_agent:
            c_agent = SpecAgent(config=config)
            c_agent.analyze_and_generate_spec(args.c_project_path, c_doc_dir)
        else:
            c_agent = CDocAgent(config=config)
            c_agent.analyze_project(args.c_project_path, c_doc_dir)

        prGreen("\n✓ C 项目分析完成")
        prGreen(f"  文档保存在：{c_doc_dir}")
    else:
        prRed("\n⊘ 跳过 C 项目分析步骤")

    spec_json_path = os.path.join(c_doc_dir, "spec_json", "spec_context.json")
    pointer_markdown_path = os.path.join(c_doc_dir, "pointer_guidance.md")

    # 可选步骤 1.5: 将 SpecAgent 产出的文档压缩为机器友好的 JSON
    if args.use_spec_agent and args.use_spec_json_agent:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 1.5: 压缩 Spec 文档为 JSON 中间层")
        prBlue("=" * 80)

        spec_json_agent = SpecJsonAgent(config=config)
        spec_json_path = spec_json_agent.compress_spec_docs(c_doc_dir)

        prGreen("\n✓ Spec JSON 中间层生成完成")
        prGreen(f"  JSON 路径：{spec_json_path}")

    # 可选步骤 1.6: 分析 C 项目中的指针用法，生成 Rust 翻译指导
    if args.use_pointer_agent:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 1.6: 生成指针翻译指导文档")
        prBlue("=" * 80)

        pointer_agent = PointerAgent(config=config)
        pointer_outputs = pointer_agent.analyze_project(args.c_project_path, c_doc_dir)
        pointer_markdown_path = pointer_outputs["markdown_path"]

        prGreen("\n✓ 指针翻译指导文档生成完成")
        prGreen(f"  文档路径：{pointer_markdown_path}")

    # 收集生成的文档路径
    doc_paths = []
    if args.use_spec_agent:
        if args.use_spec_json_agent and os.path.exists(spec_json_path):
            doc_paths.append(spec_json_path)
        else:
            candidate_paths = [
                os.path.join(c_doc_dir, "docs", "rewrite-context"),
                os.path.join(c_doc_dir, ".specify", "memory")
            ]
            for doc_path in candidate_paths:
                if os.path.exists(doc_path):
                    doc_paths.append(doc_path)
    else:
        target_doc_name = ["final_project_overview.md"]
        for doc_file in target_doc_name:
            doc_path = os.path.join(c_doc_dir, doc_file)
            if os.path.exists(doc_path):
                doc_paths.append(doc_path)

    if args.use_pointer_agent and os.path.exists(pointer_markdown_path):
        doc_paths.append(pointer_markdown_path)

    if not doc_paths:
        prRed("\n✗ 错误：未找到 C 项目文档")
        sys.exit(1)

    prGreen(f"\n✓ 找到 {len(doc_paths)} 个文档文件")

    # =========================================================================
    # 步骤 2: 根据文档生成 Rust 代码
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
    # 步骤 3: 对生成的 Rust 代码进行编译修复
    # =========================================================================
    if not args.skip_code_fix:
        prBlue("\n" + "=" * 80)
        prYellow("步骤 3: 编译修复 Rust 代码")
        prBlue("=" * 80)

        code_fixer = CodeFixer(
            config=config,
            project_path=rust_project_path,
            max_iterations=args.max_fix_iterations,
            error_organizer_agent=error_organizer_agent
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
            max_iterations=args.max_fix_iterations,
            error_organizer_agent=error_organizer_agent
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
    prGreen("\n生成的文件：")
    prGreen(f"  - C 项目文档：{c_doc_dir}/")
    prGreen(f"  - Rust 项目：{rust_project_path}/")
    prBlue("=" * 80 + "\n")


if __name__ == "__main__":
    main()
