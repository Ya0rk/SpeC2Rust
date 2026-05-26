"""rtest 包共享常量。集中原先散落在代码里的魔数，方便统一调优。"""

from __future__ import annotations

# --------- 单次测试结果 / 诊断截断长度 ---------

# 正常运行时保留的 stdout / stderr 尾部字符数
TEST_OUTPUT_TAIL_CHARS = 4000

# bash -x trace 尾部保留字符数
TRACE_TAIL_CHARS = 6000

# TestCaseResult.short_failure_excerpt 默认截断
FAILURE_EXCERPT_CHARS = 2000

# failure signature 用的 hex 截断（用于 stall 检测）
FAILURE_SIGNATURE_HEX = 16

# --------- Prompt 内部各子块长度 ---------

# 传给 LLM 的 cargo 编译错误尾部
BUILD_ERROR_TAIL_CHARS = 2000

# trace 在 prompt 中展示的尾部
PROMPT_TRACE_TAIL_CHARS = 3500

# 回归警告尾部
REGRESSION_WARNING_TAIL_CHARS = 1500

# 每条 expected_output 在 prompt 中展示的最大长度
EXPECTED_OUTPUT_DISPLAY_CHARS = 1200

# 单条 expected_output 收录到分析时的最大长度（超过则忽略，避免抓到大 fixture）
EXPECTED_OUTPUT_MAX_BODY_CHARS = 4000

# prompt 中 expected_outputs 最多展示的条数
EXPECTED_OUTPUT_DISPLAY_COUNT = 3

# --------- Prompt 整体 token 预算（按字符近似） ---------

# provided_c_records + provided_rust_files + provided_test_artifacts 合计字符上限。
# 超过后启动 LRU 淘汰最早注入的材料。
# 这里按字符近似 token。256k chars 通常可容纳约 64k token 级别的材料，
# 足以保留一个 60k chars 左右的大 Rust 文件以及必要的 C 片段。
PROMPT_MATERIAL_BUDGET_CHARS = 256000

# --------- 反作弊检测 ---------

# 期望输出字面量长度阈值：超过此值且原样出现在 Rust 源码中才判定为疑似硬编码作弊
FAKE_IMPL_HARDCODED_MIN_CHARS = 64

# --------- 注入数量 ---------

SEED_C_LIMIT = 4
SEED_RUST_LIMIT = 3

# --------- Stall 判定 ---------

# 失败签名连续相同的轮数阈值（达到后提示 LLM 改变策略，不再提前终止）
STALL_SAME_SIGNATURE_ROUNDS = 3

# --------- 项目概览 ---------

PROJECT_OVERVIEW_MAX_FILES = 40
C_SOURCE_INDEX_MAX_ITEMS = 80

# --------- 项目结构快照目标 ---------

SNAPSHOT_TARGETS = ("src", "test", "Cargo.toml", "Cargo.lock", "build.rs")
