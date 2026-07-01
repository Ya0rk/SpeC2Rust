#!/bin/bash
# Sub-test 24 covers the sdsMakeRoomFor block, which expands into multiple
# test_cond invocations (sdsnew free/len, per-iteration len/free checks,
# final content, final length). Rather than tracking the exact numbering of
# each inner check, we require the entire suite to report zero failures.
# This is the only assert in the file because test_report() already prints
# the final summary line we match below.

set -u
. "$(dirname "$0")/_lib.bash"

sds_run_suite

# 1) The entry-point of the makeroomfor block must pass.
if ! printf '%s\n' "$SDS_OUTPUT" | grep -qE "^24 -.*: PASSED$"; then
    echo "Sub-test #24 (sdsmakeroomfor entry) did not pass." >&2
    printf '%s\n' "$SDS_OUTPUT" >&2
    exit 1
fi

# 2) The aggregate report must end with "<N> tests, <N> passed, 0 failed".
if ! printf '%s\n' "$SDS_OUTPUT" | grep -qE "^[0-9]+ tests, [0-9]+ passed, 0 failed$"; then
    echo "sds suite did not end with zero failures." >&2
    printf '%s\n' "$SDS_OUTPUT" >&2
    exit 1
fi

exit 0
