"""Convert ag's cram .t test files into standalone .sh scripts for RustTestAgent.

Each .t file becomes a .sh file in datasets/ggreer_the_silver_searcher/test/.
The conversion handles:
  - $ commands → shell commands
  - Expected output lines → captured and compared via diff
  - [N] exit code markers → checked via $?
  - (esc) markers → handled with printf
  - (re) markers → matched with grep -E
  - Multi-line commands (> continuation)
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "datasets" / "ggreer_the_silver_searcher" / "tests"
OUTPUT_DIR = ROOT / "datasets" / "ggreer_the_silver_searcher" / "test"


def parse_cram_file(path: Path):
    """Parse a .t file into a list of test blocks.
    Each block is a list of (command_lines, expected_lines, expected_exit_code).
    """
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Command line starts with "  $ "
        if line.startswith("  $ "):
            cmd_lines = [line[4:]]
            i += 1
            # Continuation lines start with "  > "
            while i < len(lines) and lines[i].startswith("  > "):
                cmd_lines.append(lines[i][4:])
                i += 1
            # Expected output lines: indented with "  " but not "  $ " or "  > "
            expected = []
            expected_exit = 0
            while i < len(lines):
                eline = lines[i]
                if eline.startswith("  $ ") or eline.startswith("  > "):
                    break
                if eline.startswith("  ") and not eline.startswith("  $ "):
                    content = eline[2:]
                    # Check for [N] exit code marker (standalone line)
                    m = re.match(r"^\[(\d+)\]$", content)
                    if m:
                        expected_exit = int(m.group(1))
                        i += 1
                        continue
                    expected.append(content)
                    i += 1
                else:
                    break
            blocks.append((cmd_lines, expected, expected_exit))
        else:
            i += 1
    return blocks


def escape_for_shell(s: str) -> str:
    """Escape a string for use in a shell heredoc comparison."""
    return s


def generate_sh(test_name: str, blocks, needs_color=False):
    """Generate a shell script from parsed cram blocks."""
    lines = []
    lines.append("#!/bin/bash")
    lines.append(f"# Auto-generated from {test_name}.t")
    lines.append("# Tests for ag (the silver searcher)")
    lines.append("set -u")
    lines.append("")
    lines.append("# Setup: ag is staged as ./ag by the test agent")
    lines.append("# Resolve the real ag binary before cd-ing to tmpdir")
    lines.append('SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)')
    lines.append('if [ -x "$SCRIPT_DIR/../ag" ]; then')
    lines.append('  AG_BIN=$(cd "$SCRIPT_DIR/.." && pwd)/ag')
    lines.append('elif [ -x "./ag" ]; then')
    lines.append('  AG_BIN=$(pwd)/ag')
    lines.append('else')
    lines.append('  echo "ERROR: ag binary not found"; exit 1')
    lines.append("fi")
    lines.append('AG="$AG_BIN --noaffinity --nocolor --workers=1 --parallel"')
    if needs_color:
        lines.append('AG_COLOR="$AG_BIN --noaffinity --workers=1 --parallel --color"')
    lines.append("")
    lines.append("# Each test runs in an isolated temp directory")
    lines.append('_tmpdir=$(mktemp -d)')
    lines.append('trap "rm -rf $_tmpdir" EXIT')
    lines.append('cd "$_tmpdir"')
    lines.append("")
    lines.append("fail=0")
    lines.append("")

    for idx, (cmd_lines, expected, expected_exit) in enumerate(blocks):
        cmd = "\n".join(cmd_lines)

        # Skip setup.sh sourcing and alias definitions
        if ". $TESTDIR/setup.sh" in cmd:
            continue
        if "alias ag=" in cmd:
            continue
        if "unalias ag" in cmd:
            continue

        # Replace $TESTDIR/../ag references with $AG_BIN or $AG
        cmd = re.sub(r'\$TESTDIR/\.\./ag\s+--noaffinity\s+--workers=1\s+--parallel\s+--color', '$AG_COLOR', cmd)
        cmd = re.sub(r'\$TESTDIR/\.\./ag\s+--noaffinity\s+--nocolor\s+--workers=1\s+--parallel', '$AG', cmd)
        cmd = re.sub(r'\$TESTDIR/\.\./ag\s+--noaffinity\s+--workers=1\s+--parallel', '$AG', cmd)
        cmd = re.sub(r'\$TESTDIR/\.\./ag\s+--noaffinity\s+--nocolor\s+--workers=1', '$AG', cmd)
        cmd = re.sub(r'\$TESTDIR/\.\./ag', '$AG_BIN', cmd)
        # Replace bare "ag " at start of command with $AG
        cmd = re.sub(r'^ag\b', '$AG', cmd)
        # Replace $TESTDIR with the script directory (for fixture copies)
        cmd = cmd.replace("$TESTDIR", "$SCRIPT_DIR")

        # Handle (esc) and (re) markers in expected output
        has_re = any(e.endswith(" (re)") for e in expected)
        has_esc = any(e.endswith(" (esc)") for e in expected)
        clean_expected = []
        for e in expected:
            if e.endswith(" (esc)"):
                e = e[:-6]
            elif e.endswith(" (re)"):
                e = e[:-5]
            clean_expected.append(e)

        if not expected and expected_exit == 0:
            # Just run the command, check exit code
            lines.append(f"# Block {idx + 1}")
            if "\n" in cmd:
                lines.append(cmd)
                lines.append('[ $? -eq 0 ] || { echo "FAILED: multi-line command"; fail=1; }')
            else:
                cmd_escaped = cmd.replace('"', '\\"')
                lines.append(f'{cmd}')
                lines.append(f'[ $? -eq 0 ] || {{ echo "FAILED: {cmd_escaped[:60]}"; fail=1; }}')
            lines.append("")
        elif not expected and expected_exit != 0:
            # Expect non-zero exit
            lines.append(f"# Block {idx + 1}: expect exit {expected_exit}")
            lines.append(f"{cmd}")
            lines.append(f'rc=$?; [ $rc -eq {expected_exit} ] || {{ echo "FAILED: expected exit {expected_exit}, got $rc"; fail=1; }}')
            lines.append("")
        elif has_re:
            # Use grep for regex matching
            lines.append(f"# Block {idx + 1}: regex match")
            lines.append(f"_out=$({cmd} 2>&1)")
            lines.append(f"_rc=$?")
            if expected_exit != 0:
                lines.append(f'[ $_rc -eq {expected_exit} ] || {{ echo "FAILED: expected exit {expected_exit}, got $_rc"; fail=1; }}')
            for pattern in clean_expected:
                # Escape single quotes for shell
                pat_escaped = pattern.replace("'", "'\\''")
                lines.append(f"echo \"$_out\" | grep -qE '{pat_escaped}' || {{ echo 'FAILED: regex not matched'; fail=1; }}")
            lines.append("")
        else:
            # Exact output comparison
            lines.append(f"# Block {idx + 1}")
            lines.append(f"_out=$({cmd} 2>&1)")
            lines.append(f"_rc=$?")
            if expected_exit != 0:
                lines.append(f'[ $_rc -eq {expected_exit} ] || {{ echo "FAILED: expected exit {expected_exit}, got $_rc"; fail=1; }}')
            else:
                cmd_desc = cmd_lines[0][:50].replace('"', '')
                lines.append(f'[ $_rc -eq 0 ] || {{ echo "FAILED: expected exit 0, got $_rc"; fail=1; }}')
            # Build expected string using heredoc for reliability
            lines.append("_exp=$(cat <<'EXPECTED_OUTPUT'")
            for e in clean_expected:
                lines.append(e)
            lines.append("EXPECTED_OUTPUT")
            lines.append(")")
            lines.append('if [ "$_out" != "$_exp" ]; then')
            lines.append(f'  echo "FAILED output mismatch in block {idx + 1}"')
            lines.append('  echo "expected:"; echo "$_exp"')
            lines.append('  echo "actual:"; echo "$_out"')
            lines.append("  fail=1")
            lines.append("fi")
            lines.append("")

    lines.append("exit $fail")
    return "\n".join(lines) + "\n"


def _shell_quote_multiline(expected_lines):
    """Quote a list of expected output lines for printf in shell."""
    if not expected_lines:
        return "''"
    # Join all lines with literal newlines inside a single-quoted string
    # We need to handle single quotes within the content
    joined = "\n".join(expected_lines)
    # Use $'...' syntax for the expected value to handle escapes
    escaped = joined.replace("\\", "\\\\").replace("'", "'\\''")
    return f"'{escaped}'"


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Copy fixture files
    pdf_src = TESTS_DIR / "is_binary.pdf"
    if pdf_src.exists():
        import shutil
        shutil.copy2(pdf_src, OUTPUT_DIR / "is_binary.pdf")

    print_end_src = TESTS_DIR / "print_end.txt"
    if print_end_src.exists():
        import shutil
        shutil.copy2(print_end_src, OUTPUT_DIR / "print_end.txt")

    # Process each .t file
    converted = 0
    skipped = []
    for t_file in sorted(TESTS_DIR.glob("*.t")):
        if t_file.name.endswith(".disabled"):
            continue

        test_name = t_file.stem
        needs_color = "color" in test_name or "adjacent" in test_name

        try:
            blocks = parse_cram_file(t_file)
        except Exception as e:
            print(f"  SKIP {test_name}: parse error: {e}")
            skipped.append(test_name)
            continue

        if not blocks:
            print(f"  SKIP {test_name}: no test blocks found")
            skipped.append(test_name)
            continue

        sh_content = generate_sh(test_name, blocks, needs_color=needs_color)
        out_path = OUTPUT_DIR / f"{test_name}.sh"
        out_path.write_text(sh_content, encoding="utf-8", newline="\n")
        converted += 1
        print(f"  OK {test_name}.sh")

    print(f"\nConverted: {converted}, Skipped: {len(skipped)}")
    if skipped:
        print(f"Skipped: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
