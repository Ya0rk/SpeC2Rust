#!/bin/bash
# Test 2: c4 interprets its own source (c4.c) running hello.c.
# The "hello, world" string must still appear in the final output.

set -u

out=$(./c4 c4.c hello.c 2>&1)
rc=$?

if [ $rc -ne 0 ]; then
    echo "c4 self-host exited non-zero: rc=$rc" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi

if printf '%s' "$out" | grep -q "hello, world"; then
    exit 0
fi

echo "missing 'hello, world' in self-host output:" >&2
printf '%s\n' "$out" >&2
exit 1
