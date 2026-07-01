#!/bin/bash
# Test 1: run hello.c through c4 and check it prints "hello, world".
#
# RustTestAgent stages ./c4 -> Rust binary in the run dir, and copies
# every non-sh file next to this script (hello.c) into the run dir as
# fixtures.

set -u

out=$(./c4 hello.c 2>&1)
rc=$?

if [ $rc -ne 0 ]; then
    echo "c4 exited non-zero: rc=$rc" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi

if printf '%s' "$out" | grep -q "hello, world"; then
    exit 0
fi

echo "missing 'hello, world' in c4 output:" >&2
printf '%s\n' "$out" >&2
exit 1
