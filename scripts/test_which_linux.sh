#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/test_which_linux.sh [rust_project_path] [binary_name] [extra RustTestAgent args...]

Defaults:
  rust_project_path = output/which/which-rust
  binary_name       = which
  c_project_path    = datasets/<binary_name>

Examples:
  bash scripts/test_which_linux.sh
  bash scripts/test_which_linux.sh output/which/which-rust which
  bash scripts/test_which_linux.sh output/taskflow/taskflow-rust taskflow

Environment overrides:
  PYTHON=python3
  C_PROJECT=datasets/which
  MAX_ITERS=3
  TEST_TIMEOUT=30
  SOURCE_RECORDS=src/parse/res/which.json
  SKIP_PREBUILD=1
EOF
}

abspath() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$REPO_ROOT" "$1" ;;
  esac
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

PYTHON_BIN="${PYTHON:-python3}"
MAX_ITERS="${MAX_ITERS:-3}"
TEST_TIMEOUT="${TEST_TIMEOUT:-30}"

rust_project_arg=""
if [[ $# -gt 0 && "${1:0:1}" != "-" ]]; then
  rust_project_arg="$1"
  shift
fi

binary_arg=""
if [[ $# -gt 0 && "${1:0:1}" != "-" ]]; then
  binary_arg="$1"
  shift
fi

if [[ -n "$rust_project_arg" ]]; then
  RUST_PROJECT="$(abspath "$rust_project_arg")"
else
  RUST_PROJECT="$(abspath "${RUST_PROJECT:-output/which/which-rust}")"
fi

if [[ -n "$binary_arg" ]]; then
  BINARY_NAME="$binary_arg"
elif [[ -n "${BINARY_NAME:-}" ]]; then
  BINARY_NAME="$BINARY_NAME"
elif [[ -n "$rust_project_arg" ]]; then
  rust_base="$(basename "$RUST_PROJECT")"
  BINARY_NAME="${rust_base%-rust}"
else
  BINARY_NAME="which"
fi

if [[ -n "${C_PROJECT:-}" ]]; then
  C_PROJECT="$(abspath "$C_PROJECT")"
else
  C_PROJECT="$(abspath "datasets/${BINARY_NAME}")"
fi

if [[ -n "${SOURCE_RECORDS:-}" ]]; then
  SOURCE_RECORDS="$(abspath "$SOURCE_RECORDS")"
elif [[ -f "${REPO_ROOT}/src/parse/res/${BINARY_NAME}.json" ]]; then
  SOURCE_RECORDS="${REPO_ROOT}/src/parse/res/${BINARY_NAME}.json"
else
  SOURCE_RECORDS=""
fi

if [[ ! -d "$RUST_PROJECT" ]]; then
  echo "Rust project not found: $RUST_PROJECT" >&2
  exit 2
fi

if [[ ! -f "$RUST_PROJECT/Cargo.toml" ]]; then
  echo "Cargo.toml not found in Rust project: $RUST_PROJECT" >&2
  exit 2
fi

if [[ ! -d "$C_PROJECT" ]]; then
  echo "C project not found: $C_PROJECT" >&2
  exit 2
fi

if [[ -f "$C_PROJECT/$BINARY_NAME" && ! -x "$C_PROJECT/$BINARY_NAME" ]]; then
  chmod +x "$C_PROJECT/$BINARY_NAME"
fi

if [[ ! -d "$C_PROJECT/test" && ! -d "$C_PROJECT/tests" ]]; then
  echo "No test directory found under: $C_PROJECT" >&2
  echo "RustTestAgent expects test/ or tests/. Put your shell test scripts there." >&2
  exit 2
fi

mkdir -p "${REPO_ROOT}/log"
LOG_FILE="${REPO_ROOT}/log/linux_rtest_${BINARY_NAME}_$(date +%Y%m%d-%H%M%S).log"

cmd=(
  "$PYTHON_BIN" -B -m agent.rtest.rust_test_agent
  --rust-project-path "$RUST_PROJECT"
  --c-project-path "$C_PROJECT"
  --binary-name "$BINARY_NAME"
  --max-repair-iterations "$MAX_ITERS"
  --test-timeout-seconds "$TEST_TIMEOUT"
)

if [[ -n "$SOURCE_RECORDS" ]]; then
  cmd+=(--source-records "$SOURCE_RECORDS")
fi

cmd+=("$@")

echo "Repository : $REPO_ROOT"
echo "Rust project: $RUST_PROJECT"
echo "C project   : $C_PROJECT"
echo "Binary name : $BINARY_NAME"
echo "Max iters   : $MAX_ITERS"
echo "Timeout     : ${TEST_TIMEOUT}s"
if [[ -n "$SOURCE_RECORDS" ]]; then
  echo "Source map  : $SOURCE_RECORDS"
fi
echo "Log file    : $LOG_FILE"
echo

if [[ "${SKIP_PREBUILD:-0}" != "1" ]]; then
  echo "[1/2] cargo build --release"
  set +e
  (
    cd "$RUST_PROJECT"
    cargo build --release
  ) 2>&1 | tee "$LOG_FILE"
  build_status="${PIPESTATUS[0]}"
  set -e
  if [[ "$build_status" -ne 0 ]]; then
    echo "cargo build --release failed. See log: $LOG_FILE" >&2
    exit "$build_status"
  fi
  echo
fi

echo "[2/2] run RustTestAgent"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
set +e
"${cmd[@]}" 2>&1 | tee -a "$LOG_FILE"
agent_status="${PIPESTATUS[0]}"
set -e
exit "$agent_status"
