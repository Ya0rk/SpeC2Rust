#!/bin/bash
# Interactive: pipe "hello world test data" into yank, hit Enter on
# the default selection. The first whitespace-delimited token must be
# yanked into the (mocked) clipboard.

set -u
. "$(dirname "$0")/_lib.bash"
require_expect
setup_yank_env

expect <<'EXPECT_EOF' >/dev/null 2>&1
spawn bash -c "echo 'hello world test data' | ./yank"
set timeout 5
expect -re ".*" { send "\r"; expect eof }
EXPECT_EOF

assert_clipboard "hello"
