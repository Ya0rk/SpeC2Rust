#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/rtest_agent.sh <project-name> [extra RustTestAgent args...]
  bash scripts/rtest_agent.sh --c-project-path <path> --rust-project-path <path> [extra args...]

Examples:
  bash scripts/rtest_agent.sh which
  bash scripts/rtest_agent.sh which --max-repair-iterations 20 --verbose
  PYTHON=/home/unix/miniconda3/envs/tcode/bin/python bash scripts/rtest_agent.sh which

Environment overrides:
  PYTHON=/path/to/python
  CONDA_ENV=tcode
  C_PROJECT_PATH=/abs/or/relative/c/project
  RUST_PROJECT_PATH=/abs/or/relative/rust/project
  BINARY_NAME=which
  MAX_REPAIR_ITERATIONS=20
  BUILD_TIMEOUT_SECONDS=600
  TEST_TIMEOUT_SECONDS=30
  CONFIG_FILE=local_config.json
  SOURCE_RECORDS=/path/to/source_records.json
  VERBOSE=1
  USE_LOG_AGENT=1
  LOG_AGENT_MAX_DEBUG_PROBES=6
  CGR_ALLOW_SYSTEM_PYTHON=1
  CGR_PYTHONNOUSERSITE=1
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$WORKSPACE"

PROJECT_NAME=""
if [[ $# -gt 0 && "${1:-}" != --* ]]; then
  PROJECT_NAME="$1"
  shift
fi

if [[ -z "$PROJECT_NAME" && ( -z "${C_PROJECT_PATH:-}" || -z "${RUST_PROJECT_PATH:-}" ) ]]; then
  usage
  exit 1
fi

C_PROJECT="${C_PROJECT_PATH:-datasets/${PROJECT_NAME}}"
RUST_PROJECT="${RUST_PROJECT_PATH:-output/${PROJECT_NAME}/${PROJECT_NAME}-rust}"
BIN_NAME="${BINARY_NAME:-${PROJECT_NAME}}"
MAX_ITERS="${MAX_REPAIR_ITERATIONS:-20}"
BUILD_TIMEOUT="${BUILD_TIMEOUT_SECONDS:-600}"
TEST_TIMEOUT="${TEST_TIMEOUT_SECONDS:-30}"
CONFIG="${CONFIG_FILE:-${WORKSPACE}/local_config.json}"
LOG_DIR="${WORKSPACE}/log"
TMP_PARENT="${TMPDIR:-/tmp}"
TMP_DIR="${TMP_PARENT%/}/cgrcode-rtest"

mkdir -p "$LOG_DIR" "$TMP_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_NAME="${PROJECT_NAME:-custom}"
LOG_FILE="${LOG_DIR}/rtest-${LOG_NAME}-${TIMESTAMP}.log"

export TEMP="$TMP_DIR"
export TMP="$TMP_DIR"
export TMPDIR="$TMP_DIR"
export CONDA_NO_PLUGINS=true
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE="${CGR_PYTHONNOUSERSITE:-1}"
export PYTHONPATH="${WORKSPACE}/src"

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    if command -v "$PYTHON" >/dev/null 2>&1; then
      command -v "$PYTHON"
      return 0
    fi
    if [[ -x "$PYTHON" ]]; then
      printf '%s\n' "$PYTHON"
      return 0
    fi
  fi

  if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
    printf '%s\n' "${CONDA_PREFIX}/bin/python"
    return 0
  fi

  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python"
    return 0
  fi

  local conda_env="${CONDA_ENV:-tcode}"
  local candidates=(
    "${HOME:-}/miniconda3/envs/${conda_env}/bin/python"
    "${HOME:-}/anaconda3/envs/${conda_env}/bin/python"
    "/opt/conda/envs/${conda_env}/bin/python"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  if [[ "${CGR_ALLOW_SYSTEM_PYTHON:-0}" == "1" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      command -v python3
      return 0
    fi
    if command -v python >/dev/null 2>&1; then
      command -v python
      return 0
    fi
  fi

  return 1
}

check_python_env() {
  local python_exe="$1"
  "$python_exe" - <<'PY'
import importlib
import sys

required = ["requests"]
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name}: {exc}")

print("Python:", sys.executable)
if missing:
    print("Missing/broken dependencies:")
    for item in missing:
        print("  -", item)
    sys.exit(1)
PY
}

if [[ ! -d "$C_PROJECT" ]]; then
  echo "Error: C project not found: $C_PROJECT" >&2
  exit 1
fi

if [[ ! -d "$RUST_PROJECT" ]]; then
  echo "Error: Rust project not found: $RUST_PROJECT" >&2
  exit 1
fi

agent_args=(
  -u -m agent.rtest.rust_test_agent
  --c-project-path "$C_PROJECT"
  --rust-project-path "$RUST_PROJECT"
  --binary-name "$BIN_NAME"
  --config-file "$CONFIG"
  --max-repair-iterations "$MAX_ITERS"
  --build-timeout-seconds "$BUILD_TIMEOUT"
  --test-timeout-seconds "$TEST_TIMEOUT"
)

if [[ -n "${SOURCE_RECORDS:-}" ]]; then
  agent_args+=(--source-records "$SOURCE_RECORDS")
fi

if [[ "${VERBOSE:-0}" == "1" ]]; then
  agent_args+=(--verbose)
fi
if [[ "${USE_LOG_AGENT:-0}" == "1" ]]; then
  agent_args+=(--use-log-agent --log-agent-max-debug-probes "${LOG_AGENT_MAX_DEBUG_PROBES:-6}")
fi

agent_args+=("$@")

echo "========================================"
echo "RustTestAgent only"
echo "Project      : ${PROJECT_NAME:-custom}"
echo "C project    : $C_PROJECT"
echo "Rust project : $RUST_PROJECT"
echo "Binary name  : $BIN_NAME"
echo "Max repairs  : $MAX_ITERS"
echo "Workspace    : $WORKSPACE"
echo "Temp dir     : $TMP_DIR"
echo "Log          : $LOG_FILE"
echo "========================================"

set +e
if python_exe="$(find_python)"; then
  echo "Runner       : $python_exe"
  if ! check_python_env "$python_exe"; then
    echo
    echo "Error: selected Python cannot run RustTestAgent dependencies." >&2
    echo "Fix one of these:" >&2
    echo "  1. conda activate ${CONDA_ENV:-tcode} && python -m pip install requests" >&2
    echo "  2. PYTHON=/absolute/path/to/env/bin/python bash scripts/rtest_agent.sh ${PROJECT_NAME:-<project>}" >&2
    echo "  3. If you intentionally use system Python: CGR_ALLOW_SYSTEM_PYTHON=1 bash scripts/rtest_agent.sh ${PROJECT_NAME:-<project>}" >&2
    status=1
  else
    "$python_exe" "${agent_args[@]}" 2>&1 | tee "$LOG_FILE"
    status="${PIPESTATUS[0]}"
  fi
else
  echo "Error: no Python runner found. Set PYTHON=/path/to/python or install python3." >&2
  status=127
fi
set -e

exit "$status"
