#!/bin/bash
# 修复 agent 调用脚本（直接调用 RustRepairAgent + RustTestAgent，跳过 main.py）
#
# 用法：
#   ./scripts/run_repair.sh <project_name> [extra_args...]
#
# 示例：
#   ./scripts/run_repair.sh lwext4
#   ./scripts/run_repair.sh cat
#   ./scripts/run_repair.sh sds
#   SKIP_TEST_AGENT=1 ./scripts/run_repair.sh lwext4
#
# 环境变量：
#   SKIP_TEST_AGENT=1                    跳过 RustTestAgent
#   CGR_RUST_REPAIR_MAX_ITERATIONS=40    编译修复最大迭代
#   CGR_RUST_TEST_MAX_ITERATIONS=20      测试修复最大迭代
#   CGR_TEST_TIMEOUT=30                  单个测试超时秒数

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$WORKSPACE"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <project_name> [extra_args...]"
    exit 1
fi

PROJECT_NAME="$1"
shift

# Paths
C_PROJECT_PATH="$WORKSPACE/datasets/$PROJECT_NAME"
OUTPUT_DIR="$WORKSPACE/output/$PROJECT_NAME"
CONFIG_FILE="$WORKSPACE/local_config.json"

# Find rust project
RUST_PROJECT=""
if [ -d "$OUTPUT_DIR" ]; then
    for d in "$OUTPUT_DIR"/*/; do
        if [ -f "${d}Cargo.toml" ]; then
            RUST_PROJECT="${d%/}"
            break
        fi
    done
fi
if [ -z "$RUST_PROJECT" ]; then
    RUST_PROJECT="$OUTPUT_DIR/${PROJECT_NAME}-rust"
fi

if [ ! -f "$RUST_PROJECT/Cargo.toml" ]; then
    echo "ERROR: Rust project not found at $RUST_PROJECT" >&2
    echo "  (No Cargo.toml — run full agent first to generate the Rust project)" >&2
    exit 1
fi

# Defaults
MAX_REPAIR_ITERS=${CGR_RUST_REPAIR_MAX_ITERATIONS:-40}
MAX_TEST_ITERS=${CGR_RUST_TEST_MAX_ITERATIONS:-20}
TEST_TIMEOUT=${CGR_TEST_TIMEOUT:-30}
SKIP_TEST_AGENT=${SKIP_TEST_AGENT:-0}

# C docs path (if exists)
C_DOCS_PATH="$OUTPUT_DIR/c_docs"
[ -d "$C_DOCS_PATH" ] || C_DOCS_PATH=""

# Environment isolation (same as agent.sh)
export CONDA_NO_PLUGINS=true
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
unset PYTHONPATH 2>/dev/null || true

# Find python (simplified from agent.sh)
find_python() {
    if [[ -n "${PYTHON:-}" && -x "${PYTHON:-}" ]]; then
        echo "$PYTHON"; return 0
    fi
    if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        echo "${CONDA_PREFIX}/bin/python"; return 0
    fi
    local conda_env="${CONDA_ENV:-tcode}"
    local candidates=(
        "${HOME}/miniconda3/envs/${conda_env}/bin/python"
        "${HOME}/anaconda3/envs/${conda_env}/bin/python"
    )
    for c in "${candidates[@]}"; do
        [[ -x "$c" ]] && { echo "$c"; return 0; }
    done
    command -v python3 2>/dev/null && return 0
    command -v python 2>/dev/null && return 0
    return 1
}

PYTHON_EXE=$(find_python) || { echo "ERROR: no python found" >&2; exit 1; }

echo "========================================"
echo "  Repair Agent (direct mode)"
echo "========================================"
echo "  C project:     $C_PROJECT_PATH"
echo "  Rust project:  $RUST_PROJECT"
echo "  C docs:        ${C_DOCS_PATH:-<none>}"
echo "  Config:        $CONFIG_FILE"
echo "  Python:        $PYTHON_EXE"
echo "  Repair iters:  $MAX_REPAIR_ITERS"
echo "  Test iters:    $MAX_TEST_ITERS"
echo "  Test timeout:  ${TEST_TIMEOUT}s"
echo "  Test agent:    $([ "$SKIP_TEST_AGENT" = "1" ] && echo "DISABLED" || echo "ENABLED")"
echo "========================================"
echo ""

# Step 1: RustRepairAgent (compile fix)
echo ">>> Step 1: RustRepairAgent (compile repair)"
(cd "$WORKSPACE/src" && "$PYTHON_EXE" -u -m agent.rust_repair_agent \
    --project_path "$RUST_PROJECT" \
    --config-file "$CONFIG_FILE" \
    --max-iterations "$MAX_REPAIR_ITERS" \
    --c-project-path "$C_PROJECT_PATH" \
    --c-docs-path "${C_DOCS_PATH:-}" \
    "$@") || repair_rc=$?

if [ "${repair_rc:-0}" -ne 0 ]; then
    echo "WARNING: RustRepairAgent exited with code $repair_rc"
fi

# Step 2: RustTestAgent (functional test + fix)
if [ "$SKIP_TEST_AGENT" != "1" ]; then
    echo ""
    echo ">>> Step 2: RustTestAgent (functional test & repair)"
    (cd "$WORKSPACE/src" && "$PYTHON_EXE" -u -m agent.rtest.rust_test_agent \
        --rust-project-path "$RUST_PROJECT" \
        --c-project-path "$C_PROJECT_PATH" \
        --config-file "$CONFIG_FILE" \
        --max-repair-iterations "$MAX_TEST_ITERS" \
        --test-timeout-seconds "$TEST_TIMEOUT")
fi

echo ""
echo "Done."
