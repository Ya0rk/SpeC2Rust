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
        prompt = f"Create a detailed analysis plan and project-document skeleton for the following C project. The goal is to fully understand this project.\n\n"
        prompt += f"Project name: {project_name}\n"
        prompt += f"Number of project files: {len(files)}\n"
        prompt += f"Number of functions: {functions_count}\n"
        prompt += f"Number of structs: {structs_count}\n"
        if readme_content:
            prompt += f"Project README content: {readme_content}\n\n"
        prompt += f"File list: {', '.join(files)}\n\n"
        prompt += "Create a detailed analysis plan that includes:\n"
        prompt += "1. Overall analysis strategy\n"
        prompt += "2. Phased analysis steps\n"
        prompt += "3. Modules and features to focus on\n"
        prompt += "4. How to verify analysis completeness\n"
        prompt += "5. Specific tasks for each iteration\n"
        prompt += '''Wrap the analysis plan in <analysis_plan> tags, for example:
        <analysis_plan>
        Analysis plan
        </analysis_plan>
        '''
        prompt += "At the same time, generate a project-document skeleton with a detailed heading structure for:\n"
        prompt += "1. Project overview\n"
        prompt += "2. Project functionality\n"
        prompt += "3. Project architecture\n"
        prompt += "4. Module relationships\n"
        prompt += "5. Code structure and key components\n"
        prompt += "6. Key function analysis\n"
        prompt += "7. Data structure analysis\n"
        prompt += "8. Core algorithm analysis\n"
        prompt += '''Wrap the document skeleton in <doc_skeleton> tags, for example:
        <doc_skeleton>
        Document skeleton
        </doc_skeleton>
        '''
        prompt += "\nProvide a detailed subheading structure for each section. Later iterations will use this skeleton to fill in the content."
        return prompt
    
    @staticmethod
    def create_analysis_plan_system_prompt() -> str:
        """制定分析计划的系统 prompt"""
        sys_prompt = "You are a C project analysis expert skilled at creating detailed project analysis plans and document structures."
        sys_prompt += "**Notes:**"
        sys_prompt += "1. The analysis plan must be detailed and include analysis tasks and verification methods for each module.\n"
        sys_prompt += "2. Each iteration's tasks must be based on the previous iteration's analysis results and must not be performed independently.\n"
        return sys_prompt
    
    @staticmethod
    def perform_iteration(project_name: str, current_analysis: str, 
                         analysis_plan: str, doc_skeleton: str, iteration: int) -> str:
        """执行迭代分析的 prompt"""
        prompt = f"Based on the current analysis results and document skeleton for the following C project, perform iteration {iteration} of iterative analysis and document refinement.\n\n"
        prompt += f"Project name: {project_name}\n\n"
        prompt += "Current analysis results:\n"
        prompt += current_analysis
        prompt += "\n"
        prompt += f"Analysis plan:\n{analysis_plan}\n\n"
        prompt += f"Document skeleton:\n{doc_skeleton}\n\n"
        prompt += "In this iteration:\n"
        prompt += "1. Perform deeper analysis based on the previous iteration's analysis results.\n"
        prompt += "2. Check the document skeleton for accuracy and fix inaccurate parts if needed.\n"
        prompt += "3. Fill in detailed content for each section in the document skeleton.\n"
        prompt += "4. Add missing information.\n"
        prompt += "5. Correct misunderstandings.\n"
        prompt += "6. Improve document details.\n"
        prompt += "7. Do not write concrete code details in the analysis report. Instead, use code locations, for example: 'a.c [start line:end line]'.\n"
        prompt += "8. Do not generate project usage/installation instructions or code style/quality notes. Generate only the project analysis report.\n"
        prompt += "9. Do not test project performance. Analyze only the project's code structure and functionality.\n"
        prompt += "10. Focus completely on the specific topic under investigation and do not drift into related topics.\n"
        prompt += '''11. If you modify the document skeleton, wrap the new skeleton in </doc_skeleton> tags,
        eg:
            <doc_skeleton>
            New document skeleton content
            </doc_skeleton>
        '''
        return prompt
    
    @staticmethod
    def perform_iteration_system_prompt() -> str:
        """迭代分析的系统 prompt"""
        return 'You are a C project analysis expert skilled at iterative refinement based on existing analysis and a document skeleton.'
    
    @staticmethod
    def generate_final_document(project_name: str, doc_skeleton: str, 
                               all_analyses: str, iteration_history: str,
                               analysis_plan: str) -> str:
        """生成最终版文档的 prompt"""
        prompt = f"Based on the document skeleton, all analysis results, and iteration history for the following C project, generate an accurate and detailed final document.\n\n"
        prompt += f"Project name: {project_name}\n\n"
        prompt += f"Analysis plan:\n{analysis_plan}\n\n"
        prompt += f"Document skeleton:\n{doc_skeleton}\n\n"
        prompt += "All analysis results:\n"
        prompt += all_analyses
        prompt += "\n"
        prompt += "Iteration history:\n"
        prompt += iteration_history
        prompt += "\n"
        prompt += "Do not write concrete code details in the analysis report. Instead, use code locations, for example: 'a.c [start line:end line]'.\n"
        prompt += "Generate the final document strictly according to the document skeleton structure while integrating information from all analysis results and iteration history.\n"
        prompt += "The document should be detailed and accurate, include source-code location information, and be generated strictly from the results of executing the analysis plan.\n"
        return prompt
    
    @staticmethod
    def generate_final_document_system_prompt() -> str:
        """生成最终文档的系统 prompt"""
        return 'You are a C project analysis expert skilled at generating detailed and accurate project documentation from a document skeleton and analysis results.'
    
    @staticmethod
    def generate_spec(project_name: str, branch_name: str, today: str,
                     files_info: str, functions_info: str, structs_info: str,
                     all_analyses: str) -> str:
        """生成 spec-kit spec 文档的 prompt"""
        return f"""Based on the following C project analysis results, generate a spec-kit functional specification document (spec.md) to guide rewriting this C project in Rust.

Project name: {project_name}
Rust project branch: {branch_name}
Generation date: {today}

## C Project Structure Analysis

### File List
{files_info}

### Main Functions (first 20)
{functions_info}

### Main Data Structures (first 20)
{structs_info}

### Detailed Module Analysis
{all_analyses}

Generate a complete spec.md document containing:

1. **Feature Specification**: Describe this C project's functionality and the functionality the Rust version must implement.
2. **User Scenarios & Testing**: Describe the C project's usage scenarios. The Rust version must support these scenarios.
3. **Requirements**: 
   - Functional Requirements: The functional requirements implemented by the C project.
   - Key Entities: The key data structures in the C project and their relationships.
4. **Success Criteria**: The success criteria the Rust version must meet.

**Important guidelines**:
- Focus on the C project's **functionality** and **behavior**, not implementation details.
- Usage scenarios should describe how users use this C project.
- Functional requirements should list all major functionality provided by the C project.
- Key entities should describe the core data structures in the C project, their relationships, and their purposes.
- Success criteria should be measurable, such as "can process the same inputs" and "produces the same outputs".
- Use spec-kit's spec-template.md format.
- Titles, body text, and notes must all use English; do not output English section titles.

**Output format**: Use the standard spec-kit spec document format with all required sections and markers, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_spec_system_prompt() -> str:
        """生成 spec 文档的系统 prompt"""
        return 'You are a spec-kit expert skilled at creating functional specification documents that guide C-to-Rust project conversion.'
    
    @staticmethod
    def generate_plan(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 spec-kit plan 文档的 prompt"""
        return f"""Based on the following C project analysis results and spec document, generate a spec-kit implementation plan document (plan.md) to guide rewriting this C project in Rust.

Project name: {project_name}
Rust project branch: {branch_name}

## Detailed C Project Analysis
{all_analyses}

Generate a complete plan.md document containing:

1. **Summary**: The C project's main functionality and the technical approach for the Rust implementation.
2. **Technical Context**: 
   - Language/Version: Rust (specify version)
   - Primary Dependencies: Recommended Rust crates.
   - Storage: If the C project uses files/databases, the corresponding Rust-version approach.
   - Testing: cargo test
   - Target Platform: The same platform as the C project.
   - Project Type: library/cli/application (based on the C project type)
   - Performance Goals: Performance comparable to or better than the C project.
   - Constraints: Rust-specific constraints such as memory safety and thread safety.
   - Scale/Scope: The same scale as the C project.
3. **Project Structure**: Directory structure of the Rust project.
4. **Implementation Phases**: Phased implementation plan.

**Important guidelines**:
- Technical choices should consider Rust best practices and the ecosystem.
- The project structure should follow Rust standard conventions (src/, tests/, Cargo.toml, etc.).
- The implementation plan should be phased from basic to complex.
- Consider C-to-Rust mappings: C structs -> Rust structs/enums, C functions -> Rust functions, and so on.
- Pay special attention to converting memory management, error handling, and concurrency models.
- Titles, body text, and notes must all use English; do not output English section titles.

**Output format**: Use spec-kit's plan-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_plan_system_prompt() -> str:
        """生成 plan 文档的系统 prompt"""
        return 'You are a Rust architect skilled at creating detailed implementation plans for C-to-Rust conversion.'
    
    @staticmethod
    def generate_tasks(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 spec-kit tasks 文档的 prompt"""
        return f"""Based on the following C project analysis, spec document, and plan document, generate a spec-kit task-list document (tasks.md) to guide rewriting this C project in Rust.

Project name: {project_name}
Rust project branch: {branch_name}

## C Project Analysis
{all_analyses}

Generate a complete tasks.md document containing the following task phases:

1. **Phase 1: Setup** - Rust project initialization.
2. **Phase 2: Foundational** - Foundational architecture implementation.
3. **Phase 3-N: User Stories** - Implement each functional module by priority.
4. **Final Phase: Polish** - Optimization and refinement.

**Task format**: Use the `[ID] [P?] [Story] Description` format.
- [P] marks tasks that can be executed in parallel.
- [Story] marks which user scenario the task belongs to.
- Include concrete file paths.

**Important guidelines**:
- Tasks should be organized by the user-story priorities in the spec.
- Each user story should be independently implementable and testable.
- Include test tasks if required by the spec.
- Consider the C-to-Rust conversion order: data structures first, then core logic, then interfaces.
- Mark dependencies between tasks.

**Output format**: Use spec-kit's tasks-template.md format."""
    
    @staticmethod
    def generate_tasks_system_prompt() -> str:
        """生成 tasks 文档的系统 prompt"""
        return 'You are a Rust development expert skilled at creating detailed C-to-Rust conversion task lists.'
    
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
            files_info += f"\n=== File: {f['path']} ===\n"
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
        
        return f"""Analyze the following C project function cluster and generate a concise summary.

Cluster name: {cluster_name}
Cluster type: {cluster_type}
Included files: {', '.join([f['path'] for f in files_content])}

## Function List
{functions_list}

## Related Structs
{structs_list}

## Code Content
{files_info}

Generate the summary from these aspects:
1. The main responsibility of this function cluster (summarized in one sentence).
2. Core functionality, inputs, and outputs.
3. Key data structures and their relationships.
4. Dependencies on external components.
5. Potential risk points or behaviors that need attention.

**Important**: Keep the summary within 300 English characters, focus on functional responsibilities, and do not get lost in implementation details."""
    
    @staticmethod
    def generate_cluster_summary_system_prompt() -> str:
        """生成函数簇摘要的系统 prompt"""
        return 'You are a C code analysis expert skilled at extracting functional responsibilities and key information from code.'
    
    @staticmethod
    def generate_file_summary(file_path: str, cluster_summaries: List[Dict]) -> str:
        """生成文件摘要的 prompt"""
        clusters_info = ""
        for cs in cluster_summaries:
            clusters_info += f"\n### {cs['cluster_name']} Cluster\n"
            clusters_info += cs['summary']
        
        return f"""Based on the function-cluster summaries contained in the following file, generate a file-level summary.

File path: {file_path}

## Included Function-Cluster Summaries
{clusters_info}

Generate a file summary including:
1. The file's main responsibility.
2. The public interfaces it provides.
3. External modules it depends on.
4. Its position in the overall architecture.

**Important**: Summarize based on the cluster summaries and do not introduce new information."""
    
    @staticmethod
    def generate_file_summary_system_prompt() -> str:
        """生成文件摘要的系统 prompt"""
        return 'You are a C architecture analysis expert skilled at summarizing module responsibilities from local information.'
    
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
        
        return f"""Based on the following information, generate a detailed summary of the C project module.

Module name: {module_name}
Module category: {module_category}
Cohesion score: {cohesion_score:.2f}
Internal calls: {internal_calls}
External calls: {external_calls}

## Included Files
{files_info}

## Main Functions
{functions_list}

## Core Data Structures
{structs_list}

## File Summaries
{file_summaries_text}

Generate the module summary from these aspects:
1. Module responsibility (explainable in one sentence).
2. Inputs and outputs (clear interface boundaries).
3. List of core interfaces.
4. Which other modules it depends on.
5. Key behaviors that must be preserved.
6. If the module is too large, identify points where it can be split further.

**Important**:
- The responsibility must be explainable in one sentence.
- Inputs and outputs must be clear.
- Core interfaces must be listed.
- Dependencies must be clear.
- If the above cannot be done, state that the module split is unreasonable and needs further splitting."""
    
    @staticmethod
    def generate_module_summary_system_prompt() -> str:
        """生成模块摘要的系统 prompt"""
        return 'You are a C modular design expert skilled at identifying high-cohesion, low-coupling module boundaries.'
    
    @staticmethod
    def generate_module_spec(project_name: str, module_name: str, 
                            module_category: str, branch_name: str, 
                            today: str, files: List[str],
                            functions_info: str, structs_info: str) -> str:
        """为单个模块生成 spec 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""Based on the following C project module analysis results, generate a spec-kit functional specification document (spec.md) to guide rewriting this module in Rust.

Project name: {project_name}
Module name: {module_name}
Module category: {module_category}
Rust project branch: {branch_name}
Generation date: {today}

## Module Files
{files_list}

## Main Functions
{functions_info}

## Core Data Structures
{structs_info}

Generate a complete spec.md document containing:

1. **Feature Specification**: Describe this module's functionality and the functionality the Rust version must implement.
2. **User Scenarios & Testing**: Describe module usage scenarios. The Rust version must support these scenarios.
3. **Requirements**: 
   - Functional Requirements: The functional requirements implemented by the module.
   - Key Entities: The key data structures in the module and their relationships.
4. **Success Criteria**: The success criteria the Rust version must meet.

**Important guidelines**:
- Focus on the module's **functionality** and **behavior**, not implementation details.
- Usage scenarios should describe how this module is used.
- Functional requirements should list all major functionality provided by the module.
- Key entities should describe the module's core data structures.
- Success criteria should be measurable.
- Use spec-kit's spec-template.md format.
- Titles, body text, and notes must all use English; do not output English section titles.

**Output format**: Use the standard spec-kit spec document format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_module_spec_system_prompt() -> str:
        """生成模块 spec 文档的系统 prompt"""
        return 'You are a spec-kit expert skilled at creating functional specification documents for individual C modules.'
    
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
        
        return f"""Based on the following C module analysis results, generate a spec-kit implementation plan document (plan.md).

Project name: {project_name}
Module name: {module_name}
Module category: {module_category}
Rust project branch: {branch_name}

## Module Files
{files_list}

## Function List
{functions_list}

## Data Structures
{structs_list}

Generate a complete plan.md document containing:

1. **Summary**: The module's main functionality and the technical approach for the Rust implementation.
2. **Technical Context**: 
   - Language/Version: Rust (specify version)
   - Primary Dependencies: Recommended Rust crates.
   - Testing: cargo test
   - Performance Goals: Performance goals.
3. **Module Mapping**: Mapping from the C module to the Rust module.
4. **Data Model**: Data-structure mapping (C struct -> Rust struct/enum).
5. **Implementation Phases**: Phased implementation plan.

**Important guidelines**:
- Technical choices should consider Rust best practices.
- The project structure should follow Rust standard conventions.
- Consider C-to-Rust mappings.
- Pay special attention to memory management and error handling.
- Titles, body text, and notes must all use English; do not output English section titles.

**Output format**: Use spec-kit's plan-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_module_plan_system_prompt() -> str:
        """生成模块 plan 文档的系统 prompt"""
        return 'You are a Rust architect skilled at creating Rust implementation plans for individual C modules.'
    
    @staticmethod
    def generate_module_tasks(project_name: str, module_name: str,
                             module_category: str, branch_name: str,
                             files: List[str], functions: List[Dict], 
                             structs: List[Dict]) -> str:
        """为单个模块生成 tasks 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""Based on the following C module analysis, generate a spec-kit task-list document (tasks.md).

Project name: {project_name}
Module name: {module_name}
Module category: {module_category}
Rust project branch: {branch_name}

## Module Files
{files_list}

## Functions
{len(functions)}

## Data Structures
{len(structs)}

Generate a complete tasks.md document containing the following task phases:

1. **Phase 1: Setup** - Rust project initialization.
2. **Phase 2: Foundational** - Foundational data-structure implementation.
3. **Phase 3-N: Functions** - Implement functions grouped by functionality.
4. **Final Phase: Polish** - Optimization and refinement.

**Task format**: Use the `[ID] [P?] [Story] Description` format.
- Include concrete file paths.
- Mark task dependencies.

**Important guidelines**:
- Implement data structures first, then functions.
- Implement related functions in groups.
- Include test tasks.
- Mark tasks that can be parallelized.
- Titles, body text, and notes must all use English; do not output English section titles.

**Output format**: Use spec-kit's tasks-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_module_tasks_system_prompt() -> str:
        """生成模块 tasks 文档的系统 prompt"""
        return 'You are a Rust development expert skilled at creating detailed task lists for individual C modules.'


# ============================================================================
# CodeFixer Agent Prompts - Rust 代码修复
# ============================================================================

class CodeFixerPrompts:
    """CodeFixer 相关 prompt 模板"""
    
    @staticmethod
    def generate_fix_prompt(error_type: str, error_message: str, file_content: str = "") -> str:
        """生成代码修复提示"""
        prompt = f'''You are a Rust code repair expert. Fix the following error:

Error type: {error_type}
Error message:
{error_message}
'''
        
        if file_content:
            prompt += f'''
Current code content:
```rust
{file_content}
```

'''
        
        prompt += '''Provide the complete repaired code. Return only code and do not explain.
Wrap the returned code in a ```rust ``` markdown code block.'''
        
        return prompt
    
    @staticmethod
    def system_prompt() -> str:
        """系统 prompt"""
        return 'You are a Rust code repair expert skilled at fixing code based on error messages.'


# ============================================================================
# TestFixer Agent Prompts - Rust 测试修复
# ============================================================================

class TestFixerPrompts:
    """TestFixer 相关 prompt 模板"""
    
    @staticmethod
    def generate_fix_prompt(test_error: str, test_name: str, file_content: str = "") -> str:
        """生成测试修复提示"""
        prompt = f'''You are a Rust test repair expert. Fix the following test failure:

Test name: {test_name}

Test error message:
{test_error}
'''

        if file_content:
            prompt += f'''
Relevant code content:
```rust
{file_content}
```

'''
        
        prompt += '''Analyze the cause of the test failure and fix logic and algorithm errors in the code.
Return only the complete repaired code and do not explain.
Wrap the returned code in a ```rust ``` markdown code block.'''
        return prompt
    
    @staticmethod
    def system_prompt() -> str:
        """系统 prompt"""
        return 'You are a Rust test repair expert skilled at analyzing test failures and fixing code logic errors.'


# ============================================================================
# RustAgent Prompts - Rust 代码生成
# ============================================================================

class RustAgentPrompts:
    """RustAgent 相关 prompt 模板"""
    
    @staticmethod
    def generate_project_structure_prompt(project_name: str, all_docs: str) -> str:
        """生成项目结构设计的 prompt"""
        return f"""Based on the following project documentation, design an idiomatic but strictly constrained Rust project structure.

{all_docs}

Design a project structure that follows Rust idioms while staying within the migration scope, including:
1. Project name: {project_name}
2. Project directory/file structure (**Important**: show it in tree command format and strictly wrap it in <project_file> tags)
Example:
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
3. Main module breakdown.
4. Core data structures and trait design.
5. Key function and method signatures.
6. Error-handling strategy.

Additional requirements:
- If the context provides a "migration contract" or allowed_rust_files, it is a hard boundary; the directory tree may only use those files.
- If the context already provides C source snippets, function bodies, or interface facts, those source facts take precedence over summary descriptions.
- Do not invent core modules, instruction sets, state machines, protocols, threading models, or recovery mechanisms that do not exist in the original C project.
- If the original project is clearly a tool/CLI/executable, the Rust project structure must preserve the corresponding entry point and external usage. Do not arbitrarily turn it into a pure library project.
- Do not split out many extra modules just to be "more Rust"; split only when the input already contains clear responsibility boundaries.
- The default dependency strategy is std-only; if the context has no explicit evidence, do not introduce third-party crates.
- Do not output tests/examples/benches/ffi/release-related directories unless the context explicitly requires them and the migration contract permits them.
"""
    
    @staticmethod
    def generate_project_structure_system_prompt() -> str:
        """生成项目结构设计的系统 prompt"""
        return """You are a Rust architecture design expert skilled at designing idiomatic Rust project structures from requirements documents.
        
Design principles:
1. Follow Rust idioms, but migration scope takes priority over "best-practice embellishment".
2. Use ownership and borrowing reasonably, avoiding secondary mutable borrows and mutable/immutable borrow conflicts.
3. Introduce traits or extra abstractions only when supported by input evidence; keep the default simple and direct.
4. Keep error handling restrained and do not introduce a complex error system arbitrarily.
5. Use clear module boundaries, but do not expand capability boundaries that are absent from the input.
"""
    
    @staticmethod
    def generate_implementation_plan_prompt(project_structure: str, files_to_generate: list) -> str:
        """生成实现计划的 prompt"""
        return f"""Based on the following Rust project structure design, create a detailed implementation plan.

{project_structure}

Files that need to be generated:
{files_to_generate}

Create a step-by-step implementation plan, including:
1. Step 1: Create foundational data structures and traits.
2. Step 2: Design dependencies between function modules and create a bottom-up function generation plan, ensuring each function is generated after the functions it depends on. Generate a new file-list order (**Note**: save the new file-list order in <new_files_to_generate> tags).
3. Step 3: Implement core functionality.
4. Step 4: Implement auxiliary functionality.
5. Step 5: Implement error handling.

For each step, explain in detail:
- Files that need to be created.
- Functions/methods that need to be implemented.
- Key algorithms and implementation points.
- How to reduce unsafe usage.

Constraints:
- The new file order may only reorder files already present in `files_to_generate`; it must not add extra files.
- By default, use only the Rust standard library; suggest third-party crates only when the context provides explicit evidence.
- If the context provides C source function bodies, source snippets, or interface facts, the plan must follow those facts.
- For tool projects, explicitly preserve the command-line entry point, argument semantics, output behavior, and exit behavior in the migration plan.
- Do not expand parts that only have source locations but no implementation basis into complex new designs.
- Do not plan thread-safety wrappers, recovery mechanisms, serialization, FFI, benchmarks, property tests, or release flows unless explicitly required by the context.
- Deduplicate the plan as much as possible: do not rewrite the same fact repeatedly in Summary, Technical Context, and Implementation Phases.
- Keep the number of phases restrained, preferably 3-5 phases; do not split indefinitely.

Wrap the implementation plan in <implementation_plan> tags."""
    
    @staticmethod
    def generate_implementation_plan_system_prompt() -> str:
        """生成实现计划的系统 prompt"""
        return """You are a Rust implementation expert skilled at creating detailed code implementation plans.
        
Implementation principles:
1. Move from simple to complex: analyze required generated-function dependencies and implement bottom-up, step by step.
2. Reduce unsafe usage.
3. Prefer safe Rust standard-library APIs.
4. Follow Rust coding conventions.
5. Do not expand technical capabilities or engineering facilities that are not supported by input evidence."""
    
    @staticmethod
    def generate_code_prompt(file_path: str, context: str, implementation_plan: str) -> str:
        """生成代码的 prompt"""
        return f"""Generate the code implementation for the following file in the Rust project.

File path: {file_path}

Project context:
{context}

Implementation plan:
{implementation_plan}

Generate idiomatic, standard Rust code with these requirements:
1. Use idiomatic Rust patterns.
2. Provide complete error handling (using Result and Option).
3. Use reasonable type design.
4. Keep ownership and borrowing clear, avoiding secondary mutable borrows and mutable/immutable borrow conflicts.
5. Follow Rust coding conventions (rustfmt style).
6. Keep the code concise and readable.
7. In Cargo.toml, use # comments instead of // comments.
8. Reduce unsafe usage.
9. Pay attention to reasonable algorithmic correctness and avoid logic errors.
10. If the context already provides C source snippets, function bodies, macros, global variables, or interface facts, implement according to those source facts first; do not invent details yourself.
11. If the original project is a tool/CLI, keep the external usage interface consistent. Do not arbitrarily change argument forms, entry points, output channels, or exit semantics.
12. If the current Rust file clearly corresponds to a C function/module, migrate as closely as possible to the corresponding source code instead of rewriting a different logic only from the module summary.
13. Implement only code within the current file's responsibility; do not opportunistically add new types, traits, or module protocols unrelated to the current file.
14. Use only std by default; if the context does not explicitly allow it, do not use external dependencies such as serde, tokio, anyhow, thiserror, clap, rand, or regex.
15. Do not introduce thread-safety wrappers, recovery mechanisms, FFI, benchmarks, property tests, release scripts, or "more complete" additional engineering facilities.
16. If evidence is insufficient, keep the implementation minimal and conservative; do not expand functional boundaries to appear more complete.

Output the code content directly with no other explanatory text. Wrap the code in a ```rust code block."""
    
    @staticmethod
    def generate_code_system_prompt() -> str:
        """生成代码的系统 prompt"""
        return """You are a Rust programming expert skilled at generating idiomatic, standard Rust code.
        
Code style:
1. Use Rust idioms.
2. Use clear naming.
3. Use reasonable abstractions.
4. Implement efficiently.
5. Abstractions must be restrained and must not exceed the capability scope already present in the input."""


# ============================================================================
# SpecAgent Prompts - Spec 文档生成
# ============================================================================

class SpecAgentPrompts:
    """SpecAgent 相关 prompt 模板"""
    
    @staticmethod
    def generate_repo_manifest(project_info: Dict) -> str:
        """生成 repo_manifest 文档的 prompt"""
        return f"""Based on the following C project information, generate a detailed repository map document (00_repo_manifest.md).

Project name: {project_info['project_name']}
Number of C files: {len(project_info['c_files'])}
Number of header files: {len(project_info['h_files'])}
Number of other files: {len(project_info['other_files'])}
Build system: {project_info['build_system']}
Executables: {', '.join(project_info['executables']) if project_info['executables'] else 'None'}
Libraries: {', '.join(project_info['libraries']) if project_info['libraries'] else 'None'}

## C File List
{chr(10).join(project_info['c_files'][:50])}

## Header File List
{chr(10).join(project_info['h_files'][:50])}

## README Content
{project_info['readme_content'][:2000] if project_info['readme_content'] else 'No README file'}

Generate a detailed repository map document including:
1. Project overview.
2. Directory structure analysis.
3. Core source file list and responsibilities.
4. Header file organization.
5. Build system description.
6. Executables and libraries.
7. Subdirectories that need focused follow-up analysis.

**Important constraints**:
1. Use only facts explicitly provided in the input. Do not add directory trees, header files, executables, or libraries that do not appear.
2. If any information is missing, directly write "not observed in the current input"; do not use "assume", "infer", or "may exist".
3. File responsibilities may only be described conservatively from file names and the README summary; avoid inventing implementation details.
4. This document is a navigation page for the later Rust migration; prioritize traceable file paths and directory information."""
    
    @staticmethod
    def generate_repo_manifest_system_prompt() -> str:
        """生成 repo_manifest 文档的系统 prompt"""
        return 'You are a rigorous C project architecture analysis expert. Record only repository facts explicitly present in the input, and never fabricate directories, header files, artifacts, or responsibilities.'
    
    @staticmethod
    def identify_subsystems(project_info: Dict, file_count: int, 
                           functions_count: int, structs_count: int) -> str:
        """识别子系统的 prompt"""
        return f"""Analyze the following C project and identify the main subsystems/modules.

Project name: {project_info['project_name']}
Number of files: {file_count}
Number of functions: {functions_count}
Number of structs: {structs_count}

## File List
C files: {', '.join(project_info['c_files'][:30])}
Header files: {', '.join(project_info['h_files'][:30])}

Identify the main subsystems/modules in the project and return them as a JSON array. Each subsystem must contain:
- name: subsystem name
- description: subsystem responsibility description
- files: list of files contained in this subsystem

Example:
```json
[
  {{
    "name": "parser",
    "description": "Parser module responsible for parsing input files",
    "files": ["src/parser.c", "src/parser.h", "src/lexer.c"]
  }},
  {{
    "name": "utils",
    "description": "Utility function library",
    "files": ["src/utils.c", "src/utils.h"]
  }}
]
```

**Important**: Return only the JSON array and no other explanatory text."""
    
    @staticmethod
    def identify_subsystems_system_prompt() -> str:
        """识别子系统的系统 prompt"""
        return 'You are a C project modular analysis expert skilled at identifying functional modules and subsystems in projects.'
    
    @staticmethod
    def generate_subsystem_doc(subsystem_name: str, subsystem_description: str, 
                              files: List[Dict]) -> str:
        """生成子系统文档的 prompt"""
        files_content = ""
        for f in files[:10]:  # 限制文件数量
            files_content += f"\n=== File: {f['path']} ===\n"
            files_content += f["content"][:3000]  # 限制每个文件长度
        
        return f"""Analyze the following C project subsystem and generate a detailed subsystem description document.

Subsystem name: {subsystem_name}
Subsystem responsibility: {subsystem_description}

## Included Files
{chr(10).join([f['path'] for f in files])}

## File Content
{files_content}

Analyze this subsystem in detail from these aspects:
1. The subsystem's main functionality and responsibilities.
2. Key data structures (struct, enum, typedef, etc.).
3. Externally exposed interfaces (public functions).
4. Internal implementation details.
5. Dependencies on other subsystems.
6. Important algorithms and implementation strategies.

**Important**: Add source-code location information for each function and data structure, using the format: [file path:line number]."""
    
    @staticmethod
    def generate_subsystem_doc_system_prompt() -> str:
        """生成子系统文档的系统 prompt"""
        return 'You are a C subsystem analysis expert skilled at deeply analyzing module functionality, interfaces, and implementation details.'
    
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
        
        return f"""Based on the following C project interface information, generate a detailed interface facts document (02_interfaces).

## Public Header Files
{chr(10).join(public_headers[:20])}

## Main Functions (first {len(functions)})
{functions_list}

## Main Data Structures (first {len(structs)})
{structs_list}

Generate a detailed interface document including:
1. Public header file organization.
2. Exported function list (function signatures, parameter descriptions, return-value descriptions).
3. Important structs and type definitions.
4. Macro definitions and constants.
5. Error code definitions.
6. Input/output format specifications.
7. Configuration item descriptions.

**Important constraints**:
1. Use only header files, functions, structs, and location information explicitly provided in the input.
2. It is strictly forbidden to invent `xxx.h`, error codes, macros, configuration items, structs, function signatures, or return-value semantics.
3. If a category of information is not observed, explicitly write "not found in the current analysis results"; do not write "assumed to exist" or "can be inferred".
4. Preserve source locations for all function and struct entries. If a signature is incomplete, only mark "definition signature requires source lookup"; do not complete it yourself.
5. This document is for Rust rewriting; prioritize traceable facts rather than generalized descriptions."""
    
    @staticmethod
    def generate_interfaces_doc_system_prompt() -> str:
        """生成接口文档的系统 prompt"""
        return 'You are a strict C interface fact-organizing expert. Missing information must be marked as missing; never assume any header file, signature, macro, error code, or configuration item.'
    
    @staticmethod
    def generate_behaviors_doc(project_name: str, all_analyses: str) -> str:
        """生成行为文档的 prompt"""
        return f"""Based on the following module analysis results for C project {project_name}, generate a detailed behavior description document (03_behaviors).

## Module Analysis Results
{all_analyses}

Generate the behavior description document from these aspects:
1. Initialization flow and startup order.
2. Main user operation flows.
3. State machines and state transitions.
4. Error-handling flows.
5. Boundary conditions and special-case handling.
6. Behaviors that must remain consistent with the C version.
7. Performance-sensitive paths.

**Important**: This records "dynamic behavior", not "static interfaces". Explain how the program actually runs and how state changes.
Additional constraints:
- Describe behavior only from the module analysis results in the input. Do not add unobserved allocation strategies, error-code semantics, or return conventions.
- If evidence is insufficient, explicitly write "the current module summary is insufficient to support a more detailed behavior judgment"; do not use "possibly", "infer", or "probably".
- Do not interpret "no call relationships" as "no functionality" or "empty implementation"."""
    
    @staticmethod
    def generate_behaviors_doc_system_prompt() -> str:
        """生成行为文档的系统 prompt"""
        return 'You are a C program behavior analysis expert skilled at analyzing dynamic behavior and runtime flows.'

    @staticmethod
    def generate_behaviors_batch_summary(
        project_name: str,
        batch_index: int,
        total_batches: int,
        batch_analyses: str,
    ) -> str:
        """生成行为文档批次摘要的 prompt"""
        return f"""Based on the following partial module analysis results for C project {project_name}, generate a behavior summary.

Current batch: {batch_index}/{total_batches}

## Module Analysis Results
{batch_analyses}

Output a behavior summary suitable for later final aggregation, focusing on:
1. Initialization and startup order.
2. Main interactions between modules.
3. State changes and key state machines.
4. Error handling and boundary conditions.
5. Behavioral constraints that the later Rust rewrite must keep consistent.

Requirements:
- Keep only high-value behavior facts; do not repeat static interface lists.
- Clearly mark uncertain areas as "to be confirmed".
- Output with clear Markdown sections and bullet points.
- Do not miswrite low cohesion, low call counts, or missing information as "empty implementation", "no functionality", or "design error"."""

    @staticmethod
    def generate_behaviors_batch_summary_system_prompt() -> str:
        """生成行为文档批次摘要的系统 prompt"""
        return 'You are a program behavior synthesis expert skilled at compressing large-scale module analysis into behavior facts that can be aggregated.'

    @staticmethod
    def generate_behaviors_final_doc(project_name: str, batch_summaries: str) -> str:
        """基于批次摘要生成最终行为文档的 prompt"""
        return f"""Based on the following batched behavior summaries for C project {project_name}, generate the final behavior description document (03_behaviors).

## Batched Behavior Summaries
{batch_summaries}

Generate a complete behavior document usable for the Rust rewrite. It must cover:
1. Initialization flow and startup order.
2. Core runtime flow.
3. State machines and state transitions.
4. Error handling and recovery paths.
5. Boundary conditions and special branches.
6. Externally observable behavior that must remain consistent with the C version.
7. Performance-sensitive paths.

Requirements:
- Merge duplicate content and avoid copying between batches.
- Organize the document around the goal of "behavioral equivalence".
- Clearly mark behavior points that contain uncertainty.
- If evidence is insufficient, use "requires source confirmation" and do not add speculative behavior conclusions."""

    @staticmethod
    def generate_behaviors_final_doc_system_prompt() -> str:
        """生成最终行为文档的系统 prompt"""
        return 'You are a program behavior modeling expert skilled at integrating multi-batch analysis results into a unified, executable behavior specification.'
    
    @staticmethod
    def generate_gaps_and_risks(project_name: str, all_analyses: str) -> str:
        """生成不确定点和风险文档的 prompt"""
        return f"""Based on the following analysis results for C project {project_name}, generate a gaps and risks document (04_gaps_and_risks).

## Module Analysis Results
{all_analyses}

Identify and list:
1. Uncertainties: places where behavior in the code is unclear and requires human confirmation.
2. Potential risks: problems that may be encountered during Rust migration.
3. Modules that require further investigation.
4. Implicit behaviors that may exist but are not clearly described.
5. Global variables and side effects.
6. Undocumented boundary conditions.

**Important**: This is to ensure important details are not missed during Rust migration, so be as comprehensive as possible."""
    
    @staticmethod
    def generate_gaps_and_risks_system_prompt() -> str:
        """生成不确定点和风险文档的系统 prompt"""
        return 'You are a risk assessment expert skilled at identifying uncertainties and potential risks in C-to-Rust migration.'
    
    @staticmethod
    def generate_constitution(
        project_name: str,
        project_context: str = "",
        interface_summary: str = "",
        behavior_summary: str = "",
    ) -> str:
        """生成 constitution 文档的 prompt"""
        return f"""Generate a project-level principles document (constitution.md) for the Rust migration of C project {project_name}.

This document will define the core principles that the entire migration project must follow.

## Project Overview
{project_context}

## Interface Summary
{interface_summary}

## Behavior Summary
{behavior_summary}

Generate a constitution.md document including the following sections:

1. **Core Principles**
   - Behavioral equivalence principle.
   - Interface compatibility first principle.
   - Safety first principle.
   - Performance constraint principle.

2. **Migration Guidelines**
   - C-to-Rust mapping rules.
   - Principles for handling uncertain behavior.
   - Test verification requirements.

3. **Quality Gates**
   - Tests that must pass.
   - Code review standards.
   - Performance benchmark requirements.

**Important**: This is the "law" for the entire migration project. Later spec, plan, and tasks documents must follow these principles."""
    
    @staticmethod
    def generate_constitution_system_prompt() -> str:
        """生成 constitution 文档的系统 prompt"""
        return 'You are a software engineering principles expert skilled at creating guiding principles and quality standards for complex migration projects.'
    
    @staticmethod
    def generate_spec(project_name: str, branch_name: str, today: str,
                     files_info: str, functions_info: str, structs_info: str,
                     all_analyses: str) -> str:
        """生成 spec 文档的 prompt"""
        return f"""Based on the following C project analysis results, generate a spec-kit functional specification document (spec.md) to guide rewriting this C project in Rust.

Project name: {project_name}
Rust project branch: {branch_name}
Generation date: {today}

## C Project Structure Analysis

### File List
{files_info}

### Main Functions
{functions_info}

### Main Data Structures
{structs_info}

### Detailed Module Analysis
{all_analyses}

Generate a complete spec.md document containing:

1. **Feature Specification**: Describe this C project's functionality and the functionality the Rust version must implement.
2. **User Scenarios & Testing**: Describe the C project's usage scenarios. The Rust version must support these scenarios.
3. **Requirements**: 
   - Functional Requirements: The functional requirements implemented by the C project.
   - Key Entities: The key data structures in the C project and their relationships.
4. **Success Criteria**: The success criteria the Rust version must meet.

**Important guidelines**:
- Focus on the C project's **functionality** and **behavior**, not implementation details.
- Usage scenarios should describe how users use this C project.
- Functional requirements should list all major functionality provided by the C project.
- Key entities should describe the core data structures in the C project, their relationships, and their purposes.
- Success criteria should be measurable, such as "can process the same inputs" and "produces the same outputs".
- Use spec-kit's spec-template.md format.
- Titles, body text, and notes must all use English; do not output English section titles.
- Avoid repeating the "file list/function list/struct list" item by item across multiple sections; keep only facts truly needed for the later Rust migration.
- Every requirement must be traceable to a file, function, type, or behavior summary in the input. If there is no evidence, mark it as missing.
- Do not expand capabilities not evidenced in the input, such as thread-safety wrappers, recovery mechanisms, serialization, FFI, benchmarks, or release flows.
- Do not turn "Rust best practices" into new functional requirements; the spec describes only behavior boundaries that must be migrated.

**Output format**: Use the standard spec-kit spec document format with all required sections and markers, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_spec_system_prompt() -> str:
        """生成 spec 文档的系统 prompt"""
        return 'You are a strict spec-kit specification writer. Keep only functionality and behavior facts required for migration, and never expand missing information into new requirements.'
    
    @staticmethod
    def generate_plan(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 plan 文档的 prompt"""
        return f"""Based on the following C project analysis results and spec document, generate a spec-kit implementation plan document (plan.md) to guide rewriting this C project in Rust.

Project name: {project_name}
Rust project branch: {branch_name}

## Detailed C Project Analysis
{all_analyses}

Generate a complete plan.md document containing:

1. **Summary**: The C project's main functionality and the technical approach for the Rust implementation.
2. **Technical Context**: 
   - Language/Version: Rust (specify version)
   - Primary Dependencies: Recommended Rust crates.
   - Storage: If the C project uses files/databases, the corresponding Rust-version approach.
   - Testing: cargo test
   - Target Platform: The same platform as the C project.
   - Project Type: library/cli/application (based on the C project type)
   - Performance Goals: Performance comparable to or better than the C project.
   - Constraints: Rust-specific constraints such as memory safety and thread safety.
   - Scale/Scope: The same scale as the C project.
3. **Project Structure**: Directory structure of the Rust project.
4. **Implementation Phases**: Phased implementation plan.

**Important guidelines**:
- Technical choices should start from the Rust standard library by default; suggest third-party crates only when there is explicit evidence in the input.
- The project structure should follow Rust standard conventions, but do not add modules or engineering facilities not supported by evidence from the original project.
- The implementation plan should be phased from basic to complex.
- Consider C-to-Rust mappings: C structs -> Rust structs/enums, C functions -> Rust functions, and so on.
- Pay special attention to converting memory management, error handling, and concurrency models.
- Titles, body text, and notes must all use English; do not output English section titles.
- Do not plan thread-safety wrappers, recovery mechanisms, serialization, FFI, benchmarks, publishing to crates.io, or other unevidenced extensions.
- Avoid copying the same batch of function facts from the spec/interface docs; the plan should keep only migration steps, file mappings, and necessary technical decisions.
- Keep the number of phases restrained, preferably 3-5 phases.

**Output format**: Use spec-kit's plan-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_plan_system_prompt() -> str:
        """生成 plan 文档的系统 prompt"""
        return 'You are a restrained Rust architect. Plans must be bounded by the migration scope and must not expand new capabilities in pursuit of completeness.'
    
    @staticmethod
    def generate_tasks(project_name: str, branch_name: str, all_analyses: str) -> str:
        """生成 tasks 文档的 prompt"""
        return f"""Based on the following C project analysis, spec document, and plan document, generate a spec-kit task-list document (tasks.md) to guide rewriting this C project in Rust.

Project name: {project_name}
Rust project branch: {branch_name}

## C Project Analysis
{all_analyses}

Generate a complete tasks.md document containing the following task phases:

1. **Phase 1: Setup** - Rust project initialization.
2. **Phase 2: Foundational** - Foundational architecture implementation.
3. **Phase 3-N: User Stories** - Implement each functional module by priority.
4. **Final Phase: Polish** - Optimization and refinement.

**Task format**: Use the `[ID] [P?] [Story] Description` format.
- [P] marks tasks that can be executed in parallel.
- [Story] marks which user scenario the task belongs to.
- Include concrete file paths.

**Important guidelines**:
- Tasks should be organized by the user-story priorities in the spec.
- Each user story should be independently implementable and testable.
- Include test tasks if required by the spec.
- Consider the C-to-Rust conversion order: data structures first, then core logic, then interfaces.
- Mark dependencies between tasks.
- Titles, body text, and notes must all use English; do not output English section titles.
- Do not create duplicate tasks for the same work; each task should correspond directly to one clear migration action.
- Keep the number of phases restrained and avoid expanding into tail engineering phases such as Phase 8/9/10.
- Do not add tasks for unevidenced thread safety, recovery mechanisms, serialization, FFI, benchmarks, or release flows.
- Write only Rust target file paths that can be inferred from the input; do not invent many support files.

**Output format**: Use spec-kit's tasks-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_tasks_system_prompt() -> str:
        """生成 tasks 文档的系统 prompt"""
        return 'You are a Rust development expert who strictly controls scope. The task list must be executable, deduplicated, and must not exceed the migration boundary.'
    
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
        
        return f"""Based on the following information, generate a detailed summary of the C project module.

Module name: {module_name}
Module category: {module_category}
Cohesion score: {cohesion_score:.2f}
Internal calls: {internal_calls}
External calls: {external_calls}

## Included Files
{files_info}

## Main Functions
{functions_list}

## Core Data Structures
{structs_list}

## File Summaries
{file_summaries_text}

Generate the module summary from these aspects:
1. Module responsibility (explainable in one sentence).
2. Inputs and outputs (clear interface boundaries).
3. List of core interfaces.
4. Which other modules it depends on.
5. Key behaviors that must be preserved.
6. If the module is too large, identify points where it can be split further.

**Important**:
- The responsibility must be explainable in one sentence.
- Inputs and outputs must be clear.
- Core interfaces must be listed.
- Dependencies must be clear.
- If the above cannot be done, state that the module split is unreasonable and needs further splitting."""
    
    @staticmethod
    def generate_module_summary_system_prompt() -> str:
        """生成模块摘要的系统 prompt"""
        return 'You are a C modular design expert skilled at identifying high-cohesion, low-coupling module boundaries.'
    
    @staticmethod
    def generate_module_spec(project_name: str, module_name: str, 
                            module_category: str, branch_name: str, 
                            today: str, files: List[str],
                            functions_info: str, structs_info: str) -> str:
        """为单个模块生成 spec 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""Based on the following C project module analysis results, generate a spec-kit functional specification document (spec.md) to guide rewriting this module in Rust.

Project name: {project_name}
Module name: {module_name}
Module category: {module_category}
Rust project branch: {branch_name}
Generation date: {today}

## Module Files
{files_list}

## Main Functions
{functions_info}

## Core Data Structures
{structs_info}

Generate a complete spec.md document containing:

1. **Feature Specification**: Describe this module's functionality and the functionality the Rust version must implement.
2. **User Scenarios & Testing**: Describe module usage scenarios. The Rust version must support these scenarios.
3. **Requirements**: 
   - Functional Requirements: The functional requirements implemented by the module.
   - Key Entities: The key data structures in the module and their relationships.
4. **Success Criteria**: The success criteria the Rust version must meet.

**Important guidelines**:
- Focus on the module's **functionality** and **behavior**, not implementation details.
- Usage scenarios should describe how this module is used.
- Functional requirements should list all major functionality provided by the module.
- Key entities should describe the module's core data structures.
- Success criteria should be measurable.
- Use spec-kit's spec-template.md format.
- Do not repeatedly rewrite function lists, file lists, or type lists in every section; keep the same fact only once.
- Every requirement and success criterion must be traceable to module files, functions, or types in the input.
- Do not expand new capabilities, new public APIs, thread-safety promises, serialization, recovery mechanisms, FFI, or benchmarks that are not evidenced for this module.

**Output format**: Use the standard spec-kit spec document format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_module_spec_system_prompt() -> str:
        """生成模块 spec 文档的系统 prompt"""
        return 'You are a strict module specification writer. Module specs describe only well-evidenced functional boundaries; repeated listing and invented extended capabilities are forbidden.'
    
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
        
        return f"""Based on the following C module analysis results, generate a spec-kit implementation plan document (plan.md).

Project name: {project_name}
Module name: {module_name}
Module category: {module_category}
Rust project branch: {branch_name}

## Module Files
{files_list}

## Function List
{functions_list}

## Data Structures
{structs_list}

Generate a complete plan.md document containing:

1. **Summary**: The module's main functionality and the technical approach for the Rust implementation.
2. **Technical Context**: 
   - Language/Version: Rust (specify version)
   - Primary Dependencies: Recommended Rust crates.
   - Testing: cargo test
   - Performance Goals: Performance goals.
3. **Module Mapping**: Mapping from the C module to the Rust module.
4. **Data Model**: Data-structure mapping (C struct -> Rust struct/enum).
5. **Implementation Phases**: Phased implementation plan.

**Important guidelines**:
- Use the Rust standard library by default for technical choices; suggest third-party crates only when there is explicit evidence in the input.
- The project structure should follow Rust standard conventions, but do not expand more modules or supporting facilities for "elegance".
- Consider C-to-Rust mappings.
- Pay special attention to memory management and error handling.
- Do not copy the spec's functional descriptions wholesale into the plan; the plan should keep only technical decisions, file mappings, and migration order.
- Keep the number of phases restrained, preferably 3-5 phases.
- Do not plan thread-safety wrappers, recovery mechanisms, serialization, FFI, benchmarks, release flows, or other unevidenced work items.

**Output format**: Use spec-kit's plan-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_module_plan_system_prompt() -> str:
        """生成模块 plan 文档的系统 prompt"""
        return 'You are a restrained Rust architect. Module plans must focus on migrating existing files and functions and must not expand extra capabilities.'
    
    @staticmethod
    def generate_module_tasks(project_name: str, module_name: str,
                             module_category: str, branch_name: str,
                             files: List[str], functions: List[Dict], 
                             structs: List[Dict]) -> str:
        """为单个模块生成 tasks 文档的 prompt"""
        files_list = "\n".join(files[:20])
        
        return f"""Based on the following C module analysis, generate a spec-kit task-list document (tasks.md).

Project name: {project_name}
Module name: {module_name}
Module category: {module_category}
Rust project branch: {branch_name}

## Module Files
{files_list}

## Functions
{len(functions)}

## Data Structures
{len(structs)}

Generate a complete tasks.md document containing the following task phases:

1. **Phase 1: Setup** - Rust project initialization.
2. **Phase 2: Foundational** - Foundational data-structure implementation.
3. **Phase 3-N: Functions** - Implement functions grouped by functionality.
4. **Final Phase: Polish** - Optimization and refinement.

**Task format**: Use the `[ID] [P?] [Story] Description` format.
- Include concrete file paths.
- Mark task dependencies.

**Important guidelines**:
- Implement data structures first, then functions.
- Implement related functions in groups.
- Include test tasks only when explicitly required by the input.
- Mark tasks that can be parallelized.
- Do not repeatedly split the same work, and do not schedule one function repeatedly across multiple phases.
- File paths may only use Rust target files directly inferable from input files.
- Keep the number of phases restrained and avoid late engineering phases unrelated to the current module.
- Do not add tasks for unevidenced thread safety, recovery mechanisms, serialization, FFI, benchmarks, or release flows.

**Output format**: Use spec-kit's tasks-template.md format, but use English consistently for titles and body text."""
    
    @staticmethod
    def generate_module_tasks_system_prompt() -> str:
        """生成模块 tasks 文档的系统 prompt"""
        return 'You are a Rust development expert who strictly controls scope. Module tasks must be deduplicated, stay close to file migration actions, and must not expand unevidenced tasks.'


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
        prompt = f"""The following Rust file still contains "unfinished implementation" placeholders. Complete them directly into working production implementations.

Target file:
{file_path}

Detected unfinished placeholders:
{findings_summary}

Current file code:
```rust
{current_code}
```

Project context:
{project_context}
"""
        if documentation_context:
            prompt += f"""
Additional documentation context:
{documentation_context}
"""

        prompt += """
Strictly follow these requirements:
1. Output only the complete repaired single-file Rust code, with no explanation.
2. Preserve the structures, names, public interfaces, and module organization that are already correct in the current file.
3. Focus on completing todo!(), unimplemented!(), and panic!/unreachable! placeholders that clearly indicate "not implemented yet".
4. Prefer implementing real logic; do not keep adding new todo!() / unimplemented!().
5. If some part truly cannot be fully recovered, still provide the smallest semantically reasonable runnable implementation and avoid leaving an empty placeholder.
6. Do not arbitrarily delete existing type definitions, fields, trait implementations, or public functions.
7. Keep Rust idioms, type design, ownership, and error-handling style consistent.

Output the final code content directly and wrap it in a ```rust code block."""
        return prompt

    @staticmethod
    def continue_unfinished_file_system_prompt() -> str:
        """补全未完成 Rust 文件的系统 prompt"""
        return """You are an expert dedicated to completing unimplemented Rust code.

Your task is not to rewrite the entire project, but to complete unfinished implementations precisely within the existing file.

Working principles:
1. Prefer completing real logic and do not keep todo!() / unimplemented!() placeholders.
2. Keep existing interfaces and data structures as stable as possible.
3. The output must be complete single-file Rust code that can replace the original file.
4. Do not output explanations; output only code."""


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
