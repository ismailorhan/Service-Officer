"""Every sentence the app can say, and which of them a language is missing.

The point of a catalogue keyed by the English sentence is that this is *computable*: walk the
source for `t(...)` calls, and the set of keys is exact. So "is the Turkish complete" is a
number rather than an opinion, and a test can hold it at zero.

    python tools/strings.py            what is missing from each language
    python tools/strings.py --list     every sentence, for a translator
    python tools/strings.py --todo tr  only what Turkish still needs

It also finds the other half of the job: literals that are still *bare* — a sentence on
screen that nobody wrapped in `t()` and which therefore stays English whatever the setting
says. That number going to zero is what "all of it" means.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: Where a sentence can be said. `tools` and `tests` are not: nobody reads them.
LOOKED_AT = ("ui", "core", "app.py", "hub.py")
#: Short strings are labels like "OK" that read the same, and anything without a space is a
#: name, a key or a path. The same test both halves use, so the two counts are comparable.
MIN_LENGTH = 3


def _files():
    for where in LOOKED_AT:
        path = ROOT / where
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def _text_of(node) -> str:
    """The sentence a node says, or "" — a plain literal, or the fixed parts of an f-string.

    An f-string cannot be a key: its pieces are joined at run time. So one is only counted
    when it is *inside* a t() call, where the template is the literal argument and the values
    are keywords.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _is_t_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id == "t"
    return isinstance(fn, ast.Attribute) and fn.attr == "t"


def wrapped() -> dict:
    """{sentence: [where]} for every t("…") in the source."""
    found: dict = {}
    for path in _files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not _is_t_call(node) or not node.args:
                continue
            said = _text_of(node.args[0])
            if said:
                found.setdefault(said, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def bare() -> dict:
    """{sentence: [where]} for literals that look like something a person reads and are not
    inside a t() call.

    Deliberately generous about what counts, and deliberately blind to a few shapes that are
    never on screen: a docstring, a logging call, and anything that is plainly a key or a
    stylesheet.
    """
    found: dict = {}
    for path in _files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        inside_t, inside_log = set(), set()
        for node in ast.walk(tree):
            if _is_t_call(node):
                for child in ast.walk(node):
                    inside_t.add(id(child))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("debug", "info", "warning", "error",
                                           "exception", "get", "setObjectName",
                                           "setProperty", "setStyleSheet"):
                for child in ast.walk(node):
                    inside_log.add(id(child))
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if id(node) in inside_t or id(node) in inside_log or id(node) in docstrings:
                continue
            said = _text_of(node).strip()
            if len(said) < MIN_LENGTH or " " not in said:
                continue
            if said.startswith(("#", "<", "{", "/", "\\")) or "://" in said:
                continue
            if ":" in said and "{" in said:          # a stylesheet fragment
                continue
            found.setdefault(said, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def everything() -> set:
    """Every string literal in the looked-at files, with no judgement about whether it is a
    sentence.

    Only for the orphan check. `bare()` deliberately skips anything without a space, because a
    key or a path is not prose — but a one-word nav label like "Clients" is prose, is
    translated where it is drawn, and would otherwise be reported as an entry matching nothing.
    An entry that matches no literal *at all* is the only real orphan.
    """
    found = set()
    for path in _files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            said = _text_of(node)
            if said:
                found.add(said)
    return found


def untranslated(code: str = "tr") -> dict:
    """{sentence: [where]} for prose that will stay English whatever the setting says.

    Bare *and* with no catalogue entry. A string can be translated without being wrapped where
    it is written — a page heading and a nav label are translated where they are drawn, which
    is one edit for every heading in the product — so "bare" alone overcounts. One definition,
    used by this tool and by the test that holds the number down, so the two cannot drift.
    """
    sys.path.insert(0, str(ROOT))
    from core import i18n

    loose = bare()
    return {s: where for s, where in loose.items() if i18n.missing(code, [s])}


def main(argv) -> int:
    sys.path.insert(0, str(ROOT))
    from core import i18n

    said = wrapped()
    loose = bare()

    if "--list" in argv:
        for sentence in sorted(said):
            print(sentence)
        return 0
    if "--todo" in argv:
        code = argv[argv.index("--todo") + 1]
        for sentence in i18n.missing(code, said):
            print(sentence)
        return 0

    # A literal can be translated without being wrapped where it is written: `_Page(title)`
    # and the navigation's labels are translated where they are *drawn*, which is one edit for
    # every heading in the product. The tool cannot see that from the call site, so a bare
    # string the catalogue has an entry for counts as done — because something translates it.
    still = untranslated("tr")
    covered = len(loose) - len(still)

    total = len(said) + len(loose)
    done = len(said) + covered
    print(f"{len(said)} wrapped in t(), {covered} translated where they are drawn, "
          f"{len(still)} still bare")
    print(f"   {done * 100 // max(1, total)}% of {total}")
    loose = still
    for code, name in i18n.LANGUAGES:
        if code == i18n.DEFAULT:
            continue
        gaps = i18n.missing(code, said)
        # Against both halves: an entry for a string translated where it is *drawn* is not an
        # orphan, and comparing only against the wrapped ones called eighteen of them stale.
        orphans = i18n.stale(code, everything())
        print(f"  {name}: {len(said) - len(gaps)}/{len(said)} translated"
              + (f", {len(gaps)} missing" if gaps else "")
              + (f", {len(orphans)} orphaned" if orphans else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
