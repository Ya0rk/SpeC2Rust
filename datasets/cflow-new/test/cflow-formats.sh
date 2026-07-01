#!/bin/sh
# Output-format and multi-file cflow behavior tests.

SCRIPTPATH="$(cd -- "$(dirname "$0")" >/dev/null 2>&1 && pwd -P)"
. "$SCRIPTPATH/../../tests/init.sh"

candidate="${1:-$SCRIPTPATH/..}"
if test -d "$candidate"; then
  path_prepend_ "$candidate"
else
  path_prepend_ "$(dirname "$candidate")"
fi

command -v cflow >/dev/null 2>&1 || framework_failure_ "cannot find cflow in PATH"

simple_src="$SCRIPTPATH/simple.c"
multi_src="$SCRIPTPATH/multi.c"

dump_output_() {
  test -f out && { echo "--- stdout ---" >&2; cat out >&2; }
  test -f err && test -s err && { echo "--- stderr ---" >&2; cat err >&2; }
  echo "--------------" >&2
}

run_ok_() {
  "$@" >out 2>err || {
    rc=$?
    echo "command failed with exit code $rc: $*" >&2
    dump_output_
    fail=1
    return 1
  }
  return 0
}

require_grep_() {
  pattern=$1
  file=$2
  message=$3
  if ! grep -Eq -- "$pattern" "$file"; then
    echo "$message" >&2
    dump_output_
    fail=1
    return 1
  fi
  return 0
}

require_empty_stderr_() {
  if test -s err; then
    echo "expected empty stderr, but cflow wrote diagnostics" >&2
    dump_output_
    fail=1
    return 1
  fi
  return 0
}

echo "formats: gnu"
if run_ok_ cflow -f gnu "$simple_src"; then
  require_empty_stderr_
  require_grep_ '^main\(\)' out "GNU format should contain main() as the root"
  require_grep_ 'compute\(\)' out "GNU format should contain compute()"
fi

echo "formats: posix"
if run_ok_ cflow -f posix "$simple_src"; then
  require_empty_stderr_
  require_grep_ '^[[:space:]]*[0-9][0-9]*[[:space:]]+main:' out \
    "POSIX format should begin entries with a reference number and 'main:'"
  require_grep_ '<[^>]*simple\.c [0-9][0-9]*>' out \
    "POSIX format should include source file and line number references"
fi

echo "formats: dot"
if cflow -f dot "$simple_src" >out 2>err; then
  require_empty_stderr_
  require_grep_ '^digraph[[:space:]]+cflow[[:space:]]*\{' out \
    "DOT output should begin with 'digraph cflow {'"
  require_grep_ '->' out "DOT output should contain directed edges"
  require_grep_ 'main' out "DOT output should contain a node for main"
elif grep -q 'No such output driver' err; then
  echo "formats: dot skipped (dot output driver is not available in this build)"
else
  rc=$?
  echo "command failed with exit code $rc: cflow -f dot $simple_src" >&2
  dump_output_
  fail=1
fi

echo "formats: multiple input files"
if run_ok_ cflow -A "$simple_src" "$multi_src"; then
  require_empty_stderr_
  require_grep_ 'simple\.c' out "multi-file output should mention simple.c"
  require_grep_ 'multi\.c' out "multi-file output should mention multi.c"
  require_grep_ 'run\(\)' out "multi-file output should include run() from multi.c"
fi

echo "formats: static symbols"
if run_ok_ cflow -i s "$multi_src"; then
  require_empty_stderr_
  require_grep_ 'helper\(\)' out "-i s should include static helper()"
  require_grep_ 'twice\(\)' out "-i s should include static twice()"
  require_grep_ 'run\(\)' out "-i s output should still include run()"
fi

Exit $fail
