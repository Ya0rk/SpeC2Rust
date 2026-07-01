#!/bin/bash
# Non-interactive: yank -h is not a recognised option, so the binary
# should print a usage line and exit non-zero. We only check the exit
# code, since usage strings are environment-dependent.

set -u

out=$(./yank -h 2>&1)
rc=$?

if [ $rc -eq 0 ]; then
    echo "yank -h unexpectedly returned 0" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi
exit 0
