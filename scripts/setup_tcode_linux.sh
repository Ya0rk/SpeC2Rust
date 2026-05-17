#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/setup_tcode_linux.sh

Environment overrides:
  CONDA_ENV=tcode
  PYTHON=/path/to/tcode/bin/python
  CREATE_ENV=1   # create missing env, or repair an env that has no bin/python
  PYTHON_VERSION=3.10
  MINIMAL=1
  INSTALL_EVAL=1
  PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/simple/

Examples:
  bash scripts/setup_tcode_linux.sh
  MINIMAL=1 bash scripts/setup_tcode_linux.sh
  CREATE_ENV=1 PYTHON_VERSION=3.10 bash scripts/setup_tcode_linux.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$WORKSPACE"

ENV_NAME="${CONDA_ENV:-tcode}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.ustc.edu.cn/pypi/simple/}"

conda_cmd() {
  conda --no-plugins "$@"
}

find_env_python() {
  if [[ -n "${CONDA_PREFIX:-}" && "$(basename "$CONDA_PREFIX")" == "$ENV_NAME" ]]; then
    if [[ -x "${CONDA_PREFIX}/bin/python" ]]; then
      printf '%s\n' "${CONDA_PREFIX}/bin/python"
      return 0
    fi
  fi

  local candidates=()
  local conda_base=""
  conda_base="$(conda_cmd info --base 2>/dev/null || true)"
  if [[ -n "$conda_base" ]]; then
    candidates+=("${conda_base}/envs/${ENV_NAME}/bin/python")
  fi

  candidates+=(
    "${HOME:-}/miniconda3/envs/${ENV_NAME}/bin/python"
    "${HOME:-}/anaconda3/envs/${ENV_NAME}/bin/python"
    "/opt/conda/envs/${ENV_NAME}/bin/python"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

repair_env_python_if_requested() {
  if [[ "${CREATE_ENV:-0}" != "1" ]]; then
    echo "Error: conda env '$ENV_NAME' exists but has no executable Python." >&2
    echo "Your env path is present, but bin/python is missing." >&2
    echo "Repair it with:" >&2
    echo "  CREATE_ENV=1 bash scripts/setup_tcode_linux.sh" >&2
    echo "Or recreate manually:" >&2
    echo "  conda --no-plugins remove -y -n $ENV_NAME --all" >&2
    echo "  conda --no-plugins create -y -n $ENV_NAME python=$PYTHON_VERSION pip" >&2
    exit 2
  fi

  echo "[setup] conda env '$ENV_NAME' exists but has no Python; installing Python $PYTHON_VERSION"
  conda_cmd install -y -n "$ENV_NAME" "python=${PYTHON_VERSION}" pip
}

ENV_PYTHON=""
if [[ -n "${PYTHON:-}" ]]; then
  if [[ ! -x "$PYTHON" ]]; then
    echo "Error: PYTHON is not executable: $PYTHON" >&2
    echo "Check the real path with:" >&2
    echo "  conda activate $ENV_NAME" >&2
    echo "  python -c 'import sys; print(sys.executable)'" >&2
    exit 2
  fi
  ENV_PYTHON="$PYTHON"
fi

if [[ -z "$ENV_PYTHON" ]]; then
  if ! command -v conda >/dev/null 2>&1; then
    echo "Error: conda not found in PATH." >&2
    echo "Open a shell where conda is available, or pass:" >&2
    echo "  PYTHON=/path/to/tcode/bin/python bash scripts/setup_tcode_linux.sh" >&2
    exit 127
  fi

  if ! conda_cmd env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    if [[ "${CREATE_ENV:-0}" == "1" ]]; then
      echo "[setup] conda env '$ENV_NAME' not found; creating it with Python $PYTHON_VERSION"
      conda_cmd create -y -n "$ENV_NAME" "python=${PYTHON_VERSION}" pip
    else
      echo "Error: conda env '$ENV_NAME' not found." >&2
      echo "Create it first, or run:" >&2
      echo "  CREATE_ENV=1 bash scripts/setup_tcode_linux.sh" >&2
      exit 2
    fi
  fi

  ENV_PYTHON="$(find_env_python || true)"
  if [[ -z "$ENV_PYTHON" ]]; then
    repair_env_python_if_requested
    ENV_PYTHON="$(find_env_python || true)"
  fi

  if [[ -z "$ENV_PYTHON" ]]; then
    echo "Error: could not find real Python for conda env '$ENV_NAME'." >&2
    echo "Check with:" >&2
    echo "  CONDA_NO_PLUGINS=true conda env list" >&2
    echo "Or pass it explicitly:" >&2
    echo "  PYTHON=/path/to/tcode/bin/python bash scripts/setup_tcode_linux.sh" >&2
    exit 2
  fi
fi

run_py() {
  PYTHONNOUSERSITE=1 PYTHONPATH= "$ENV_PYTHON" -I "$@"
}

run_pip() {
  PYTHONNOUSERSITE=1 PYTHONPATH= "$ENV_PYTHON" -I -m pip "$@"
}

echo "========================================"
echo "Workspace : $WORKSPACE"
echo "Conda env : $ENV_NAME"
echo "Python    : $ENV_PYTHON"
echo "Pip index : $PIP_INDEX_URL"
echo "Minimal   : ${MINIMAL:-1}"
echo "Eval deps : ${INSTALL_EVAL:-0}"
echo "========================================"

echo "[setup] selected Python:"
run_py - <<'PY'
import site
import sys
print(sys.executable)
print("version:", sys.version.replace("\n", " "))
print("user site enabled:", site.ENABLE_USER_SITE)
PY

echo "[setup] upgrading pip tooling"
run_pip install -i "$PIP_INDEX_URL" --upgrade pip setuptools wheel

echo "[setup] installing runtime dependencies into '$ENV_NAME'"
run_pip install -i "$PIP_INDEX_URL" \
  --ignore-installed \
  requests openai networkx \
  "tree-sitter==0.21.3" \
  "tree-sitter-c==0.21.3"

if [[ "${MINIMAL:-1}" != "1" ]]; then
  echo "[setup] installing optional LLM/training dependencies"
  run_pip install -i "$PIP_INDEX_URL" \
    --ignore-installed \
    torch datasets transformers trl peft
fi

if [[ "${INSTALL_EVAL:-0}" == "1" ]]; then
  echo "[setup] installing optional evaluation dependency: codebleu"
  run_pip install -i "$PIP_INDEX_URL" \
    --no-build-isolation \
    "codebleu==0.6.0"
fi

echo "[setup] verifying imports"
run_py - <<'PY'
import importlib
import sys

required = [
    "requests",
    "openai",
    "tree_sitter",
    "tree_sitter_c",
]

print("python:", sys.executable)
for name in required:
    mod = importlib.import_module(name)
    version = getattr(mod, "__version__", "(no __version__)")
    print(f"{name}: ok {version}")
PY

echo
echo "[setup] done."
echo "Run the agent with:"
echo "  conda activate $ENV_NAME"
echo "  PYTHON=\"\$CONDA_PREFIX/bin/python\" bash scripts/agent.sh which"
