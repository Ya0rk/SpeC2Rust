# TLDR

本目录描述编译修复阶段：如何把 rustc 诊断转成分批、可审计、可验证的多轮修复闭环。

| 文件 | 讲什么 |
| --- | --- |
| [01_rust_repair_agent.md](01_rust_repair_agent.md) | 系统性调研 RustRepairAgent 的编译修复闭环，包括诊断计划、材料读取、结构化编辑、post-check 和 release build 验证。 |
| [02_error_frontier_and_acceptance.md](02_error_frontier_and_acceptance.md) | 分析错误前沿、候选修复验收、错误数量变化和停滞判断，说明系统如何避免无效或退化修复。 |
| [03_error_organizer_batches.md](03_error_organizer_batches.md) | 说明 ErrorOrganizerAgent 如何把 rustc 诊断组织成活跃批次，降低 prompt 中的错误密度并聚焦当前修复目标。 |
| [04_deterministic_structural_repair.md](04_deterministic_structural_repair.md) | 总结确定性结构修复策略，例如 markdown fence 清理、括号 / 结构补丁、本地清洗和非 LLM 的低风险修复。 |
| [05_layered_information_injection.md](05_layered_information_injection.md) | 解释代码修复中的分层信息注入：编译错误、项目结构、诊断计划、材料、历史、工具协议和验证反馈如何组合，并给出比例统计口径。 |
