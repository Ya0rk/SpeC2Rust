#!/bin/bash
# Non-interactive: yank -v should print version and exit 0.
# This test deliberately does NOT need expect, so it works on any host.

set -u

out=$(./yank -v 2>&1)
rc=$?

if [ $rc -ne 0 ]; then
    echo "yank -v exited non-zero: rc=$rc" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi

# We do not pin a specific version string (Makefile defines VERSION at
# compile time and the Rust port may not honor it identically). We only
# require that something was printed.
if [ -z "$out" ]; then
    echo "yank -v produced no output" >&2
    exit 1
fi
exit 0
