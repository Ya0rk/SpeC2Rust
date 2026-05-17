#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/agent.sh <project-name> [extra main.py args...]

Examples:
  bash scripts/agent.sh quadtree
  bash scripts/agent.sh which --continue
  bash scripts/agent.sh which --freeze-c-docs --rust-test-agent-max-iterations 3

Environment overrides:
  PYTHON=/path/to/python
  CONDA_ENV=tcode
  CGR_NO_DEFAULT_FLAGS=1
  CGR_ALLOW_SYSTEM_PYTHON=1
  CGR_RUST_REPAIR_MAX_ITERATIONS=40
  CGR_RUST_TEST_MAX_ITERATIONS=20
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$WORKSPACE"

PROJECT_NAME="$1"
shift

DATASET="datasets/${PROJECT_NAME}"
OUTPUT_DIR="output/${PROJECT_NAME}"
RUST_PROJECT_NAME="${PROJECT_NAME}-rust"
LOG_DIR="${WORKSPACE}/log"

TMP_PARENT="${TMPDIR:-/tmp}"
TMP_DIR="${TMP_PARENT%/}/cgrcode-agent"

mkdir -p "$LOG_DIR" "$TMP_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/agent-${PROJECT_NAME}-${TIMESTAMP}.log"

export TEMP="$TMP_DIR"
export TMP="$TMP_DIR"
export TMPDIR="$TMP_DIR"
export CONDA_NO_PLUGINS=true
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
unset PYTHONPATH

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    command -v "$PYTHON" >/dev/null 2>&1 && {
      command -v "$PYTHON"
      return 0
    }
    [[ -x "$PYTHON" ]] && {
      printf '%s\n' "$PYTHON"
      return 0
    }
  fi

  if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
    printf '%s\n' "${CONDA_PREFIX}/bin/python"
    return 0
  fi

  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python"
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    local active_python
    active_python="$(command -v python)"
    if [[ "$active_python" != "/usr/bin/python" && "$active_python" != "/usr/local/bin/python" ]]; then
      printf '%s\n' "$active_python"
      return 0
    fi
  fi

  local conda_env="${CONDA_ENV:-tcode}"
  local candidates=(
    "${HOME:-}/miniconda3/envs/${conda_env}/bin/python"
    "${HOME:-}/anaconda3/envs/${conda_env}/bin/python"
    "/opt/conda/envs/${conda_env}/bin/python"
  )

  if command -v conda >/dev/null 2>&1; then
    local conda_base
    conda_base="$(conda info --base 2>/dev/null || true)"
    if [[ -n "$conda_base" ]]; then
      candidates+=("${conda_base}/envs/${conda_env}/bin/python")
    fi
  fi

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
  PYTHONNOUSERSITE=1 PYTHONPATH= "$python_exe" - <<'PY'
import importlib
import sys

required = ["requests", "tree_sitter", "tree_sitter_c"]
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name}: {exc}")

if missing:
    print("Python:", sys.executable)
    print("Missing/broken dependencies:")
    for item in missing:
        print("  -", item)
    sys.exit(1)

print("Python:", sys.executable)
PY
}

default_flags=()
if [[ "${CGR_NO_DEFAULT_FLAGS:-0}" != "1" ]]; then
  default_flags=(
    --use-rust-repair-agent
    --use-contextual-rust-agent
    --use-rust-test-agent
    --use-spec-agent
    # --freeze-c-docs
    --rust-entry-kind main
    --rust-repair-max-iterations "${CGR_RUST_REPAIR_MAX_ITERATIONS:-40}"
    --rust-test-agent-max-iterations "${CGR_RUST_TEST_MAX_ITERATIONS:-20}"
  )
fi

main_args=(
  -u ./src/agent/main.py
  --c_project_path "$DATASET"
  --output_dir "$OUTPUT_DIR"
  --rust-project-name "$RUST_PROJECT_NAME"
  "${default_flags[@]}"
  "$@"
)

echo "========================================"
echo "Project      : $PROJECT_NAME"
echo "Dataset      : $DATASET"
echo "Output       : $OUTPUT_DIR"
echo "Rust project : $RUST_PROJECT_NAME"
echo "Workspace    : $WORKSPACE"
echo "Temp dir     : $TMP_DIR"
echo "Log          : $LOG_FILE"
echo "========================================"

set +e
if python_exe="$(find_python)"; then
  echo "Runner       : $python_exe"
  if ! check_python_env "$python_exe"; then
    echo
    echo "Error: selected Python cannot run cGrcode dependencies." >&2
    echo "Fix one of these:" >&2
    echo "  1. conda activate ${CONDA_ENV:-tcode} && python -m pip install -r requirements.txt" >&2
    echo "  2. PYTHON=/absolute/path/to/env/bin/python bash scripts/agent.sh ${PROJECT_NAME}" >&2
    echo "  3. If you intentionally use system Python: CGR_ALLOW_SYSTEM_PYTHON=1 python -m pip install -r requirements.txt" >&2
    status=1
  else
  "$python_exe" "${main_args[@]}" 2>&1 | tee "$LOG_FILE"
  status="${PIPESTATUS[0]}"
  fi
elif command -v conda >/dev/null 2>&1; then
  conda_env="${CONDA_ENV:-tcode}"
  echo "Runner       : conda run -n ${conda_env}"
  conda --no-plugins run --no-capture-output -n "$conda_env" \
    python "${main_args[@]}" 2>&1 | tee "$LOG_FILE"
  status="${PIPESTATUS[0]}"
else
  echo "Error: no Python runner found. Set PYTHON=/path/to/python or install python3." >&2
  status=127
fi
set -e

exit "$status"
