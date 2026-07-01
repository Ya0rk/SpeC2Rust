#!/bin/bash
# Helpers shared by every sds/test/*.sh script.
#
# The C sds test driver (built with -DSDS_TEST_MAIN) ignores argv and always
# runs the same 24 sub-tests in sequence. testhelp.h prints one line per
# sub-test:
#
#     1 - Create a string and obtain the length: PASSED
#     2 - Create a string with specified length: FAILED
#     ...
#     24 tests, 23 passed, 1 failed
#
# If any sub-test fails the binary exits 1. We don't care: each .sh script
# below greps for one specific sub-test number and reports based on that.
# Splitting the suite into one .sh per sub-test gives the LLM repair loop a
# focused single-failure signal instead of a single noisy aggregate.

set -u

# Run the suite once and cache stdout+stderr in $SDS_OUTPUT.
sds_run_suite() {
    SDS_OUTPUT="$(./sds 2>&1 || true)"
    export SDS_OUTPUT
}

# assert_test_num <num> [<description-fragment>]
#
# Verify that line "<num> - ...: PASSED" appears in the captured output.
# The description fragment is optional and only used for human-readable
# diagnostics on failure; the match itself is purely numeric.
assert_test_num() {
    local num="$1"
    local descr="${2:-}"

    sds_run_suite

    if printf '%s\n' "$SDS_OUTPUT" | grep -qE "^${num} -.*: PASSED$"; then
        return 0
    fi

    echo "Sub-test #${num} (${descr}) did not pass." >&2
    echo "Suite output:" >&2
    printf '%s\n' "$SDS_OUTPUT" >&2
    return 1
}
