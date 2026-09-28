"""Does the suite run what it thinks it runs?

Found by accident: seven tests in test_ui_smoke.py were defined twice, a two-hundred-line block
pasted in a second time. Python rebinds the name, so pytest collects only the last definition —
seven tests had been silently dead, and every "N passed" for however long was N short of what
anybody believed. Nothing complains about it: not pytest, not a linter, not a passing run.

So this file checks the suite the way the suite checks the product.
"""

import ast
import collections
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
FILES = sorted(HERE.glob("test_*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_test_is_defined_twice(path):
    """A second definition of the same name silently replaces the first, and the first stops
    running. It still reads like a test, it is still in the file, and it proves nothing."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = [node.name for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith("test_")]
    twice = sorted(name for name, count in collections.Counter(names).items() if count > 1)
    assert twice == [], (
        f"{path.name} defines these more than once, so only the last of each runs: {twice}")


def test_no_two_files_claim_the_same_test_name():
    """Not fatal the way a duplicate inside one file is — pytest runs both — but it makes
    `-k thename` ambiguous and a failure report hard to place."""
    where = collections.defaultdict(list)
    for path in FILES:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test_"):
                where[node.name].append(path.name)
    shared = {name: files for name, files in where.items() if len(files) > 1}
    assert shared == {}, f"the same test name in more than one file: {shared}"


def test_every_test_file_holds_at_least_one_test():
    """A file of helpers named test_something contributes nothing and reads as coverage."""
    empty = []
    for path in FILES:
        if path.name == pathlib.Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test_") for n in tree.body):
            empty.append(path.name)
    assert empty == [], f"no tests in: {empty}"
