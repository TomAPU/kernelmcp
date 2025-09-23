import os
from pathlib import Path

import pytest

from kernelmcp.infra.source_view import SourceView


@pytest.fixture()
def tmp_project(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    # create some files
    (root / "a.c").write_text("int foo() { return 1; }\nvoid bar() {}\n")
    (root / "sub").mkdir()
    (root / "sub" / "b.c").write_text("// b file\nint baz() {}\n")
    return root


def test_get_source_basic(tmp_project: Path):
    sv = SourceView(str(tmp_project))
    out = sv.get_source("a.c", start=1, end=1)
    assert out["path"] == "a.c"
    # default get_source returns the exact first line (1-based)
    expected_line = "int foo() { return 1; }"
    assert out["code"] == expected_line
    assert out["lines"] == [{"lineno": 1, "text": expected_line}]


def test_search_fallback(tmp_project: Path):
    sv = SourceView(str(tmp_project))
    # If rg is not available, search should raise; otherwise it should find b.c
    try:
        res = sv.search(None, "baz", regex=False)
    except RuntimeError:
        pytest.skip("rg not available; search requires ripgrep")
    else:
        assert any(r["path"].endswith("b.c") for r in res)


def test_get_source_with_linenos(tmp_project: Path):
    sv = SourceView(str(tmp_project))
    out = sv.get_source("a.c", start=1, end=2, include_linenos=True)
    assert isinstance(out["lines"], list)
    assert out["lines"][0]["lineno"] == 1
    assert out["lines"][0]["text"] == "int foo() { return 1; }"
    # code should contain the numbered prefix for both lines
    numbered_expected = (
        f"{str(1).rjust(6)}: int foo() {{ return 1; }}\n"
        f"{str(2).rjust(6)}: void bar() {{}}"
    )
    assert out["code"] == numbered_expected


def test_list_files(tmp_project: Path):
    sv = SourceView(str(tmp_project))
    files = sv.list_files(".", recursive=True)
    paths = {f["path"] for f in files}
    expected = {"a.c", os.path.join("sub", "b.c")}
    assert paths == expected


def test_path_traversal_blocked(tmp_project: Path):
    sv = SourceView(str(tmp_project))
    with pytest.raises(ValueError):
        sv.get_source("../etc/passwd")
