#!/bin/sh
# Option-focused cflow behavior tests.

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
recursion_src="$SCRIPTPATH/recursion.c"

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

echo "flags: reverse output"
if run_ok_ cflow -r "$simple_src"; then
  require_empty_stderr_
  require_grep_ '^add\(\)|^mul\(\)' out "reverse output should start from a leaf callee such as add() or mul()"
  require_grep_ 'main\(\)' out "reverse output should still reference main()"
fi

echo "flags: ascii tree"
if run_ok_ cflow -T "$simple_src"; then
  require_empty_stderr_
  require_grep_ '[+\\]-' out "ASCII tree output should contain branch markers"
  require_grep_ 'main\(\)' out "ASCII tree output should contain main()"
fi

echo "flags: depth limit"
if run_ok_ cflow --depth 1 "$simple_src"; then
  cp out out.depth1
  require_empty_stderr_
  require_grep_ 'main\(\)' out "depth 1 output should contain main()"
  require_no_grep_ 'compute\(\)' out "depth 1 output should not descend into compute()"
fi
if run_ok_ cflow --depth 2 "$simple_src"; then
  cp out out.depth2
  require_empty_stderr_
  require_grep_ 'compute\(\)' out "depth 2 output should include compute()"
  if test -f out.depth1; then
    lines_depth1=$(wc -l < out.depth1)
    lines_depth2=$(wc -l < out.depth2)
    if test "$lines_depth2" -le "$lines_depth1"; then
      echo "depth 2 output should be larger than depth 1 output" >&2
      dump_output_
      fail=1
    fi
  fi
fi

echo "flags: cross-reference"
if run_ok_ cflow -x "$simple_src"; then
  require_empty_stderr_
  require_grep_ '^main([:]|[[:space:]])|main[[:space:]]*:' out "cross-reference output should list main"
  require_grep_ '^compute([:]|[[:space:]])|compute[[:space:]]*:' out "cross-reference output should list compute"
  require_grep_ '^add([:]|[[:space:]])|add[[:space:]]*:' out "cross-reference output should list add"
  require_grep_ '^mul([:]|[[:space:]])|mul[[:space:]]*:' out "cross-reference output should list mul"
  require_grep_ '^orphan([:]|[[:space:]])|orphan[[:space:]]*:' out "cross-reference output should list orphan"
fi

echo "flags: recursion detection"
if run_ok_ cflow "$recursion_src"; then
  require_empty_stderr_
  require_grep_ 'fib\(\)' out "recursion test should contain fib()"
  require_grep_ 'fact\(\)' out "recursion test should contain fact()"
  require_grep_ '\(R\)' out "recursive root should be marked with (R)"
  require_grep_ 'recursive: see' out "recursive back-edge should be marked with 'recursive: see'"
fi

Exit $fail
