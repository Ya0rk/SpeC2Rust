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
from agent.macro_agent import MacroAgent
from agent.pointer_agent import PointerAgent
from agent.spec_agent import SpecAgent
from agent.spec_json_agent import SpecJsonAgent
from agent.rust_agent import RustAgent
from agent.alternatives.stable_rust_agent import StableRustAgent
from agent.alternatives.growth_rust_agent import GrowthRustAgent
from agent.alternatives.contextual_rust_agent import ContextualRustAgent
from agent.code_fixer_agent import CodeFixer, TestFixer
from agent.rust_repair_agent import RustRepairAgent
from agent.unfinished_code_agent import UnfinishedCodeAgent
from config.config import Config
from utils.fmtpr import prGreen, prRed, prBlue, prYellow
from utils.translation_metrics import translation_metrics


def c_docs_writable(args) -> bool:
    return not getattr(args, "freeze_c_docs", False)


def should_run_primary_c_analysis(args) -> bool:
    return not args.skip_c_analysis and c_docs_writable(args)


def should_run_spec_json_stage(args) -> bool:
    return args.use_spec_agent and args.use_spec_json_agent and c_docs_writable(args)


def should_run_pointer_stage(args) -> bool:
    return args.use_pointer_agent and not args.use_spec_agent and c_docs_writable(args)


def should_run_macro_stage(args) -> bool:
    return args.use_macro_agent and not args.use_spec_agent and c_docs_writable(args)


def selected_rust_agent_mode(args) -> str:
    if getattr(args, "use_contextual_rust_agent", False):
        return "ContextualRustAgent"
    if getattr(args, "use_growth_rust_agent", False):
        return "GrowthRustAgent"
    if getattr(args, "use_stable_rust_agent", False):
        return "StableRustAgent"
    return "RustAgent"


def run_optional_rust_repair_agent(args, config: Config, rust_project_path: str):
    """
    可选运行独立 RustRepairAgent。
    主流程中默认原地修复当前 Rust 项目，不再创建新的修复项目作为最终产物。
    """
    if not getattr(args, "use_rust_repair_agent", False):
        return None

    prBlue("\n" + "=" * 80)
    prYellow("步骤 4.5: RustRepairAgent 深度修复")
    prBlue("=" * 80)

    repair_agent = RustRepairAgent(
        config=config,
        max_iterations=getattr(args, "rust_repair_max_iterations", 15),
    )
    result = repair_agent.repair_project(
        project_path=rust_project_path,
        in_place=True,
        apply_best=False,
    )

    if result.check_passed and result.test_passed:
        prGreen("\n✓ RustRepairAgent 修复后 cargo check/test 通过")
    elif result.check_passed:
        prYellow("\n⚠ RustRepairAgent 修复后 cargo check 通过，但测试仍未完全通过")
    else:
        prRed(f"\n⚠ RustRepairAgent 修复后仍有 {result.error_count} 个错误")
    return result


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
        "--freeze-c-docs",
        action="store_true",
        help="完全禁止产生任何新的 c_docs 文件，只复用已有文档"
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
        "--use-macro-agent",
        action="store_true",
        help="可选开启宏分析中间层，生成 C 宏到 Rust 的迁移指导文档"
    )
    parser.add_argument(
        "--use-stable-rust-agent",
        action="store_true",
        help="使用可选的 StableRustAgent，采用更薄、更可控的代码生成路径"
    )
    parser.add_argument(
        "--use-growth-rust-agent",
        action="store_true",
        help="使用可选的 GrowthRustAgent，按主树干最小可编译集逐步生长式生成代码"
    )
    parser.add_argument(
        "--use-contextual-rust-agent",
        action="store_true",
        help="使用可选的 ContextualRustAgent，按需读取 spec/source/Rust 上下文并维护符号表"
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
        "--skip-unfinished-check",
        action="store_true",
        help="跳过未完成实现检查步骤（默认会扫描 todo!/unimplemented! 并尝试续写）"
    )
    parser.add_argument(
        "--unfinished-max-passes",
        type=int,
        default=2,
        help="未完成实现检查的最大续写轮数（默认：2）"
    )
    parser.add_argument(
        "--skip-test-fix",
        action="store_true",
        help="跳过测试修复步骤"
    )
    parser.add_argument(
        "--use-rust-repair-agent",
        action="store_true",
        help="可选开启 RustRepairAgent，在主流程末尾对当前 Rust 项目做原地深度修复"
    )
    parser.add_argument(
        "--rust-repair-max-iterations",
        type=int,
        default=15,
        help="RustRepairAgent 最大修复迭代次数（默认：15）"
    )
    parser.add_argument(
        "--continue",
        dest="continue_run",
        action="store_true",
        help="继续上一次生成，复用已有项目与生成计划；默认不传时会全量重建 Rust 项目"
    )
    parser.add_argument(
        "--max-fix-iterations",
        type=int,
        default=10,
        help="最大修复迭代次数（默认：10）"
    )

    args = parser.parse_args()

    translation_metrics.start()
    rust_project_path = os.path.join(args.output_dir, args.rust_project_name) if args.output_dir else ""
    metrics_path = ""

    try:
        # 创建输出目录
        os.makedirs(args.output_dir, exist_ok=True)

        c_doc_dir = os.path.join(args.output_dir, "c_docs")
        if c_docs_writable(args):
            # 创建临时目录存放 C 项目文档
            os.makedirs(c_doc_dir, exist_ok=True)

        # 加载配置
        config = Config(config_path=args.config_file)

        prBlue("\n" + "=" * 80)
        prYellow("C 到 Rust 项目转换 Agent")
        prYellow(f"ErrorOrganizerAgent：{'开启' if args.use_error_organizer_agent else '关闭'}")
        prYellow(f"错误分批大小：{args.error_batch_size}")
        prYellow(f"MacroAgent：{'开启' if args.use_macro_agent else '关闭'}")
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
        rust_agent_mode = selected_rust_agent_mode(args)
        prYellow(f"代码生成路径：{rust_agent_mode}")
        prYellow(f"续跑模式：{'开启' if args.continue_run else '关闭（默认全量重建）'}")
        prYellow(f"未完成实现检查：{'关闭' if args.skip_unfinished_check else '开启'}")
        prYellow(f"未完成实现最大续写轮数：{args.unfinished_max_passes}")
        prYellow(f"Spec JSON 中间层：{'开启' if args.use_spec_json_agent else '关闭'}")
        prYellow(f"PointerAgent：{'开启' if args.use_pointer_agent else '关闭'}")
        prYellow(f"冻结 c_docs：{'开启' if args.freeze_c_docs else '关闭'}")
        prYellow(f"最大修复迭代次数：{args.max_fix_iterations}")
        prBlue("=" * 80)

        rust_agent_flag_count = sum(
            1
            for enabled in [
                args.use_stable_rust_agent,
                args.use_growth_rust_agent,
                args.use_contextual_rust_agent,
            ]
            if enabled
        )
        if rust_agent_flag_count > 1:
            prRed("\n✗ 错误：--use-stable-rust-agent、--use-growth-rust-agent、--use-contextual-rust-agent 只能开启一个")
            return 1

        if args.use_spec_json_agent and not args.use_spec_agent:
            prYellow("提示：Spec JSON 中间层仅对 SpecAgent 路径生效，当前将忽略该开关。")

        if args.freeze_c_docs:
            prYellow("提示：--freeze-c-docs 已开启，将跳过所有会写入 c_docs 的步骤，只读取已有文档。")

        # =========================================================================
        # 步骤 1: 分析 C 项目并生成文档
        # =========================================================================
        if should_run_primary_c_analysis(args):
            prBlue("\n" + "=" * 80)
            prYellow("步骤 1: 分析 C 项目并生成文档")
            prBlue("=" * 80)

            if args.use_spec_agent:
                c_agent = SpecAgent(config=config)
                c_agent.analyze_and_generate_spec(
                    args.c_project_path,
                    c_doc_dir,
                    use_pointer_agent=args.use_pointer_agent,
                    use_macro_agent=args.use_macro_agent,
                )
            else:
                c_agent = CDocAgent(config=config)
                c_agent.analyze_project(args.c_project_path, c_doc_dir)

            prGreen("\n✓ C 项目分析完成")
            prGreen(f"  文档保存在：{c_doc_dir}")
        elif args.skip_c_analysis:
            prRed("\n⊘ 跳过 C 项目分析步骤")
        else:
            prRed("\n⊘ 已冻结 c_docs，跳过 C 项目分析步骤")

        spec_json_path = os.path.join(c_doc_dir, "spec_json", "spec_context.json")
        pointer_markdown_path = os.path.join(c_doc_dir, "pointer_guidance.md")
        macro_markdown_path = os.path.join(c_doc_dir, "macro_guidance.md")

        # 可选步骤 1.5: 将 SpecAgent 产出的文档压缩为机器友好的 JSON
        if should_run_spec_json_stage(args):
            prBlue("\n" + "=" * 80)
            prYellow("步骤 1.5: 压缩 Spec 文档为 JSON 中间层")
            prBlue("=" * 80)

            spec_json_agent = SpecJsonAgent(config=config)
            spec_json_path = spec_json_agent.compress_spec_docs(c_doc_dir)

            prGreen("\n✓ Spec JSON 中间层生成完成")
            prGreen(f"  JSON 路径：{spec_json_path}")

        # 可选步骤 1.6: 分析 C 项目中的指针用法，生成 Rust 翻译指导
        if should_run_pointer_stage(args):
            prBlue("\n" + "=" * 80)
            prYellow("步骤 1.6: 生成指针翻译指导文档")
            prBlue("=" * 80)

            pointer_agent = PointerAgent(config=config)
            pointer_outputs = pointer_agent.analyze_project(args.c_project_path, c_doc_dir)
            pointer_markdown_path = pointer_outputs["markdown_path"]

            prGreen("\n✓ 指针翻译指导文档生成完成")
            prGreen(f"  文档路径：{pointer_markdown_path}")

        # 收集生成的文档路径
        # 可选步骤 1.7: 分析 C 项目中的宏定义，生成 Rust 迁移指导
        if should_run_macro_stage(args):
            prBlue("\n" + "=" * 80)
            prYellow("步骤 1.7: 生成宏迁移指导文档")
            prBlue("=" * 80)

            macro_agent = MacroAgent(config=config)
            macro_outputs = macro_agent.analyze_project(args.c_project_path, c_doc_dir)
            macro_markdown_path = macro_outputs["markdown_path"]

            prGreen("\n✓ 宏迁移指导文档生成完成")
            prGreen(f"  文档路径：{macro_markdown_path}")

        doc_paths = []
        if args.use_spec_agent:
            if args.use_spec_json_agent and os.path.exists(spec_json_path):
                doc_paths.append(spec_json_path)
            else:
                candidate_paths = [
                    os.path.join(c_doc_dir, ".specify", "memory"),
                    os.path.join(c_doc_dir, "docs", "rewrite-context"),
                    os.path.join(c_doc_dir, "specs"),
                ]
                for doc_path in candidate_paths:
                    if os.path.exists(doc_path):
                        doc_paths.append(doc_path)

            # SpecAgent 路径下，PointerAgent / MacroAgent 会先写入各模块目录，
            # 再由 rewrite-context/04_gaps_and_risks/ 下的汇总文件提供给 RustAgent。
            # 这样可以避免把所有模块的 pointer.md / macro.md 全部塞进上下文。
            auxiliary_summary_path = os.path.join(
                c_doc_dir,
                "docs",
                "rewrite-context",
                "04_gaps_and_risks",
                "001_pointer_macro_summary.md",
            )
            if os.path.exists(auxiliary_summary_path):
                doc_paths.append(auxiliary_summary_path)
        else:
            target_doc_name = ["final_project_overview.md"]
            for doc_file in target_doc_name:
                doc_path = os.path.join(c_doc_dir, doc_file)
                if os.path.exists(doc_path):
                    doc_paths.append(doc_path)

        if args.use_pointer_agent and not args.use_spec_agent and os.path.exists(pointer_markdown_path):
            doc_paths.append(pointer_markdown_path)
        if args.use_macro_agent and not args.use_spec_agent and os.path.exists(macro_markdown_path):
            doc_paths.append(macro_markdown_path)

        if not doc_paths:
            prRed("\n✗ 错误：未找到 C 项目文档")
            return 1

        prGreen(f"\n✓ 找到 {len(doc_paths)} 个文档文件")
        for doc_path in doc_paths:
            prGreen(f"  - 文档输入：{doc_path}")

        source_json_path = ""
        if args.c_project_path:
            candidate_source_json = repo_root / "src" / "parse" / "res" / f"{Path(args.c_project_path).name}.json"
            if candidate_source_json.exists():
                source_json_path = str(candidate_source_json)
                prGreen(f"  - 源码 JSON：{source_json_path}")
            else:
                prYellow(f"提示：未找到对应源码 JSON：{candidate_source_json}")

        # =========================================================================
        # 步骤 2: 根据文档生成 Rust 代码
        # =========================================================================
        prBlue("\n" + "=" * 80)
        prYellow("步骤 2: 根据文档生成 Rust 代码")
        prBlue("=" * 80)

        if args.use_contextual_rust_agent:
            rust_agent = ContextualRustAgent(config=config)
        elif args.use_growth_rust_agent:
            rust_agent = GrowthRustAgent(config=config)
        elif args.use_stable_rust_agent:
            rust_agent = StableRustAgent(config=config)
        else:
            rust_agent = RustAgent(config=config)
        if hasattr(rust_agent, "continue_mode"):
            rust_agent.continue_mode = args.continue_run
        success = rust_agent.generate_from_docs(
            project_name=args.rust_project_name,
            output_dir=args.output_dir,
            doc_paths=doc_paths,
            c_project_path=args.c_project_path or "",
            source_json_path=source_json_path,
        )

        if not success:
            prRed("\n✗ Rust 代码生成失败")
            return 1

        prGreen(f"\n✓ Rust 代码生成完成")
        prGreen(f"  项目路径：{rust_project_path}")

        # =========================================================================
        # 步骤 2.5: 检查并补全未完成实现
        # =========================================================================
        if not args.skip_unfinished_check:
            prBlue("\n" + "=" * 80)
            prYellow("步骤 2.5: 检查并补全未完成实现")
            prBlue("=" * 80)

            unfinished_code_agent = UnfinishedCodeAgent(config=config, rust_agent=rust_agent)
            unfinished_report = unfinished_code_agent.check_and_continue(
                project_path=rust_project_path,
                max_passes=args.unfinished_max_passes,
            )

            repaired_count = len(unfinished_report.get("repaired_files", []))
            remaining_count = len(unfinished_report.get("remaining_files", []))
            detected_any = bool(unfinished_report.get("passes"))
            if repaired_count:
                prGreen(f"\n✓ 未完成实现补全完成，已续写 {repaired_count} 个文件")
            elif detected_any:
                prRed("\n⚠ 检测到未完成实现，但本轮未成功续写任何文件")
            else:
                prGreen("\n✓ 未检测到需要续写的未完成实现")

            if remaining_count:
                prRed(f"⚠ 仍有 {remaining_count} 个文件包含未完成占位：{unfinished_report.get('remaining_files', [])}")
            else:
                prGreen("  当前项目中未检测到 todo!/unimplemented! 等未完成占位")
        else:
            prRed("\n⊘ 跳过未完成实现检查步骤")

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
        prGreen(f"  - C 项目文档：{c_doc_dir}/{'（只读复用）' if args.freeze_c_docs else ''}")
        prGreen(f"  - Rust 项目：{rust_project_path}/")
        prBlue("=" * 80 + "\n")
        return 0
    finally:
        translation_metrics.finish()
        metrics_target_dir = rust_project_path if rust_project_path else args.output_dir
        if metrics_target_dir:
            metrics_path = translation_metrics.save_to(
                os.path.join(metrics_target_dir, "translation_metrics.json")
            )
        metrics = translation_metrics.snapshot()
        prBlue("\n" + "=" * 80)
        prYellow("翻译运行指标")
        prBlue("=" * 80)
        prGreen(f"总耗时（秒）：{metrics['elapsed_seconds']}")
        prGreen(f"LLM 请求轮数：{metrics['llm_request_count']}")
        if metrics_path:
            prGreen(f"指标文件：{metrics_path}")
        prBlue("=" * 80 + "\n")


if __name__ == "__main__":
    sys.exit(main())
