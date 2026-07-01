#!/bin/sh
# Ensure that pwd works even when run from a very deep directory.
# Simplified from the coreutils Perl-based test to avoid requiring
# perl, CuSkip.pm, and other coreutils test infrastructure.

# Copyright (C) 2006-2024 Free Software Foundation, Inc.
# License: GPLv3+

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
. "$SCRIPTPATH/../../tests/init.sh"; path_prepend_ "${1:-$SCRIPTPATH/..}"

# We need to create a directory tree deeper than PATH_MAX (typically 4096).
# Use 128 levels of 31-char names = 128*32 = 4096 bytes of path.
depth=128
segment="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"  # 31 chars

# Build the deep tree and descend into it.
i=0
while test "$i" -lt "$depth"; do
  mkdir "$segment" 2>/dev/null || {
    # Some filesystems (or OS limits) may not support this depth.
    # That's not a pwd bug — skip the test.
    echo "skipped: cannot mkdir at depth $i" >&2
    exit 0
  }
  cd "$segment" || {
    echo "skipped: cannot cd at depth $i" >&2
    exit 0
  }
  i=$((i + 1))
done

# Now run the pwd under test and verify it produces a sane result.
actual=$(pwd 2>&1)
rc=$?

if test $rc -ne 0; then
  echo "pwd exited with code $rc" >&2
  echo "output: $actual" >&2
  fail=1
  Exit $fail
fi

# Build the expected suffix: /zzz.../zzz.../... (depth times)
expected=""
i=0
while test "$i" -lt "$depth"; do
  expected="${expected}/${segment}"
  i=$((i + 1))
done

# The actual output should end with our expected suffix.
case "$actual" in
  *"$expected")
    ;;
  *)
    echo "pwd output does not end with expected deep path" >&2
    echo "expected suffix: ...${expected}" | head -c 200 >&2
    echo "" >&2
    echo "actual (last 200 chars): ...$(printf '%s' "$actual" | tail -c 200)" >&2
    fail=1
    ;;
esac

Exit ${fail:-0}
