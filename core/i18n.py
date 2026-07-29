"""What the product says, in the language somebody reads.

**The English sentence is the key.** `t("Watching only — no control.")` rather than
`t("machines.no_control")`. Three reasons, all of them about a codebase this one's size being
retrofitted rather than written for two languages from the start:

* the code stays readable. A file full of `t("history.windows_levels_hint")` cannot be
  reviewed — you have to hold the catalogue open beside it to know what a page says.
* nothing has to be named. Nine hundred and seventy-five invented keys is nine hundred and
  seventy-five chances to name one badly, and a badly named key outlives the sentence.
* a missing translation falls back to the English, which is the right failure. The other way
  round — a key with no entry — shows `machines.no_control` to a customer.

The cost is that editing an English sentence orphans its translation. That is what
`missing()` is for, and a test uses it.

Sentences with values in them are templates: `t("Could not {action} {name}", action=…,
name=…)`. Formatting happens here so a translation with a wrong placeholder cannot take a
window down — it falls back to the English, which at least says something true.
"""

from __future__ import annotations

#: The languages there are. The code is what `Config.language` holds and what a catalogue
#: module is named after.
LANGUAGES = (("en", "English"), ("tr", "Türkçe"))
DEFAULT = "en"

_current = DEFAULT
_catalogue: dict = {}


def use(code: str) -> str:
    """Read everything in this language from now on. Returns the code actually used.

    An unknown code is English rather than an error: a config file edited by hand, or one
    written by a newer build, must not stop the app from starting.
    """
    global _current, _catalogue
    code = (code or DEFAULT).strip().lower()
    if code not in dict(LANGUAGES):
        code = DEFAULT
    _current = code
    _catalogue = _load(code)
    return code


def current() -> str:
    return _current


def _load(code: str) -> dict:
    """That language's catalogue, or {} for English and for anything unreadable."""
    if code == DEFAULT:
        return {}
    try:
        module = __import__(f"core.translations.{code}", fromlist=["WORDS"])
        return dict(getattr(module, "WORDS", {}) or {})
    except Exception:
        # Not fatal, and not silent either: a missing catalogue means English, and English
        # is a working product.
        from . import applog
        applog.get("i18n").warning("no catalogue for %r; reading English", code)
        return {}


def t(text: str, **fields) -> str:
    """This sentence, in the current language.

    `fields` are substituted after the lookup, so a template is translated as a whole
    sentence — word order differs between languages and a sentence assembled from pieces
    cannot be reordered.
    """
    said = _catalogue.get(text, text)
    if not fields:
        return said
    try:
        return said.format(**fields)
    except (KeyError, IndexError, ValueError):
        # A translation whose placeholders do not match the English. Reported once and then
        # the English is used: a wrong sentence is better than a traceback, and this is a
        # catalogue mistake rather than anything the reader did.
        from . import applog
        applog.get("i18n").warning("placeholders do not match for %r", text[:60])
        try:
            return text.format(**fields)
        except Exception:
            return text


def missing(code: str, texts) -> list:
    """Which of these sentences that language has no entry for.

    Editing an English sentence orphans its translation, which is the price of using the
    sentence as the key. A test walks the source for `t(...)` calls and asks this.
    """
    words = _load(code)
    return sorted({s for s in texts if s and s not in words})


def stale(code: str, texts) -> list:
    """Entries in that catalogue for sentences no longer anywhere in the source.

    An orphan is not harmless: it is a translated sentence somebody will look for on screen
    and never find, and it hides the fact that the English it belonged to changed.
    """
    known = set(texts)
    return sorted(s for s in _load(code) if s not in known)


use(DEFAULT)
