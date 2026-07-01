# Minimal coreutils init.sh shim for testing translated binaries
fail=0

path_prepend_() {
    local dir="$1"
    if [ -d "$dir" ]; then
        PATH="$dir:$PATH"
        export PATH
    fi
}

framework_failure_() {
    echo "FRAMEWORK FAILURE: $*" >&2
    exit 99
}

skip_() {
    echo "SKIP: $1" >&2
    exit 77
}

mkfifo_or_skip_()
{
  name=$1
  if command -v mkfifo >/dev/null 2>&1; then
    mkfifo "$name" 2>/dev/null && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import os,sys; os.mkfifo(sys.argv[1])' "$name" 2>/dev/null && return 0
  fi
  if command -v python >/dev/null 2>&1; then
    python -c 'import os,sys; os.mkfifo(sys.argv[1])' "$name" 2>/dev/null && return 0
  fi
  skip_ "cannot create fifo $name"
}

retry_delay_()
{
  func=$1
  delay=$2
  retries=$3
  shift 3

  i=0
  while :; do
    "$func" "$delay" "$@" && return 0
    i=$((i + 1))
    test "$i" -ge "$retries" && return 1
    sleep "$delay"
  done
}

compare() {
    if cmp -s "$1" "$2"; then
        return 0
    else
        echo "compare: files differ" >&2
        echo "  expected: $1" >&2
        echo "  actual:   $2" >&2
        diff -u "$1" "$2" 2>/dev/null | head -20 >&2
        return 1
    fi
}

returns_() {
    local expected="$1"; shift
    "$@"
    local rc=$?
    test "$rc" -eq "$expected"
}

Exit() {
    local val="${1:-$fail}"
    exit "$val"
}

getlimits_() {
    SSIZE_MAX=2147483647
    export SSIZE_MAX
}

get_min_ulimit_v_() {
    # 返回一个合理值让 ulimit -v 测试能跑
    echo 20000
}

# 切换到临时工作目录，避免污染原始目录
_test_tmpdir=$(mktemp -d)
cd "$_test_tmpdir" || exit 99
# 退出时清理
_cleanup_test_tmp() { rm -rf "$_test_tmpdir"; }
trap '_cleanup_test_tmp' EXIT
