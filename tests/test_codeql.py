# tests/test_codeql_csv.py
import csv
import shutil
import subprocess
from pathlib import Path
import pytest

from infra.codeql_utils import CodeQLCpp


def _which_compiler():
    # prefer gcc, then clang
    for cc in ("gcc", "clang"):
        if shutil.which(cc):
            return cc
    return None


@pytest.fixture(scope="function")
def make_codeql_db(tmp_path: Path):
    """
    Create a minimal C source and a CodeQL C/C++ database by running a real compile command.
    """
    if not shutil.which("codeql"):
        pytest.skip("CodeQL CLI not found in PATH.")

    cc = _which_compiler()
    if not cc:
        pytest.skip("No C compiler (gcc/clang) found in PATH.")

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "hello.c").write_text(
        "#include <stdio.h>\n"
        "int add(int a, int b) { return a + b; }\n"
        "int main(void) { printf(\"%d\\n\", add(1,2)); return 0; }\n",
        encoding="utf-8",
    )

    db = tmp_path / "db"
    res = subprocess.run(
        [
            "codeql", "database", "create", str(db),
            "--language=cpp",
            f"--source-root={src}",
            "--command", f"{cc} -c hello.c",
        ],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        pytest.skip(f"Failed to create CodeQL DB: {res.stderr[:400]}")
    assert (db / "codeql-database.yml").exists()
    return str(db)


def test_cpp_query_csv(make_codeql_db):
    ql = CodeQLCpp(db_path=make_codeql_db)

    # simplest: list functions and their names; YOU must keep query valid with trailing ';'
    query = """
    import cpp
    from Function f
    select f, f.getName()
    """

    csv_text = ql.run_query_text_csv(query)

    # basic CSV assertions
    rows = list(csv.reader(csv_text.splitlines()))
    assert len(rows) >= 2  # header + at least one row

    # find a value 'add' in any cell (should appear in second column)
    flat = [cell for row in rows for cell in row]
    assert "add" in flat
