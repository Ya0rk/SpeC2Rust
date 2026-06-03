"""rtest 包的冒烟测试。不依赖 LLM / cargo，仅验证核心算法模块。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.rtest.signals import (  # noqa: E402
    extract_expected_outputs,
    extract_test_flags,
    extract_test_keywords,
    violates_no_fake_impl,
)
from agent.rtest.source_loader import CSourceIndex  # noqa: E402
from agent.rtest.repair_prompt import MaterialBudget  # noqa: E402
from agent.rtest.snapshot import ProjectSnapshot, SnapshotError  # noqa: E402


def _h(title: str) -> None:
    print(f"\n=== {title} ===")


def _pass(msg: str) -> None:
    print(f"  [pass] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    raise SystemExit(1)


# ---------------------------------------------------------------- signals


def test_flag_extraction_excludes_bash_internal() -> None:
    _h("#7 extract_test_flags excludes bash internal")
    script = """#!/bin/bash
set -euo pipefail
shopt -s nullglob
$RUST_BIN --uppercase foo
$RUST_BIN -v bar
"""
    flags = extract_test_flags("foo-uppercase.sh", script)
    if "-e" in flags or "-u" in flags or "-o" in flags or "-s" in flags:
        _fail(f"bash internal flag leaked: {flags}")
    if "--uppercase" not in flags:
        _fail(f"--uppercase missing: {flags}")
    if "-v" not in flags:
        _fail(f"-v missing: {flags}")
    _pass(f"flags = {flags}")


def test_flag_extraction_strips_heredoc() -> None:
    _h("#28 extract_test_flags ignores flags inside heredoc")
    script = """#!/bin/bash
$RUST_BIN --real > out
cat > exp <<EOF
usage: tool [--fake] [--made-up]
EOF
diff out exp
"""
    flags = extract_test_flags("x.sh", script)
    if "--fake" in flags or "--made-up" in flags:
        _fail(f"heredoc flag leaked: {flags}")
    if "--real" not in flags:
        _fail(f"--real missing: {flags}")
    _pass(f"flags = {flags}")


def test_expected_output_extraction() -> None:
    _h("extract_expected_outputs")
    script = """cat > exp <<END
hello
world
END
"""
    outs = extract_expected_outputs(script)
    if len(outs) != 1 or "hello" not in outs[0]:
        _fail(f"expected output parse wrong: {outs}")
    _pass(f"captured {len(outs)} block")


def test_keywords() -> None:
    _h("extract_test_keywords")
    kws = extract_test_keywords("decompress-basic.sh", 'echo "magic_token"')
    if "decompress" not in kws or "magic_token" not in kws:
        _fail(f"keywords missing: {kws}")
    _pass(f"keywords = {kws}")


def test_violates_no_fake_impl() -> None:
    _h("violates_no_fake_impl")
    if not violates_no_fake_impl("fn f() { todo!() }", []):
        _fail("todo! not detected")
    if not violates_no_fake_impl("fn f() { unimplemented!() }", []):
        _fail("unimplemented! not detected")
    # short literal shouldn't trip hardcode detection
    if violates_no_fake_impl('fn f() -> &\'static str { "ok" }', ["ok"]):
        _fail("short literal false positive")
    # long literal matching expected_output trips
    exp = "A" * 100
    if not violates_no_fake_impl(f'fn f() {{ "{exp}" }}', [exp]):
        _fail("hardcoded long literal not detected")
    _pass("all branches OK")


# ---------------------------------------------------------------- source loader


def test_source_index_file_aggregation() -> None:
    _h("#2 CSourceIndex.fulfill_request kind=file returns aggregated file")
    idx = CSourceIndex()
    idx.add({
        "name": "foo",
        "file": "src/util.c",
        "span": "1-5",
        "source": "int foo() { return 1; }",
        "num_lines": 1,
        "func_defid": "src/util.c:foo",
    })
    idx.add({
        "name": "bar",
        "file": "src/util.c",
        "span": "7-12",
        "source": "int bar() { return 2; }",
        "num_lines": 1,
        "func_defid": "src/util.c:bar",
    })
    idx.add({
        "name": "baz",
        "file": "src/other.c",
        "span": "1-3",
        "source": "int baz() { return 3; }",
        "num_lines": 1,
        "func_defid": "src/other.c:baz",
    })

    # kind=function
    rec = idx.fulfill_request({"kind": "function", "query": "foo"})
    if not rec or rec.get("name") != "foo":
        _fail(f"function lookup failed: {rec}")
    _pass("function lookup OK")

    # kind=file -> aggregate
    agg = idx.fulfill_request({"kind": "file", "query": "src/util.c"})
    if not agg or not agg.get("is_file_aggregate"):
        _fail(f"file aggregate missing: {agg}")
    src = agg.get("source", "")
    if "foo" not in src or "bar" not in src:
        _fail(f"aggregate missing members: {src}")
    if "baz" in src:
        _fail("aggregate leaked other file")
    _pass("file aggregation OK")


def test_source_index_raw_file_and_eof_clamp() -> None:
    _h("CSourceIndex raw whole-file and EOF clamp")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src_dir = root / "src"
        src_dir.mkdir()
        (src_dir / "shc.c").write_text(
            "int helper(void) { return 1; }\n"
            "static char *RTC[] = {\n"
            "  \"main\\n\",\n"
            "};\n",
            encoding="utf-8",
        )
        idx = CSourceIndex(source_root=str(root))
        idx.add({
            "name": "helper",
            "file": "src/shc.c",
            "span": "1-1",
            "source": "int helper(void) { return 1; }",
            "num_lines": 1,
            "func_defid": "src/shc.c:helper",
        })

        raw = idx.fulfill_request({"kind": "file", "query": "src/shc.c", "mode": "whole_file"})
        if not raw or not raw.get("is_raw_file"):
            _fail(f"raw whole-file missing: {raw}")
        if "RTC" not in raw.get("source", ""):
            _fail("raw whole-file lost global RTC array")
        _pass("raw whole-file includes globals")

        tail = idx.fulfill_request({
            "kind": "file",
            "query": "src/shc.c",
            "mode": "line_range",
            "start_line": 3,
            "end_line": 99,
        })
        if not tail or tail.get("end_line") != 4:
            _fail(f"EOF clamp failed: {tail}")
        if "};" not in tail.get("source", ""):
            _fail("EOF-clamped range missing tail line")
        _pass("line_range clamps to EOF")


# ---------------------------------------------------------------- material budget


def test_material_budget_lru_eviction() -> None:
    _h("#18 MaterialBudget LRU eviction")
    m = MaterialBudget(budget_chars=100)
    m.add_c_record({
        "name": "a", "file": "x.c", "source": "x" * 60, "span": "",
    })
    m.add_c_record({
        "name": "b", "file": "y.c", "source": "y" * 60, "span": "",
    })
    # Total would be 120 > 100 budget, so oldest 'a' evicted.
    names = [r["name"] for r in m.c_records()]
    if "a" in names:
        _fail(f"old record not evicted: {names}")
    if "b" not in names:
        _fail(f"new record missing: {names}")
    _pass(f"after eviction: {names}")

    # Adding rust file triggers further eviction
    m.add_rust_file("src/foo.rs", "r" * 80)
    names = [r["name"] for r in m.c_records()]
    files = list(m.rust_files().keys())
    # Should not exceed budget
    if m.total_chars() > 100:
        # Acceptable: single biggest item larger than budget remains;
        # but here 80 <= 100 so should fit after evicting b
        _fail(f"over budget: {m.total_chars()}")
    _pass(f"c_records={names}, rust_files={files}, total={m.total_chars()}")


def test_material_budget_deduplicates_whole_rust_file_and_ranges() -> None:
    _h("MaterialBudget deduplicates Rust whole-file and line ranges")
    m = MaterialBudget(budget_chars=10000)
    if not m.add_rust_file("src/shc.rs", "line1\nline2\n", start_line=1, end_line=2, mode="line_range"):
        _fail("failed to add initial line range")
    if not m.add_rust_file("src/shc.rs", "line1\nline2\n"):
        _fail("failed to add whole file")
    keys = list(m.rust_files().keys())
    if keys != ["src/shc.rs"]:
        _fail(f"line range survived whole-file add: {keys}")
    if m.add_rust_file("src/shc.rs", "line1\n", start_line=1, end_line=1, mode="line_range"):
        _fail("line range added despite whole-file coverage")
    keys = list(m.rust_files().keys())
    if keys != ["src/shc.rs"]:
        _fail(f"unexpected keys after covered range add: {keys}")
    _pass("whole-file/range dedupe OK")


def test_material_budget_refreshes_test_artifacts() -> None:
    _h("MaterialBudget refreshes test artifacts")
    m = MaterialBudget(budget_chars=10000)
    if not m.add_test_artifact("artifacts/out.x.c", "old\n", start_line=1, end_line=1, mode="line_range"):
        _fail("failed to add initial artifact range")
    if not m.add_test_artifact("artifacts/out.x.c", "new full\n"):
        _fail("whole artifact did not replace stale range")
    keys = list(m.test_artifacts().keys())
    if keys != ["artifacts/out.x.c"]:
        _fail(f"stale artifact range survived whole-file add: {keys}")
    if not m.add_test_artifact("artifacts/out.x.c", "newer full\n"):
        _fail("changed artifact content should refresh existing key")
    values = list(m.test_artifacts().values())
    if values != ["newer full\n"]:
        _fail(f"artifact content not refreshed: {values}")
    if m.add_test_artifact("artifacts/out.x.c", "newer full\n"):
        _fail("unchanged artifact should be treated as already available")
    _pass("test artifact refresh OK")


# ---------------------------------------------------------------- snapshot


def test_snapshot_atomic_restore() -> None:
    _h("#4 ProjectSnapshot atomic create + restore")
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "lib.rs").write_text("pub fn good() {}", encoding="utf-8")
        (proj / "Cargo.toml").write_text("[package]\nname='x'", encoding="utf-8")

        snap = ProjectSnapshot(str(proj))
        snap.create()

        # "破坏"项目
        (proj / "src" / "lib.rs").write_text("junk", encoding="utf-8")
        (proj / "Cargo.toml").unlink()
        (proj / "src" / "extra.rs").write_text("// added by llm", encoding="utf-8")

        snap.restore()

        if (proj / "src" / "lib.rs").read_text(encoding="utf-8") != "pub fn good() {}":
            _fail("lib.rs not restored")
        if not (proj / "Cargo.toml").exists():
            _fail("Cargo.toml not restored")
        # extra.rs is under src/ which was restored as a whole -> extra.rs should be gone
        if (proj / "src" / "extra.rs").exists():
            _fail("extra.rs should be gone after src/ restore")
        _pass("snapshot restored cleanly")

        snap.discard()
        if snap._dir is not None:
            _fail("discard did not clear _dir")
        _pass("discard OK")


def test_snapshot_incomplete_target_raises() -> None:
    _h("#4 ProjectSnapshot handles missing target items gracefully")
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "lib.rs").write_text("ok", encoding="utf-8")
        # 没有 Cargo.toml / Cargo.lock / build.rs -> create 应该只记录 src

        snap = ProjectSnapshot(str(proj))
        snap.create()
        if snap._existed != ["src"]:
            _fail(f"unexpected _existed: {snap._existed}")
        _pass("only existing targets snapshotted")

        snap.restore()  # should be a no-op restore of src
        _pass("restore with only src works")
        snap.discard()


# ---------------------------------------------------------------- main


def main() -> int:
    test_flag_extraction_excludes_bash_internal()
    test_flag_extraction_strips_heredoc()
    test_expected_output_extraction()
    test_keywords()
    test_violates_no_fake_impl()
    test_source_index_file_aggregation()
    test_source_index_raw_file_and_eof_clamp()
    test_material_budget_lru_eviction()
    test_material_budget_deduplicates_whole_rust_file_and_ranges()
    test_material_budget_refreshes_test_artifacts()
    test_snapshot_atomic_restore()
    test_snapshot_incomplete_target_raises()
    print("\nAll smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
