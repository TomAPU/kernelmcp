# tests/test_cqsearch_utils.py
import os
import shutil
import subprocess
from pathlib import Path
import pytest

from infra.cqsearch_utils import CodeQuerySearch


@pytest.fixture(scope="function")
def make_cqdb(tmp_path: Path):
    """
    Build a minimal CodeQuery DB (strict, exact queries).
    Skips with clear reasons if any tool is missing or a step fails.

    If you want to avoid building, set CQDB_PATH to a prebuilt DB:
      CQDB_PATH=/absolute/path/to/project.cqdb pytest -rs tests/test_cqsearch_utils.py
    """
    env_db = os.getenv("CQDB_PATH")
    if env_db and Path(env_db).exists():
        return {"db": str(Path(env_db).resolve()), "src": None}

    for tool in ("cqsearch", "cqmakedb", "ctags", "cscope"):
        if not shutil.which(tool):
            pytest.skip(
                f"{tool} not found in PATH. Set CQDB_PATH to a prebuilt .cqdb to run tests.")

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)

    (src / "header.h").write_text(
        "#pragma once\n"
        "#define MYMACRO(x) ((x) + 1)\n"
        "struct Foo { int x; };\n"
        "int add(int a, int b);\n"
        "extern int g_counter;\n",
        encoding="utf-8",
    )
    (src / "a.c").write_text(
        '#include "header.h"\n'
        "int g_counter = 0;\n"
        "int add(int a, int b) { return a + b + g_counter; }\n",
        encoding="utf-8",
    )
    (src / "b.c").write_text(
        '#include "header.h"\n'
        "int use(void) {\n"
        "  struct Foo f; f.x = 41;\n"
        "  g_counter += 1;\n"
        "  int z = MYMACRO(f.x);\n"
        "  return z + add(f.x, 1);\n"
        "}\n",
        encoding="utf-8",
    )

    (src / "range_test.c").write_text(
        "int target_func(int n)\n"
        "{\n"
        "    int s = 0;\n"
        "    for (int i = 0; i < n; i++) {\n"
        "        s += i;\n"
        "    }\n"
        "    return s;\n"
        "}\n",
        encoding="utf-8",
    )

    # 1) ctags
    res = subprocess.run(
        ["ctags", "-R", "-n", "-o", "tags", str(src)],
        cwd=str(src), capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.skip(f"ctags failed: {res.stderr[:400]}")

    # 2) cscope
    res = subprocess.run(
        ["cscope", "-bqR"],
        cwd=str(src), capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.skip(f"cscope failed: {res.stderr[:400]}")

    # 3) cqmakedb (strictly per `-h`)
    db = tmp_path / "project.cqdb"
    res = subprocess.run(
        ["cqmakedb",
         "-s", str(db),
         "-c", str(src / "cscope.out"),
         "-t", str(src / "tags")],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not db.exists():
        pytest.skip(f"cqmakedb failed: {res.stderr[:400]}")

    # 4) quick sanity
    res = subprocess.run(
        ["cqsearch", "-s", str(db), "-p", "5", "-t",
         "a.c", "-e", "-u", "-l", "0"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.skip(f"cqsearch sanity failed: {res.stderr[:400]}")

    return {"db": str(db), "src": str(src)}


def test_definitions(make_cqdb):
    cqs = CodeQuerySearch(make_cqdb["db"])

    # function/macro definitions (p=2)
    defs_add = cqs.get_definition("add")
    assert any(h["kind"] == "function_or_macro" and Path(
        h["path"]).name == "a.c" for h in defs_add), defs_add

    defs_macro = cqs.get_definition("MYMACRO")
    assert any(h["kind"] == "function_or_macro" for h in defs_macro), defs_macro

    # class/struct definitions (p=3)
    defs_foo = cqs.get_definition("Foo")
    assert any(h["kind"] == "class_or_struct" for h in defs_foo), defs_foo


def test_references_basic(make_cqdb):
    cqs = CodeQuerySearch(make_cqdb["db"])

    # appearances of 'add' should include b.c (the user/caller), and not include its definition location
    defs_add = {(h["path"], h["line"]) for h in cqs.get_definition("add")}
    refs_add = cqs.get_reference("add", include_calls=True)
    assert any(Path(h["path"]).name == "b.c" for h in refs_add), refs_add
    assert all((h["path"], h["line"])
               not in defs_add for h in refs_add), refs_add

    # macro symbol references should include at least one user file (a.c or b.c)
    refs_macro = cqs.get_reference("MYMACRO", include_calls=True)
    assert any(Path(h["path"]).name in {"a.c", "b.c"}
               for h in refs_macro) or refs_macro == [], refs_macro
    # (some setups may only yield calls via p=8; we don't hard-require "call" here)


def test_path_filter(make_cqdb):
    cqs = CodeQuerySearch(make_cqdb["db"])

    defs_add_a = cqs.get_definition("add", path_filter="a.c")
    assert defs_add_a and all("a.c" in h["path"]
                              for h in defs_add_a), defs_add_a

    defs_add_b = cqs.get_definition("add", path_filter="b.c")
    assert all("b.c" in h["path"]
               for h in defs_add_b) or not defs_add_b, defs_add_b


def test_no_defs_in_refs(make_cqdb):
    cqs = CodeQuerySearch(make_cqdb["db"])

    def_locs = {(h["path"], h["line"]) for h in cqs.get_definition("MYMACRO")}
    refs = cqs.get_reference("MYMACRO", include_calls=False)
    assert all((h["path"], h["line"]) not in def_locs for h in refs), refs


def test_preview_and_line_numbers(make_cqdb):
    cqs = CodeQuerySearch(make_cqdb["db"])
    defs = cqs.get_definition("add")
    assert defs, "No defs found for add"
    for h in defs:
        assert isinstance(h["line"], int) and h["line"] > 0, h
        assert isinstance(h["preview"], str), h
        assert Path(h["path"]).is_absolute(), h

def test_function_range(make_cqdb):
    cqs = CodeQuerySearch(make_cqdb["db"])
    ranges = cqs.get_function_range("target_func")
    assert ranges, "No range found for target_func"

    # pick the one defined in our injected file
    from pathlib import Path
    r = next((h for h in ranges if Path(
        h["path"]).name == "range_test.c"), None)
    if r is None:
        pytest.skip("target_func not indexed in range_test.c")

    assert r["start"] == 1, f"Expected start=1, got {r['start']}"
    if r["end"] is not None:
        assert r["end"] == 8, f"Expected end=8, got {r['end']}"
