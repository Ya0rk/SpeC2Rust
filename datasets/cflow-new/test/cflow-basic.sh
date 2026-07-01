#!/bin/sh
# Basic cflow behavior tests.

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

require_no_grep_() {
  pattern=$1
  file=$2
  message=$3
  if grep -Eq -- "$pattern" "$file"; then
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

echo "basic: default output"
if run_ok_ cflow "$simple_src"; then
  cp out out.default
  require_empty_stderr_
  require_grep_ '^main\(\)' out "default output should start from main()"
  require_grep_ 'compute\(\)' out "default output should contain compute()"
  require_grep_ 'add\(\)' out "default output should contain add()"
  require_grep_ 'mul\(\)' out "default output should contain mul()"
  require_no_grep_ 'orphan\(\)' out "default output should not include unreachable orphan()"
fi

echo "basic: brief output"
if run_ok_ cflow -b "$simple_src"; then
  cp out out.brief
  require_empty_stderr_
  require_grep_ '^main\(\)' out "brief output should still start from main()"
  require_grep_ 'compute\(\)' out "brief output should contain compute()"
  require_no_grep_ 'orphan\(\)' out "brief output should not include unreachable orphan()"
  if test -f out.default; then
    lines_default=$(wc -l < out.default)
    lines_brief=$(wc -l < out.brief)
    if test "$lines_brief" -gt "$lines_default"; then
      echo "brief output should not be longer than default output" >&2
      dump_output_
      fail=1
    fi
  fi
fi

echo "basic: numbered output"
if run_ok_ cflow -n "$simple_src"; then
  require_empty_stderr_
  require_grep_ '^[[:space:]]*[0-9][0-9]*[[:space:]]+main\(\)' out \
    "numbered output should prefix main() with an output reference number"
  require_grep_ '^[[:space:]]*[0-9][0-9]*[[:space:]]+.*compute\(\)' out \
    "numbered output should prefix compute() with an output reference number"
fi

echo "basic: all functions"
if run_ok_ cflow -A "$simple_src"; then
  require_empty_stderr_
  require_grep_ 'orphan\(\)' out "-A should include the unreachable global orphan()"
  require_grep_ '^orphan\(\)' out "-A should emit orphan() as its own top-level graph"
fi

Exit $fail
