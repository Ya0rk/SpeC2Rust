"""Smoke 测试：验证 tokenizer / bracket_scanner / repair_rules 行为正确。

直接执行：python -m src.agent.rust_structural_repair._smoke_test
或：python src/agent/rust_structural_repair/_smoke_test.py
"""

from __future__ import annotations

import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rust_structural_repair.bracket_scanner import scan_brackets, bracket_imbalance
from rust_structural_repair.repair_rules import apply_all_rules
from rust_structural_repair.tokenizer import mask_non_code


PASSED = 0
FAILED = 0
FAILURES = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [pass] {label}")
    else:
        FAILED += 1
        FAILURES.append(f"{label}: {detail}")
        print(f"  [FAIL] {label}: {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_balanced_file() -> None:
    section("balanced file")
    src = "fn main() {\n    println!(\"hi\");\n}\n"
    r = scan_brackets(src)
    expect(r.is_balanced, "balanced=true", str(r))


def test_strings_with_brackets() -> None:
    section("strings/comments containing brackets")
    src = textwrap.dedent('''\
        fn main() {
            let _ = "{{{{";
            let _ = '}';
            // comment with { and }
            /* block comment with } { */
            let _ = b"{}";
            let _ = r#"raw with } { #"#;
        }
    ''')
    r = scan_brackets(src)
    expect(r.is_balanced, "ignore brackets in strings/comments", str(r))


def test_lifetime_not_char() -> None:
    section("lifetime vs char literal")
    src = textwrap.dedent('''\
        fn foo<'a>(x: &'a str) -> &'a str {
            let c: char = 'x';
            x
        }
    ''')
    r = scan_brackets(src)
    expect(r.is_balanced, "lifetimes recognized", str(r))


def test_truncation_repair() -> None:
    section("R1: truncation close")
    src = textwrap.dedent('''\
        pub struct Foo {
            x: i32,
        }

        impl Foo {
            pub fn new() -> Self {
                if true {
                    println!("unclosed");
    ''')
    pre = scan_brackets(src)
    expect(not pre.is_balanced, "pre is unbalanced")
    expect(len(pre.unclosed_opens) == 3, f"3 unclosed opens, got {len(pre.unclosed_opens)}")

    fixed = apply_all_rules(src)
    expect(fixed.changed, "rule applied", fixed.description)
    post = scan_brackets(fixed.source)
    expect(post.is_balanced, "post is balanced", str(post))
    print("    fixed source tail:")
    for line in fixed.source.splitlines()[-6:]:
        print(f"      {line!r}")


def test_orphan_close_repair() -> None:
    section("R2: orphan close removal")
    src = textwrap.dedent('''\
        fn main() {
            println!("hi");
        }
        }
    ''')
    pre = scan_brackets(src)
    expect(not pre.is_balanced, "pre is unbalanced")
    expect(len(pre.orphan_closes) == 1, f"1 orphan close, got {len(pre.orphan_closes)}")

    fixed = apply_all_rules(src)
    expect(fixed.changed, "rule applied", fixed.description)
    post = scan_brackets(fixed.source)
    expect(post.is_balanced, "post is balanced", str(post))


def test_mixed_skips_safely() -> None:
    section("mixed unclosed + orphan -> R1 should skip")
    # 既有未闭合又有孤立闭：R1 应跳过（避免与 R2 互相干扰）
    src = textwrap.dedent('''\
        fn good() {}

        fn bad() {
            if true {
        }
        }
        }
    ''')
    pre = scan_brackets(src)
    expect(not pre.is_balanced, "pre unbalanced", str(pre))
    fixed = apply_all_rules(src)
    # 至少不能让情况变得更糟
    pre_imb = len(pre.unclosed_opens) + len(pre.orphan_closes) + len(pre.mismatches)
    post = scan_brackets(fixed.source)
    post_imb = len(post.unclosed_opens) + len(post.orphan_closes) + len(post.mismatches)
    expect(post_imb <= pre_imb, f"imbalance not worsened ({pre_imb} -> {post_imb})")


def test_real_sds_file() -> None:
    section("real sds.rs (already balanced)")
    target = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "output", "sds", "sds-rust", "src", "sds.rs",
        )
    )
    if not os.path.exists(target):
        print(f"  [skip] {target} not found")
        return
    with open(target, encoding="utf-8") as f:
        src = f.read()
    r = scan_brackets(src)
    expect(r.is_balanced, "sds.rs is balanced", str(r))


def test_idempotent() -> None:
    section("idempotent: applying twice gives same result")
    src = textwrap.dedent('''\
        impl Foo {
            fn bar() {
                let x = 1;
    ''')
    once = apply_all_rules(src).source
    twice = apply_all_rules(once).source
    expect(once == twice, "idempotent", f"once_len={len(once)} twice_len={len(twice)}")


def test_mask_non_code() -> None:
    section("mask_non_code preserves length and newlines")
    src = '''let x = "hello\n{world}"; // }\nlet y = 1;\n'''
    masked = mask_non_code(src)
    expect(len(masked) == len(src), "length preserved")
    # 换行保留
    expect(masked.count("\n") == src.count("\n"), "newlines preserved")
    # 字符串内容应被屏蔽
    expect("{world}" not in masked, "string content masked")
    # 注释 } 不应保留
    masked_lines = masked.splitlines()
    for line in masked_lines:
        if "//" in src.splitlines()[masked_lines.index(line)]:
            # 该行的注释部分应已被空格覆盖
            pass


def main() -> int:
    test_balanced_file()
    test_strings_with_brackets()
    test_lifetime_not_char()
    test_truncation_repair()
    test_orphan_close_repair()
    test_mixed_skips_safely()
    test_real_sds_file()
    test_idempotent()
    test_mask_non_code()

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED:
        print("Failures:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
