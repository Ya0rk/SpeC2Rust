"""
集中管理所有 agent 使用的 prompt 模板
"""

from typing import List, Dict

# ============================================================================
# CDocAgent Prompts - C 项目文档生成
# ============================================================================

class CDocAgentPrompts:
    """CDocAgent 相关 prompt 模板"""
    
    @staticmethod
    def create_analysis_plan(project_name: str, files: list, functions_count: int, 
                            structs_count: int, readme_content: str = "") -> str:
        """制定项目分析计划并生成文档骨架的 prompt"""
        prompt = f"请为以下 C 项目制定一个详细的分析计划并生成项目文档骨架，目标是完全理解这个项目。\n\n"
        prompt += f"项目名称：{project_name}\n"
        prompt += f"项目文件数量：{len(files)}\n"
        prompt += f"函数数量：{functions_count}\n"
        prompt += f"结构体数量：{structs_count}\n"
        if readme_content:
            prompt += f"项目 README 内容：{readme_content}\n\n"
        prompt += f"文件列表：{', '.join(files)}\n\n"
        prompt += "请制定一个详细的分析计划，包括：\n"
        prompt += "1. 整体分析策略\n"
        prompt += "2. 分阶段分析步骤\n"
        prompt += "3. 重点关注的模块和功能\n"
        prompt += "4. 如何验证分析的完整性\n"
        prompt += "5. 每轮迭代的具体任务\n"
        prompt += '''使用<analysis_plan>标签包裹分析计划，例如：
        <analysis_plan>
        分析计划
        </analysis_plan>
        '''
        prompt += "同时，请生成项目文档骨架，包括以下内容的详细标题结构：\n"
        prompt += "1. 项目概述\n"
        prompt += "2. 项目功能\n"
        prompt += "3. 项目架构\n"
        prompt += "4. 模块关系\n"
        prompt += "5. 代码结构和关键组件\n"
        prompt += "6. 关键函数分析\n"
        prompt += "7. 数据结构分析\n"
        prompt += "8. 核心算法分析\n"
        prompt += '''使用<doc_skeleton>标签包裹文档骨架，例如：
        <doc_skeleton>
        文档骨架
        </doc_skeleton>
        '''
        prompt += "\n请为每个部分提供详细的子标题结构，后续轮次将基于此骨架进行内容完善。"
        return prompt
    
    @staticmethod
    def create_analysis_plan_system_prompt() -> str:
        """制定分析计划的系统 prompt"""
        sys_prompt = "你是一个 C 项目分析专家，擅长制定详细的项目分析计划和文档结构。"
        sys_prompt += "**注意事项：**"
        sys_prompt += "1. 分析计划必须详细，包括每个模块的分析任务和验证方法\n"
        sys_prompt += "2. 每轮迭代的任务必须基于上一轮的分析结果，不能独立进行\n"
        return sys_prompt
    
    @staticmethod
    def perform_iteration(project_name: str, current_analysis: str, 
                         analysis_plan: str, doc_skeleton: str, iteration: int) -> str:
        """执行迭代分析的 prompt"""
        prompt = f"请基于以下 C 项目的当前分析结果和文档骨架，进行第 {iteration} 轮迭代分析和文档完善。\n\n"
        prompt += f"项目名称：{project_name}\n\n"
        prompt += "当前分析结果:\n"
        prompt += current_analysis
        prompt += "\n"
        prompt += f"分析计划:\n{analysis_plan}\n\n"
        prompt += f"文档骨架:\n{doc_skeleton}\n\n"
        prompt += "请在本轮迭代中：\n"
        prompt += "1. 基于上一轮的分析结果进行深入分析\n"
        prompt += "2. 检查文档骨架的准确性，如有需要可以修复骨架中不准确的地方\n"
        prompt += "3. 为文档骨架中的各个部分填充详细内容\n"
        prompt += "4. 补充缺失的信息\n"
        prompt += "5. 修正错误的理解\n"
        prompt += "6. 完善文档的细节\n"
        prompt += "7. 不要将具体的代码细节写到分析报告中，而是使用代码定位方式，例如：'a.c [开始行：结束行]'\n"
        prompt += "8. 不需要生成项目的使用安装说明和代码风格质量说明，只需要生成项目的分析报告\n"
        prompt += "9. 不需要测试项目的性能，只需要分析项目的代码结构和功能\n"
        prompt += "10. 完全专注于正在研究的特定主题，不要偏离到相关主题\n"
        prompt += '''11. 如果修改了文档骨架，将新骨架包裹在</doc_skeleton>标签中，
        eg:
            <doc_skeleton>
            新的文档骨架内容
            </doc_skeleton>
        '''
        return prompt
    
    @staticmethod
    def perform_iteration_system_prompt() -> str:
        """迭代分析的系统 prompt"""
        return '你是一个 C 项目分析专家，擅长基于现有分析和文档骨架进行迭代完善。'
    
    @staticmethod
    def generate_final_document(project_name: str, doc_skeleton: str, 
                               all_analyses: str, iteration_history: str,
                               analysis_plan: str) -> str:
        """生成最终版文档的 prompt"""
        prompt = f"请基于以下 C 项目的文档骨架、所有分析结果和迭代历史，生成一个准确详细的最终版文档。\n\n"
        prompt += f"项目名称：{project_name}\n\n"
        prompt += f"分析计划:\n{analysis_plan}\n\n"
        prompt += f"文档骨架:\n{doc_skeleton}\n\n"
        prompt += "所有分析结果:\n"
        prompt += all_analyses
        prompt += "\n"
        prompt += "迭代历史:\n"
        prompt += iteration_history
        prompt += "\n"
        prompt += "不要将具体的代码细节写到分析报告中，而是使用代码定位方式，例如：'a.c [开始行：结束行]'\n"
        prompt += "请严格按照文档骨架的结构生成最终文档，同时整合所有分析结果和迭代历史中的信息。\n"
        prompt += "文档应该详细、准确，包含源代码位置信息，并严格按照分析计划执行的结果生成。\n"
        return prompt
    
    @staticmethod
    def generate_final_document_system_prompt() -> str:
        """生成最终文档的系统 prompt"""
        return '你是一个 C 项目分析专家，擅长基于文档骨架和分析结果生成详细准确的项目文档。'
    
    @staticmethod
    def generate_spec(project_name: str, branch_name: str, today: str,
                     files_info: str, functions_info: str, structs_info: str,
                     all_analyses: str) -> str:
        """生成 spec-kit spec 文档的 prompt"""
        return f"""请基于以下 C 项目分析结果，生成一个 spec-kit 格式的功能规格文档（spec.md），用于指导将该 C 项目重写为 Rust 版本。

项目名称：{project_name}
Rust 项目分支：{branch_name}
生成日期：{today}

## C 项目结构分析

### 文件列表
{files_info}

### 主要函数（前 20 个）
{functions_info}

### 主要数据结构（前 20 个）
{structs_info}

### 详细模块分析
{all_analyses}

请生成一个完整的 spec.md 文档，包含以下内容：

1. **Feature Specification**: 描述这个 C 项目的功能，以及 Rust 版本需要实现的功能
2. **User Scenarios & Testing**: 描述 C 项目的使用场景，Rust 版本需要支持这些场景
3. **Requirements**: 
   - Functional Requirements: C 项目实现的功能需求
   - Key Entities: C 项目中的关键数据结构和它们的关系
4. **Success Criteria**: Rust 版本需要达到的成功标准

**重要指导原则**：
- 专注于 C 项目的**功能**和**行为**，而不是实现细节
- 使用场景应该描述用户如何使用这个 C 项目
- 功能需求应该列出 C 项目提供的所有主要功能
- 关键实体应该描述 C 项目中的核心数据结构、它们之间的关系和用途
- 成功标准应该是可测量的，例如"能够处理相同的输入"、"产生相同的输出"等
- 使用 spec-kit 的 spec-template.md 格式
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用标准的 spec-kit spec 文档格式，包含所有必要的章节和标记，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_spec_system_prompt() -> str:
        """生成 spec 文档的系统 prompt"""
        return '你是一个 spec-kit 专家，擅长创建用于指导 C 到 Rust 项目转换的功能规格文档。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_plan(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 spec-kit plan 文档的 prompt"""
        return f"""请基于以下 C 项目分析结果和 spec 文档，生成一个 spec-kit 格式的实现计划文档（plan.md），用于指导将该 C 项目重写为 Rust 版本。

项目名称：{project_name}
Rust 项目分支：{branch_name}

## C 项目详细分析
{all_analyses}

请生成一个完整的 plan.md 文档，包含以下内容：

1. **Summary**: C 项目的主要功能和 Rust 实现的技术方法
2. **Technical Context**: 
   - Language/Version: Rust (指定版本)
   - Primary Dependencies: 推荐的 Rust crates
   - Storage: 如果 C 项目使用文件/数据库，Rust 版本对应的方案
   - Testing: cargo test
   - Target Platform: 与 C 项目相同的平台
   - Project Type: library/cli/application (根据 C 项目类型)
   - Performance Goals: 与 C 项目相当或更好的性能
   - Constraints: 内存安全、线程安全等 Rust 特有的约束
   - Scale/Scope: 与 C 项目相同的规模
3. **Project Structure**: Rust 项目的目录结构
4. **Implementation Phases**: 分阶段实现计划

**重要指导原则**：
- 技术选型应该考虑 Rust 的最佳实践和生态系统
- 项目结构应该符合 Rust 的标准约定（src/, tests/, Cargo.toml 等）
- 实现计划应该分阶段，从基础到复杂
- 考虑 C 到 Rust 的映射：C 的结构体→Rust 的 struct/enum，C 的函数→Rust 的函数等
- 特别注意内存管理、错误处理、并发模型的转换
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用 spec-kit 的 plan-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_plan_system_prompt() -> str:
        """生成 plan 文档的系统 prompt"""
        return '你是一个 Rust 架构师，擅长制定从 C 到 Rust 的详细实现计划。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_tasks(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 spec-kit tasks 文档的 prompt"""
        return f"""请基于以下 C 项目分析、spec 文档和 plan 文档，生成一个 spec-kit 格式的任务列表文档（tasks.md），用于指导将该 C 项目重写为 Rust 版本。

项目名称：{project_name}
Rust 项目分支：{branch_name}

## C 项目分析
{all_analyses}

请生成一个完整的 tasks.md 文档，包含以下任务阶段：

1. **Phase 1: Setup** - Rust 项目初始化
2. **Phase 2: Foundational** - 基础架构实现
3. **Phase 3-N: User Stories** - 按优先级实现各个功能模块
4. **Final Phase: Polish** - 优化和完善

**任务格式**：使用 `[ID] [P?] [Story] Description` 格式
- [P] 标记可以并行执行的任务
- [Story] 标记任务属于哪个用户场景
- 包含具体的文件路径

**重要指导原则**：
- 任务应该按照 spec 中的用户故事优先级组织
- 每个用户故事应该可以独立实现和测试
- 包含测试任务（如果 spec 中要求）
- 考虑 C 到 Rust 的转换顺序：先数据结构，再核心逻辑，最后接口
- 标记任务之间的依赖关系
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用 spec-kit 的 tasks-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_tasks_system_prompt() -> str:
        """生成 tasks 文档的系统 prompt"""
        return '你是一个 Rust 开发专家，擅长创建详细的 C 到 Rust 转换任务列表。输出必须统一使用简体中文。'
    
    # ============================================================================
    # 分层聚类方法相关 Prompts
    # ============================================================================
    
    @staticmethod
    def generate_cluster_summary(cluster_name: str, cluster_type: str, 
                                functions: List[Dict], structs: List[Dict],
                                files_content: List[Dict]) -> str:
        """生成函数簇摘要的 prompt"""
        files_info = ""
        for f in files_content:
            files_info += f"\n=== 文件：{f['path']} ===\n"
            files_info += f["content"][:1500]
        
        functions_list = ""
        for func in functions:
            if isinstance(func, dict):
                functions_list += (
                    f"- {func.get('name', 'unknown')}: "
                    f"{func.get('file', 'unknown')}:{func.get('start_line', func.get('startLine', '?'))}\n"
                )
        
        structs_list = ""
        for struct in structs:
            if isinstance(struct, dict):
                structs_list += f"- {struct.get('name', 'unknown')}\n"
        
        return f"""请分析以下 C 项目函数簇，生成简洁的摘要。

簇名称：{cluster_name}
簇类型：{cluster_type}
包含文件：{', '.join([f['path'] for f in files_content])}

## 函数列表
{functions_list}

## 相关结构体
{structs_list}

## 代码内容
{files_info}

请从以下几个方面生成摘要：
1. 这个函数簇的主要职责（一句话概括）
2. 核心功能和输入输出
3. 关键数据结构和它们的关系
4. 与外部的依赖关系
5. 潜在风险点或需要注意的行为

**重要**：摘要控制在 300 字以内，专注于功能职责，不要陷入实现细节。"""
    
    @staticmethod
    def generate_cluster_summary_system_prompt() -> str:
        """生成函数簇摘要的系统 prompt"""
        return '你是一个 C 代码分析专家，擅长从代码中提取功能职责和关键信息。'
    
    @staticmethod
    def generate_file_summary(file_path: str, cluster_summaries: List[Dict]) -> str:
        """生成文件摘要的 prompt"""
        clusters_info = ""
        for cs in cluster_summaries:
            clusters_info += f"\n### {cs['cluster_name']} 簇\n"
            clusters_info += cs['summary']
        
        return f"""请基于以下文件包含的函数簇摘要，生成文件级别的摘要。

文件路径：{file_path}

## 包含的函数簇摘要
{clusters_info}

请生成文件摘要，包括：
1. 文件的主要职责
2. 提供的公共接口
3. 依赖的外部模块
4. 在整体架构中的位置

**重要**：基于簇摘要进行汇总，不要引入新信息。"""
    
    @staticmethod
    def generate_file_summary_system_prompt() -> str:
        """生成文件摘要的系统 prompt"""
        return '你是一个 C 架构分析专家，擅长从局部信息汇总出模块职责。'
    
    @staticmethod
    def generate_module_summary(module_name: str, module_category: str, 
                               files: List[str], functions: List[Dict], 
                               structs: List[Dict], file_summaries: List[Dict],
                               cohesion_score: float, internal_calls: int, 
                               external_calls: int) -> str:
        """生成模块摘要的 prompt"""
        files_info = "\n".join(files[:20])
        
        functions_list = ""
        for func in functions[:30]:
            if isinstance(func, dict):
                functions_list += f"- {func.get('name', 'unknown')}\n"
        
        structs_list = ""
        for struct in structs[:20]:
            if isinstance(struct, dict):
                structs_list += f"- {struct.get('name', 'unknown')}\n"
        
        file_summaries_text = ""
        for fs in file_summaries[:10]:
            file_summaries_text += f"\n{fs}\n"
        
        return f"""请基于以下信息，生成 C 项目模块的详细摘要。

模块名称：{module_name}
模块类别：{module_category}
内聚度分数：{cohesion_score:.2f}
内部调用：{internal_calls}
外部调用：{external_calls}

## 包含文件
{files_info}

## 主要函数
{functions_list}

## 核心数据结构
{structs_list}

## 文件摘要
{file_summaries_text}

请从以下几个方面生成模块摘要：
1. 模块职责（能用一句话说明白）
2. 输入和输出（清晰的接口边界）
3. 核心接口列表
4. 依赖哪些其他模块
5. 必须保留的关键行为
6. 如果模块过大，指出可以进一步拆分的点

**重要**：
- 职责必须能用一句话说清楚
- 输入输出必须明确
- 核心接口必须列出
- 依赖关系必须清晰
- 如果做不到以上几点，说明模块划分不合理，需要继续拆分"""
    
    @staticmethod
    def generate_module_summary_system_prompt() -> str:
        """生成模块摘要的系统 prompt"""
        return '你是一个 C 模块化设计专家，擅长识别高内聚低耦合的模块边界。'
    
    @staticmethod
    def generate_module_spec(project_name: str, module_name: str, 
                            module_category: str, branch_name: str, 
                            today: str, files: List[str],
                            functions_info: str, structs_info: str) -> str:
        """为单个模块生成 spec 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""请基于以下 C 项目模块分析结果，生成一个 spec-kit 格式的功能规格文档 (spec.md)，用于指导将该模块重写为 Rust 版本。

项目名称：{project_name}
模块名称：{module_name}
模块类别：{module_category}
Rust 项目分支：{branch_name}
生成日期：{today}

## 模块文件
{files_list}

## 主要函数
{functions_info}

## 核心数据结构
{structs_info}

请生成一个完整的 spec.md 文档，包含以下内容：

1. **Feature Specification**: 描述这个模块的功能，以及 Rust 版本需要实现的功能
2. **User Scenarios & Testing**: 描述模块的使用场景，Rust 版本需要支持这些场景
3. **Requirements**: 
   - Functional Requirements: 模块实现的功能需求
   - Key Entities: 模块中的关键数据结构和它们的关系
4. **Success Criteria**: Rust 版本需要达到的成功标准

**重要指导原则**：
- 专注于模块的**功能**和**行为**，而不是实现细节
- 使用场景应该描述如何使用这个模块
- 功能需求应该列出模块提供的所有主要功能
- 关键实体应该描述模块中的核心数据结构
- 成功标准应该是可测量的
- 使用 spec-kit 的 spec-template.md 格式
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用标准的 spec-kit spec 文档格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_module_spec_system_prompt() -> str:
        """生成模块 spec 文档的系统 prompt"""
        return '你是一个 spec-kit 专家，擅长为单个 C 模块创建功能规格文档。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_module_plan(project_name: str, module_name: str,
                            module_category: str, branch_name: str,
                            files: List[str], functions: List[Dict], 
                            structs: List[Dict]) -> str:
        """为单个模块生成 plan 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        functions_list = ""
        for func in functions[:20]:
            if isinstance(func, dict):
                functions_list += f"- {func.get('name', 'unknown')}\n"
        
        structs_list = ""
        for struct in structs[:20]:
            if isinstance(struct, dict):
                structs_list += f"- {struct.get('name', 'unknown')}\n"
        
        return f"""请基于以下 C 模块分析结果，生成一个 spec-kit 格式的实现计划文档 (plan.md)。

项目名称：{project_name}
模块名称：{module_name}
模块类别：{module_category}
Rust 项目分支：{branch_name}

## 模块文件
{files_list}

## 函数列表
{functions_list}

## 数据结构
{structs_list}

请生成一个完整的 plan.md 文档，包含：

1. **Summary**: 模块的主要功能和 Rust 实现的技术方法
2. **Technical Context**: 
   - Language/Version: Rust (指定版本)
   - Primary Dependencies: 推荐的 Rust crates
   - Testing: cargo test
   - Performance Goals: 性能目标
3. **Module Mapping**: C 模块到 Rust 模块的映射
4. **Data Model**: 数据结构映射（C struct → Rust struct/enum）
5. **Implementation Phases**: 分阶段实现计划

**重要指导原则**：
- 技术选型考虑 Rust 最佳实践
- 项目结构符合 Rust 标准约定
- 考虑 C 到 Rust 的映射
- 特别注意内存管理、错误处理
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用 spec-kit 的 plan-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_module_plan_system_prompt() -> str:
        """生成模块 plan 文档的系统 prompt"""
        return '你是一个 Rust 架构师，擅长为单个 C 模块制定 Rust 实现计划。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_module_tasks(project_name: str, module_name: str,
                             module_category: str, branch_name: str,
                             files: List[str], functions: List[Dict], 
                             structs: List[Dict]) -> str:
        """为单个模块生成 tasks 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""请基于以下 C 模块分析，生成一个 spec-kit 格式的任务列表文档 (tasks.md)。

项目名称：{project_name}
模块名称：{module_name}
模块类别：{module_category}
Rust 项目分支：{branch_name}

## 模块文件
{files_list}

## 函数
{len(functions)} 个

## 数据结构
{len(structs)} 个

请生成一个完整的 tasks.md 文档，包含以下任务阶段：

1. **Phase 1: Setup** - Rust 项目初始化
2. **Phase 2: Foundational** - 基础数据结构实现
3. **Phase 3-N: Functions** - 按功能分组实现各个函数
4. **Final Phase: Polish** - 优化和完善

**任务格式**：使用 `[ID] [P?] [Story] Description` 格式
- 包含具体的文件路径
- 标记任务依赖关系

**重要指导原则**：
- 先实现数据结构，再实现函数
- 相关函数分组实现
- 包含测试任务
- 标记可并行的任务
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用 spec-kit 的 tasks-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_module_tasks_system_prompt() -> str:
        """生成模块 tasks 文档的系统 prompt"""
        return '你是一个 Rust 开发专家，擅长为单个 C 模块创建详细任务列表。输出必须统一使用简体中文。'


# ============================================================================
# CodeFixer Agent Prompts - Rust 代码修复
# ============================================================================

class CodeFixerPrompts:
    """CodeFixer 相关 prompt 模板"""
    
    @staticmethod
    def generate_fix_prompt(error_type: str, error_message: str, file_content: str = "") -> str:
        """生成代码修复提示"""
        prompt = f'''你是一个 Rust 代码修复专家。请修复以下错误：

错误类型：{error_type}
错误信息：
{error_message}
'''
        
        if file_content:
            prompt += f'''
当前代码内容：
```rust
{file_content}
```

'''
        
        prompt += '''请提供修复后的完整代码，只返回代码，不要解释。
将返回的代码包裹在 ```rust ``` 的 markdown 代码块中。'''
        
        return prompt
    
    @staticmethod
    def system_prompt() -> str:
        """系统 prompt"""
        return '你是一个 Rust 代码修复专家，擅长根据错误信息修复代码。'


# ============================================================================
# TestFixer Agent Prompts - Rust 测试修复
# ============================================================================

class TestFixerPrompts:
    """TestFixer 相关 prompt 模板"""
    
    @staticmethod
    def generate_fix_prompt(test_error: str, test_name: str, file_content: str = "") -> str:
        """生成测试修复提示"""
        prompt = f'''你是一个 Rust 测试修复专家。请修复以下测试失败的问题：

测试名称：{test_name}

测试错误信息：
{test_error}
'''

        if file_content:
            prompt += f'''
相关代码内容：
```rust
{file_content}
```

'''
        
        prompt += '''请分析测试失败的原因，并修复代码中的逻辑和算法错误。
只返回修复后的完整代码，不要解释。
将返回的代码包裹在 ```rust ``` 的 markdown 代码块中。'''
        return prompt
    
    @staticmethod
    def system_prompt() -> str:
        """系统 prompt"""
        return '你是一个 Rust 测试修复专家，擅长分析测试失败原因并修复代码逻辑错误。'


# ============================================================================
# RustAgent Prompts - Rust 代码生成
# ============================================================================

class RustAgentPrompts:
    """RustAgent 相关 prompt 模板"""
    
    @staticmethod
    def generate_project_structure_prompt(project_name: str, all_docs: str) -> str:
        """生成项目结构设计的 prompt"""
        return f"""请根据以下项目文档，设计一个地道的 Rust 项目结构。

{all_docs}

请设计一个符合 Rust 最佳实践的项目结构，包括：
1. 项目名称：{project_name}
2. 项目目录文件结构（**重要**：必须使用 tree 命令格式展示，并严格使用<project_file>标签包裹）
例如：
<project_file>
{project_name}/
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── avl_tree.rs
│   ├── avl_node.rs
│   └── traits.rs
├── tests/
│   └── avl_tree_test.rs
└── README.md
</project_file>
3. 主要模块划分
4. 核心数据结构和 trait 设计
5. 关键函数和方法签名
6. 错误处理策略

额外要求：
- 如果上下文中已经提供了 C 源码片段、函数体或接口事实，这些源码事实优先于摘要性描述
- 不要凭空创造原 C 项目中不存在的核心模块、指令集、状态机或协议
- 如果原项目明显是工具/CLI/可执行程序，Rust 项目结构必须保留对应的入口与对外使用方式，不要擅自改成纯库项目
"""
    
    @staticmethod
    def generate_project_structure_system_prompt() -> str:
        """生成项目结构设计的系统 prompt"""
        return """你是一个 Rust 架构设计专家，擅长根据需求文档设计地道的 Rust 项目结构。
        
设计原则：
1. 遵循 Rust 惯用法和最佳实践
2. 合理使用所有权和借用，避免二次可变借用和不可变借用冲突
3. 合理使用 trait 进行抽象，也可以使用 struct 进行实现，trait 不是必须的，根据实际情况选择
4. 完善的错误处理
5. 清晰的模块划分
"""
    
    @staticmethod
    def generate_implementation_plan_prompt(project_structure: str, files_to_generate: list) -> str:
        """生成实现计划的 prompt"""
        return f"""基于以下 Rust 项目结构设计，制定详细的实现计划。

{project_structure}

需要生成的文件列表：
{files_to_generate}

请制定一个分步骤的实现计划，包括：
1. 第一步：创建基础数据结构和 trait
2. 第二步：设计各个函数模块之间的依赖关系，制定自底向上的函数生成计划，确保每个函数在其依赖的函数之后生成，生成新的文件列表顺序（**注意**：将新的文件列表顺序保存到<new_files_to_generate>标签中）
3. 第三步：实现核心功能
4. 第四步：实现辅助功能
5. 第五步：实现错误处理

对于每个步骤，请详细说明：
- 需要创建的文件
- 需要实现的函数/方法
- 关键算法和实现要点
- 减少 unsafe 的使用

约束：
- 如果上下文中提供了 C 源码函数体、源码片段或接口事实，计划必须以这些事实为准
- 对工具类项目，必须明确保留命令行入口、参数语义、输出行为和退出方式的迁移方案
- 不要把只有源码位置但未给出实现依据的部分擅自扩写成复杂新设计

请使用<implementation_plan>标签包裹实现计划。"""
    
    @staticmethod
    def generate_implementation_plan_system_prompt() -> str:
        """生成实现计划的系统 prompt"""
        return """你是一个 Rust 实现专家，擅长制定详细的代码实现计划。
        
实现原则：
1. 由简到繁，分析需要的生成函数依赖关系，自底向上，逐步实现
2. 减少 unsafe 使用
3. 优先使用 safe 的 Rust 标准库
4. 遵循 Rust 编码规范"""
    
    @staticmethod
    def generate_code_prompt(file_path: str, context: str, implementation_plan: str) -> str:
        """生成代码的 prompt"""
        return f"""请为 Rust 项目生成以下文件的代码实现。

文件路径：{file_path}

项目上下文：
{context}

实现计划：
{implementation_plan}

请生成地道、规范的 Rust 代码，要求：
1. 使用地道的 Rust 惯用法
2. 完善的错误处理（使用 Result 和 Option)
3. 合理的类型设计
4. 所有权和借用清晰，避免二次可变借用和不可变借用冲突
5. 遵循 Rust 编码规范（使用 rustfmt 风格）
6. 代码简洁、可读性好
7. Cargo.toml 不要使用 // 注释，而要使用 # 注释
8. 减少 unsafe 的使用
9. 注意算法的合理正确性，避免逻辑错误
10. 如果上下文中已经提供了 C 源码片段、函数体、宏、全局变量或接口事实，必须优先按照这些源码事实实现，不要自行脑补
11. 如果原项目是工具/CLI，必须保持对外使用接口一致，不要擅自改变参数形式、入口方式、输出通道或退出语义
12. 如果当前 Rust 文件与某个 C 函数/模块明显对应，应尽量贴着对应源码迁移，而不是只根据模块摘要重写成另一套逻辑

请直接输出代码内容，不要包含其他说明文字。使用```rust 代码块包裹代码。"""
    
    @staticmethod
    def generate_code_system_prompt() -> str:
        """生成代码的系统 prompt"""
        return """你是一个 Rust 编程专家，擅长生成地道、规范的 Rust 代码。
        
代码风格：
1. 使用 Rust 惯用法
2. 清晰的命名
3. 合理的抽象
4. 高效的实现"""


# ============================================================================
# SpecAgent Prompts - Spec 文档生成
# ============================================================================

class SpecAgentPrompts:
    """SpecAgent 相关 prompt 模板"""
    
    @staticmethod
    def generate_repo_manifest(project_info: Dict) -> str:
        """生成 repo_manifest 文档的 prompt"""
        return f"""请根据以下 C 项目信息，生成一个详细的仓库地图文档 (00_repo_manifest.md)。

项目名称：{project_info['project_name']}
C 文件数量：{len(project_info['c_files'])}
头文件数量：{len(project_info['h_files'])}
其他文件数量：{len(project_info['other_files'])}
构建系统：{project_info['build_system']}
可执行文件：{', '.join(project_info['executables']) if project_info['executables'] else '无'}
库文件：{', '.join(project_info['libraries']) if project_info['libraries'] else '无'}

## C 文件列表
{chr(10).join(project_info['c_files'][:50])}

## 头文件列表
{chr(10).join(project_info['h_files'][:50])}

## README 内容
{project_info['readme_content'][:2000] if project_info['readme_content'] else '无 README 文件'}

请生成一个详细的仓库地图文档，包括：
1. 项目概述
2. 目录结构分析
3. 核心源文件列表及其职责
4. 头文件组织
5. 构建系统说明
6. 可执行程序和库
7. 后续需要重点分析的子目录

**重要约束**：
1. 只能使用输入中明确给出的事实，不得补写未出现的目录树、头文件、可执行文件或库文件。
2. 如果某项信息缺失，直接写“未在当前输入中观察到”，不要使用“假设”“推测”“可能存在”。
3. 文件职责只能基于文件名和 README 摘要做保守描述，避免臆造实现细节。
4. 该文档是后续 Rust 迁移的导航页，优先保留可追溯的文件路径和目录信息。"""
    
    @staticmethod
    def generate_repo_manifest_system_prompt() -> str:
        """生成 repo_manifest 文档的系统 prompt"""
        return '你是一个严谨的 C 项目架构分析专家。只记录输入中明确存在的仓库事实，禁止虚构目录、头文件、产物和职责。'
    
    @staticmethod
    def identify_subsystems(project_info: Dict, file_count: int, 
                           functions_count: int, structs_count: int) -> str:
        """识别子系统的 prompt"""
        return f"""请分析以下 C 项目，识别出主要的子系统/模块。

项目名称：{project_info['project_name']}
文件数量：{file_count}
函数数量：{functions_count}
结构体数量：{structs_count}

## 文件列表
C 文件：{', '.join(project_info['c_files'][:30])}
头文件：{', '.join(project_info['h_files'][:30])}

请识别项目中的主要子系统/模块，并以 JSON 数组格式返回，每个子系统包含：
- name: 子系统名称
- description: 子系统职责描述
- files: 该子系统包含的文件列表

例如：
```json
[
  {{
    "name": "parser",
    "description": "解析器模块，负责解析输入文件",
    "files": ["src/parser.c", "src/parser.h", "src/lexer.c"]
  }},
  {{
    "name": "utils",
    "description": "工具函数库",
    "files": ["src/utils.c", "src/utils.h"]
  }}
]
```

**重要**：只返回 JSON 数组，不要其他说明文字。"""
    
    @staticmethod
    def identify_subsystems_system_prompt() -> str:
        """识别子系统的系统 prompt"""
        return '你是一个 C 项目模块化分析专家，擅长识别项目中的功能模块和子系统。'
    
    @staticmethod
    def generate_subsystem_doc(subsystem_name: str, subsystem_description: str, 
                              files: List[Dict]) -> str:
        """生成子系统文档的 prompt"""
        files_content = ""
        for f in files[:10]:  # 限制文件数量
            files_content += f"\n=== 文件：{f['path']} ===\n"
            files_content += f["content"][:3000]  # 限制每个文件长度
        
        return f"""请分析以下 C 项目子系统，生成详细的子系统说明文档。

子系统名称：{subsystem_name}
子系统职责：{subsystem_description}

## 包含的文件
{chr(10).join([f['path'] for f in files])}

## 文件内容
{files_content}

请从以下几个方面详细分析这个子系统：
1. 子系统的主要功能和职责
2. 关键数据结构（struct, enum, typedef 等）
3. 对外暴露的接口（公共函数）
4. 内部实现细节
5. 与其他子系统的依赖关系
6. 重要算法和实现策略

**重要**：为每个函数和数据结构添加源代码位置信息，格式为：[文件路径：行号]。"""
    
    @staticmethod
    def generate_subsystem_doc_system_prompt() -> str:
        """生成子系统文档的系统 prompt"""
        return '你是一个 C 子系统分析专家，擅长深入分析模块的功能、接口和实现细节。'
    
    @staticmethod
    def generate_interfaces_doc(public_headers: List[str], functions: List[Dict], 
                               structs: List[Dict]) -> str:
        """生成接口文档的 prompt"""
        functions_list = ""
        for func in functions[:30]:
            functions_list += (
                f"- {func.get('name', 'unknown')}: "
                f"{func.get('file', 'unknown')}:{func.get('start_line', func.get('startLine', '?'))}\n"
            )
        
        structs_list = ""
        for struct in structs[:30]:
            structs_list += (
                f"- {struct.get('name', 'unknown')}: "
                f"{struct.get('file', struct.get('filename', 'unknown'))}:"
                f"{struct.get('start_line', struct.get('startLine', '?'))}\n"
            )
        
        return f"""请根据以下 C 项目的接口信息，生成详细的接口事实文档 (02_interfaces)。

## 公共头文件
{chr(10).join(public_headers[:20])}

## 主要函数（前{len(functions)}个）
{functions_list}

## 主要数据结构（前{len(structs)}个）
{structs_list}

请生成一个详细的接口文档，包括：
1. 公共头文件组织
2. 导出函数列表（函数签名、参数说明、返回值说明）
3. 重要结构体和类型定义
4. 宏定义和常量
5. 错误码定义
6. 输入输出格式规范
7. 配置项说明

**重要约束**：
1. 只能使用输入中明确给出的头文件、函数、结构体和位置信息。
2. 严禁编造 `xxx.h`、错误码、宏、配置项、结构体、函数签名或返回值语义。
3. 如果没有观察到某一类信息，明确写“未在当前分析结果中发现”，不要写“假设存在”“可以推测”。
4. 所有函数和结构体条目都要保留源码位置；如果签名不完整，只能标注“定义签名待回查源码”，不能自行补全。
5. 该文档面向 Rust 重写，优先输出可追溯事实，而不是泛化描述。"""
    
    @staticmethod
    def generate_interfaces_doc_system_prompt() -> str:
        """生成接口文档的系统 prompt"""
        return '你是一个严格的 C 接口事实整理专家。缺失信息必须标成缺失，禁止假设任何头文件、签名、宏、错误码或配置项。'
    
    @staticmethod
    def generate_behaviors_doc(project_name: str, all_analyses: str) -> str:
        """生成行为文档的 prompt"""
        return f"""请根据以下 C 项目 {project_name} 的模块分析结果，生成详细的行为说明文档 (03_behaviors)。

## 模块分析结果
{all_analyses}

请从以下几个方面生成行为说明文档：
1. 初始化流程和启动顺序
2. 主要用户操作流程
3. 状态机和状态转换
4. 错误处理流程
5. 边界条件和特殊情况处理
6. 与 C 版本必须保持一致的行为
7. 性能敏感路径

**重要**：这里记录的是"动态行为"，而不是"静态接口"。要说明程序实际如何运行，状态如何变化。
额外约束：
- 只能基于输入中的模块分析结果描述行为，不得补写未观察到的分配策略、错误码语义或返回约定。
- 如果证据不足，明确写“当前模块摘要不足以支持更细行为判断”，不要使用“可能”“推测”“大概”。
- 不要把“没有调用关系”解释成“没有功能”或“空实现”。"""
    
    @staticmethod
    def generate_behaviors_doc_system_prompt() -> str:
        """生成行为文档的系统 prompt"""
        return '你是一个 C 程序行为分析专家，擅长分析程序的动态行为和运行流程。'

    @staticmethod
    def generate_behaviors_batch_summary(
        project_name: str,
        batch_index: int,
        total_batches: int,
        batch_analyses: str,
    ) -> str:
        """生成行为文档批次摘要的 prompt"""
        return f"""请根据以下 C 项目 {project_name} 的部分模块分析结果，生成一份行为摘要。

当前批次：{batch_index}/{total_batches}

## 模块分析结果
{batch_analyses}

请输出一份适合后续总汇总的行为摘要，重点保留：
1. 初始化和启动顺序
2. 模块之间的主要交互
3. 状态变化和关键状态机
4. 错误处理和边界条件
5. 后续 Rust 重写必须保持一致的行为约束

要求：
- 只保留高价值行为事实，不要重复静态接口列表
- 不确定的地方明确标注“待确认”
- 输出使用清晰的 Markdown 小节和要点
- 不要把低内聚度、低调用计数或信息缺失误写成“空实现”“无功能”或“设计错误”"""

    @staticmethod
    def generate_behaviors_batch_summary_system_prompt() -> str:
        """生成行为文档批次摘要的系统 prompt"""
        return '你是一个程序行为归纳专家，擅长把大规模模块分析压缩成可汇总的行为事实。'

    @staticmethod
    def generate_behaviors_final_doc(project_name: str, batch_summaries: str) -> str:
        """基于批次摘要生成最终行为文档的 prompt"""
        return f"""请根据以下 C 项目 {project_name} 的分批行为摘要，生成最终的行为说明文档 (03_behaviors)。

## 批次行为摘要
{batch_summaries}

请生成一个完整且可用于 Rust 重写的行为文档，必须覆盖：
1. 初始化流程和启动顺序
2. 核心运行流程
3. 状态机与状态转换
4. 错误处理和恢复路径
5. 边界条件和特殊分支
6. 必须与 C 版本保持一致的外部可观察行为
7. 性能敏感路径

要求：
- 合并重复内容，避免批次之间互相抄写
- 以“行为等价”为目标组织文档
- 明确标注存在不确定性的行为点
- 如果没有充分证据，使用“待源码确认”，不要补写猜测性行为结论"""

    @staticmethod
    def generate_behaviors_final_doc_system_prompt() -> str:
        """生成最终行为文档的系统 prompt"""
        return '你是一个程序行为建模专家，擅长把多批次分析结果整合成统一、可执行的行为规范。'
    
    @staticmethod
    def generate_gaps_and_risks(project_name: str, all_analyses: str) -> str:
        """生成不确定点和风险文档的 prompt"""
        return f"""请根据以下 C 项目 {project_name} 的分析结果，生成不确定点和风险文档 (04_gaps_and_risks)。

## 模块分析结果
{all_analyses}

请识别并列出：
1. 不确定点：代码中行为不明确、需要人工确认的地方
2. 潜在风险：Rust 迁移时可能遇到的问题
3. 需要进一步调查的模块
4. 可能存在但未明确说明的隐式行为
5. 全局变量和副作用
6. 未文档化的边界条件

**重要**：这是为了保证 Rust 迁移时不会遗漏重要细节，需要尽可能全面。"""
    
    @staticmethod
    def generate_gaps_and_risks_system_prompt() -> str:
        """生成不确定点和风险文档的系统 prompt"""
        return '你是一个风险评估专家，擅长识别 C 到 Rust 迁移中的不确定因素和潜在风险。'
    
    @staticmethod
    def generate_constitution(
        project_name: str,
        project_context: str = "",
        interface_summary: str = "",
        behavior_summary: str = "",
    ) -> str:
        """生成 constitution 文档的 prompt"""
        return f"""请为 C 项目 {project_name} 的 Rust 迁移生成项目级原则文档 (constitution.md)。

这个文档将规定整个迁移工程必须遵守的核心原则。

## 项目概况
{project_context}

## 接口摘要
{interface_summary}

## 行为摘要
{behavior_summary}

请生成一个 constitution.md 文档，包括以下章节：

1. **Core Principles** (核心原则)
   - 行为等价性原则
   - 接口兼容优先原则
   - 安全优先原则
   - 性能约束原则

2. **Migration Guidelines** (迁移指导)
   - C 到 Rust 的映射规则
   - 不确定行为处理原则
   - 测试验证要求

3. **Quality Gates** (质量关卡)
   - 必须通过的测试
   - 代码审查标准
   - 性能基准要求

**重要**：这是整个迁移工程的"法律"，后续的 spec、plan、tasks 都必须遵守这些原则。"""
    
    @staticmethod
    def generate_constitution_system_prompt() -> str:
        """生成 constitution 文档的系统 prompt"""
        return '你是一个软件工程原则制定专家，擅长为复杂迁移项目制定指导原则和质量标准。'
    
    @staticmethod
    def generate_spec(project_name: str, branch_name: str, today: str,
                     files_info: str, functions_info: str, structs_info: str,
                     all_analyses: str) -> str:
        """生成 spec 文档的 prompt"""
        return f"""请基于以下 C 项目分析结果，生成一个 spec-kit 格式的功能规格文档 (spec.md)，用于指导将该 C 项目重写为 Rust 版本。

项目名称：{project_name}
Rust 项目分支：{branch_name}
生成日期：{today}

## C 项目结构分析

### 文件列表
{files_info}

### 主要函数
{functions_info}

### 主要数据结构
{structs_info}

### 详细模块分析
{all_analyses}

请生成一个完整的 spec.md 文档，包含以下内容：

1. **Feature Specification**: 描述这个 C 项目的功能，以及 Rust 版本需要实现的功能
2. **User Scenarios & Testing**: 描述 C 项目的使用场景，Rust 版本需要支持这些场景
3. **Requirements**: 
   - Functional Requirements: C 项目实现的功能需求
   - Key Entities: C 项目中的关键数据结构和它们的关系
4. **Success Criteria**: Rust 版本需要达到的成功标准

**重要指导原则**：
- 专注于 C 项目的**功能**和**行为**，而不是实现细节
- 使用场景应该描述用户如何使用这个 C 项目
- 功能需求应该列出 C 项目提供的所有主要功能
- 关键实体应该描述 C 项目中的核心数据结构、它们之间的关系和用途
- 成功标准应该是可测量的，例如"能够处理相同的输入"、"产生相同的输出"等
- 使用 spec-kit 的 spec-template.md 格式
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用标准的 spec-kit spec 文档格式，包含所有必要的章节和标记，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_spec_system_prompt() -> str:
        """生成 spec 文档的系统 prompt"""
        return '你是一个 spec-kit 专家，擅长创建用于指导 C 到 Rust 项目转换的功能规格文档。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_plan(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 plan 文档的 prompt"""
        return f"""请基于以下 C 项目分析结果和 spec 文档，生成一个 spec-kit 格式的实现计划文档 (plan.md)，用于指导将该 C 项目重写为 Rust 版本。

项目名称：{project_name}
Rust 项目分支：{branch_name}

## C 项目详细分析
{all_analyses}

请生成一个完整的 plan.md 文档，包含以下内容：

1. **Summary**: C 项目的主要功能和 Rust 实现的技术方法
2. **Technical Context**: 
   - Language/Version: Rust (指定版本)
   - Primary Dependencies: 推荐的 Rust crates
   - Storage: 如果 C 项目使用文件/数据库，Rust 版本对应的方案
   - Testing: cargo test
   - Target Platform: 与 C 项目相同的平台
   - Project Type: library/cli/application (根据 C 项目类型)
   - Performance Goals: 与 C 项目相当或更好的性能
   - Constraints: 内存安全、线程安全等 Rust 特有的约束
   - Scale/Scope: 与 C 项目相同的规模
3. **Project Structure**: Rust 项目的目录结构
4. **Implementation Phases**: 分阶段实现计划

**重要指导原则**：
- 技术选型应该考虑 Rust 的最佳实践和生态系统
- 项目结构应该符合 Rust 的标准约定（src/, tests/, Cargo.toml 等）
- 实现计划应该分阶段，从基础到复杂
- 考虑 C 到 Rust 的映射：C 的结构体→Rust 的 struct/enum，C 的函数→Rust 的函数等
- 特别注意内存管理、错误处理、并发模型的转换
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用 spec-kit 的 plan-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_plan_system_prompt() -> str:
        """生成 plan 文档的系统 prompt"""
        return '你是一个 Rust 架构师，擅长制定从 C 到 Rust 的详细实现计划。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_tasks(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 tasks 文档的 prompt"""
        return f"""请基于以下 C 项目分析、spec 文档和 plan 文档，生成一个 spec-kit 格式的任务列表文档 (tasks.md)，用于指导将该 C 项目重写为 Rust 版本。

项目名称：{project_name}
Rust 项目分支：{branch_name}

## C 项目分析
{all_analyses}

请生成一个完整的 tasks.md 文档，包含以下任务阶段：

1. **Phase 1: Setup** - Rust 项目初始化
2. **Phase 2: Foundational** - 基础架构实现
3. **Phase 3-N: User Stories** - 按优先级实现各个功能模块
4. **Final Phase: Polish** - 优化和完善

**任务格式**：使用 `[ID] [P?] [Story] Description` 格式
- [P] 标记可以并行执行的任务
- [Story] 标记任务属于哪个用户场景
- 包含具体的文件路径

**重要指导原则**：
- 任务应该按照 spec 中的用户故事优先级组织
- 每个用户故事应该可以独立实现和测试
- 包含测试任务（如果 spec 中要求）
- 考虑 C 到 Rust 的转换顺序：先数据结构，再核心逻辑，最后接口
- 标记任务之间的依赖关系
- 标题、正文、说明全部使用简体中文；不要输出英文章节名

**输出格式**：使用 spec-kit 的 tasks-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_tasks_system_prompt() -> str:
        """生成 tasks 文档的系统 prompt"""
        return '你是一个 Rust 开发专家，擅长创建详细的 C 到 Rust 转换任务列表。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_module_summary(module_name: str, module_category: str, 
                               files: List[str], functions: List[Dict], 
                               structs: List[Dict], file_summaries: List[Dict],
                               cohesion_score: float, internal_calls: int, 
                               external_calls: int) -> str:
        """生成模块摘要的 prompt"""
        files_info = "\n".join(files[:20])
        
        functions_list = ""
        for func in functions[:30]:
            if isinstance(func, dict):
                functions_list += f"- {func.get('name', 'unknown')}\n"
        
        structs_list = ""
        for struct in structs[:20]:
            if isinstance(struct, dict):
                structs_list += f"- {struct.get('name', 'unknown')}\n"
        
        file_summaries_text = ""
        for fs in file_summaries[:10]:
            file_summaries_text += f"\n{fs}\n"
        
        return f"""请基于以下信息，生成 C 项目模块的详细摘要。

模块名称：{module_name}
模块类别：{module_category}
内聚度分数：{cohesion_score:.2f}
内部调用：{internal_calls}
外部调用：{external_calls}

## 包含文件
{files_info}

## 主要函数
{functions_list}

## 核心数据结构
{structs_list}

## 文件摘要
{file_summaries_text}

请从以下几个方面生成模块摘要：
1. 模块职责（能用一句话说明白）
2. 输入和输出（清晰的接口边界）
3. 核心接口列表
4. 依赖哪些其他模块
5. 必须保留的关键行为
6. 如果模块过大，指出可以进一步拆分的点

**重要**：
- 职责必须能用一句话说清楚
- 输入输出必须明确
- 核心接口必须列出
- 依赖关系必须清晰
- 如果做不到以上几点，说明模块划分不合理，需要继续拆分"""
    
    @staticmethod
    def generate_module_summary_system_prompt() -> str:
        """生成模块摘要的系统 prompt"""
        return '你是一个 C 模块化设计专家，擅长识别高内聚低耦合的模块边界。'
    
    @staticmethod
    def generate_module_spec(project_name: str, module_name: str, 
                            module_category: str, branch_name: str, 
                            today: str, files: List[str],
                            functions_info: str, structs_info: str) -> str:
        """为单个模块生成 spec 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""请基于以下 C 项目模块分析结果，生成一个 spec-kit 格式的功能规格文档 (spec.md)，用于指导将该模块重写为 Rust 版本。

项目名称：{project_name}
模块名称：{module_name}
模块类别：{module_category}
Rust 项目分支：{branch_name}
生成日期：{today}

## 模块文件
{files_list}

## 主要函数
{functions_info}

## 核心数据结构
{structs_info}

请生成一个完整的 spec.md 文档，包含以下内容：

1. **Feature Specification**: 描述这个模块的功能，以及 Rust 版本需要实现的功能
2. **User Scenarios & Testing**: 描述模块的使用场景，Rust 版本需要支持这些场景
3. **Requirements**: 
   - Functional Requirements: 模块实现的功能需求
   - Key Entities: 模块中的关键数据结构和它们的关系
4. **Success Criteria**: Rust 版本需要达到的成功标准

**重要指导原则**：
- 专注于模块的**功能**和**行为**，而不是实现细节
- 使用场景应该描述如何使用这个模块
- 功能需求应该列出模块提供的所有主要功能
- 关键实体应该描述模块中的核心数据结构
- 成功标准应该是可测量的
- 使用 spec-kit 的 spec-template.md 格式

**输出格式**：使用标准的 spec-kit spec 文档格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_module_spec_system_prompt() -> str:
        """生成模块 spec 文档的系统 prompt"""
        return '你是一个 spec-kit 专家，擅长为单个 C 模块创建功能规格文档。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_module_plan(project_name: str, module_name: str,
                            module_category: str, branch_name: str,
                            files: List[str], functions: List[Dict], 
                            structs: List[Dict]) -> str:
        """为单个模块生成 plan 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        functions_list = ""
        for func in functions[:20]:
            if isinstance(func, dict):
                functions_list += f"- {func.get('name', 'unknown')}\n"
        
        structs_list = ""
        for struct in structs[:20]:
            if isinstance(struct, dict):
                structs_list += f"- {struct.get('name', 'unknown')}\n"
            elif isinstance(struct, str):
                structs_list += f"- {struct}\n"
        
        return f"""请基于以下 C 模块分析结果，生成一个 spec-kit 格式的实现计划文档 (plan.md)。

项目名称：{project_name}
模块名称：{module_name}
模块类别：{module_category}
Rust 项目分支：{branch_name}

## 模块文件
{files_list}

## 函数列表
{functions_list}

## 数据结构
{structs_list}

请生成一个完整的 plan.md 文档，包含：

1. **Summary**: 模块的主要功能和 Rust 实现的技术方法
2. **Technical Context**: 
   - Language/Version: Rust (指定版本)
   - Primary Dependencies: 推荐的 Rust crates
   - Testing: cargo test
   - Performance Goals: 性能目标
3. **Module Mapping**: C 模块到 Rust 模块的映射
4. **Data Model**: 数据结构映射（C struct → Rust struct/enum）
5. **Implementation Phases**: 分阶段实现计划

**重要指导原则**：
- 技术选型考虑 Rust 最佳实践
- 项目结构符合 Rust 标准约定
- 考虑 C 到 Rust 的映射
- 特别注意内存管理、错误处理

**输出格式**：使用 spec-kit 的 plan-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_module_plan_system_prompt() -> str:
        """生成模块 plan 文档的系统 prompt"""
        return '你是一个 Rust 架构师，擅长为单个 C 模块制定 Rust 实现计划。输出必须统一使用简体中文。'
    
    @staticmethod
    def generate_module_tasks(project_name: str, module_name: str,
                             module_category: str, branch_name: str,
                             files: List[str], functions: List[Dict], 
                             structs: List[Dict]) -> str:
        """为单个模块生成 tasks 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""请基于以下 C 模块分析，生成一个 spec-kit 格式的任务列表文档 (tasks.md)。

项目名称：{project_name}
模块名称：{module_name}
模块类别：{module_category}
Rust 项目分支：{branch_name}

## 模块文件
{files_list}

## 函数
{len(functions)} 个

## 数据结构
{len(structs)} 个

请生成一个完整的 tasks.md 文档，包含以下任务阶段：

1. **Phase 1: Setup** - Rust 项目初始化
2. **Phase 2: Foundational** - 基础数据结构实现
3. **Phase 3-N: Functions** - 按功能分组实现各个函数
4. **Final Phase: Polish** - 优化和完善

**任务格式**：使用 `[ID] [P?] [Story] Description` 格式
- 包含具体的文件路径
- 标记任务依赖关系

**重要指导原则**：
- 先实现数据结构，再实现函数
- 相关函数分组实现
- 包含测试任务
- 标记可并行的任务

**输出格式**：使用 spec-kit 的 tasks-template.md 格式，但标题和正文统一使用简体中文。"""
    
    @staticmethod
    def generate_module_tasks_system_prompt() -> str:
        """生成模块 tasks 文档的系统 prompt"""
        return '你是一个 Rust 开发专家，擅长为单个 C 模块创建详细任务列表。输出必须统一使用简体中文。'


# ============================================================================
# UnfinishedCodeAgent Prompts - 未完成实现检查与续写
# ============================================================================

class UnfinishedCodeAgentPrompts:
    """UnfinishedCodeAgent 相关 prompt 模板"""

    @staticmethod
    def continue_unfinished_file(
        file_path: str,
        findings_summary: str,
        current_code: str,
        project_context: str,
        documentation_context: str = "",
    ) -> str:
        """为包含占位实现的 Rust 文件生成续写 prompt"""
        prompt = f"""下面这个 Rust 文件中仍然存在“未完成实现”的占位，需要你直接把它补成可工作的正式实现。

目标文件：
{file_path}

检测到的未完成占位：
{findings_summary}

当前文件代码：
```rust
{current_code}
```

项目上下文：
{project_context}
"""
        if documentation_context:
            prompt += f"""
补充文档上下文：
{documentation_context}
"""

        prompt += """
请严格遵守以下要求：
1. 只输出修复后的完整单文件 Rust 代码，不要输出解释
2. 保留当前文件中已经正确的结构、命名、公开接口和模块组织
3. 重点补全 todo!()、unimplemented!()、以及明显表示“尚未实现”的 panic!/unreachable! 占位
4. 优先实现真实逻辑，不要继续保留新的 todo!() / unimplemented!()
5. 如果某处确实无法完整恢复，也要给出最小但语义合理的可运行实现，避免直接留空占位
6. 不要随意删除已经存在的类型定义、字段、trait 实现和公共函数
7. 保持 Rust 惯用法、类型设计、所有权和错误处理风格一致

请直接输出最终代码内容，并使用 ```rust 代码块包裹。"""
        return prompt

    @staticmethod
    def continue_unfinished_file_system_prompt() -> str:
        """补全未完成 Rust 文件的系统 prompt"""
        return """你是一个专门负责补全 Rust 未实现代码的专家。

你的任务不是重写整个项目，而是在现有文件基础上定点补齐未完成实现。

工作原则：
1. 优先补齐真实逻辑，不保留 todo!() / unimplemented!() 占位
2. 尽量保持现有接口和数据结构稳定
3. 输出必须是完整、可替换原文件的单文件 Rust 代码
4. 不输出解释，只输出代码"""


unfinished_code_prompt_manager = UnfinishedCodeAgentPrompts()

# ============================================================================
# 统一的 Prompt 管理器
# ============================================================================

class PromptManager:
    """统一的 Prompt 管理器"""
    
    def __init__(self):
        self.c_doc = CDocAgentPrompts()
        self.code_fixer = CodeFixerPrompts()
        self.test_fixer = TestFixerPrompts()
        self.rust_agent = RustAgentPrompts()
        self.spec_agent = SpecAgentPrompts()
    
    def get(self, agent_name: str, prompt_name: str, **kwargs):
        """
        获取指定 agent 的 prompt
        
        Args:
            agent_name: agent 名称 ('c_doc', 'code_fixer', 'test_fixer', 'rust_agent')
            prompt_name: prompt 名称
            **kwargs: prompt 参数
            
        Returns:
            prompt 字符串
        """
        agent_map = {
            'c_doc': self.c_doc,
            'code_fixer': self.code_fixer,
            'test_fixer': self.test_fixer,
            'rust_agent': self.rust_agent,
            'spec_agent': self.spec_agent,
        }
        
        if agent_name not in agent_map:
            raise ValueError(f"Unknown agent: {agent_name}")
        
        agent = agent_map[agent_name]
        
        if not hasattr(agent, prompt_name):
            raise ValueError(f"Unknown prompt: {prompt_name} for agent: {agent_name}")
        
        method = getattr(agent, prompt_name)
        return method(**kwargs)


# 创建全局 prompt 管理器实例
prompt_manager = PromptManager()
