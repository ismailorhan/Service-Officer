"""The language setting, and whether a catalogue keeps up with the source.

The point of keying a catalogue by the English sentence is that completeness is *computable*:
walk the source for `t(...)`, and the set of keys is exact. So these are not "spot checks" —
they are the whole question, answered.
"""

import pytest

from core import config as cfg_mod
from core import i18n

from tools import strings as strings_tool


def test_english_is_what_an_unconfigured_install_reads():
    """Not the machine's locale. A server installed by an administrator in one country and
    read by an engineer in another has no single right answer, so the answer is the one
    everything is written in — and it is one setting away."""
    assert cfg_mod.Config().language == "en"
    assert i18n.DEFAULT == "en"


def test_an_unknown_language_reads_english_rather_than_failing():
    """A config edited by hand, or written by a newer build that has more languages."""
    try:
        assert i18n.use("kl") == "en"
        assert i18n.t("Watching only — no control.") == "Watching only — no control."
    finally:
        i18n.use("en")


def test_a_missing_translation_falls_back_to_the_english():
    """Which is the right failure. The other way round — an invented key with no entry —
    shows `machines.no_control` to a customer."""
    try:
        i18n.use("tr")
        assert i18n.t("A sentence nobody has translated yet") == \
            "A sentence nobody has translated yet"
    finally:
        i18n.use("en")


def test_a_template_with_the_wrong_placeholders_still_says_something(monkeypatch):
    """A catalogue mistake must not take a window down. The English is used instead, which is
    at least true."""
    monkeypatch.setattr(i18n, "_catalogue", {"Could not {action} {name}": "{eylem} yapılamadı"})
    monkeypatch.setattr(i18n, "_current", "tr")

    assert i18n.t("Could not {action} {name}", action="stop", name="AppEngine") == \
        "Could not stop AppEngine"


def test_the_language_is_this_computers_own_not_the_hubs():
    """Like the theme and auto-start. One person's language is not a property of the
    landscape, and adopting a hub's would hand every workstation whatever the server was
    installed in."""
    assert "language" in cfg_mod.LOCAL_TASTE
    assert "language" not in cfg_mod.LANDSCAPE

    hub = cfg_mod.Config(language="tr")
    mine = cfg_mod.Config(language="en")
    assert cfg_mod.merged(hub, mine).language == "en"


# ---------------------------------------------------------------------------
# how far the conversion has got
# ---------------------------------------------------------------------------
def test_every_wrapped_sentence_is_translated():
    """Whatever has been wrapped in `t()` must have a Turkish entry.

    This is what stops the conversion drifting: a sentence wrapped and not translated shows
    English inside an otherwise Turkish screen, which reads as broken rather than untranslated.
    """
    said = strings_tool.wrapped()
    gaps = i18n.missing("tr", said)

    assert gaps == [], (
        f"{len(gaps)} wrapped sentence(s) have no Turkish; first few: {gaps[:5]}")


def test_no_orphaned_translations():
    """An entry for a sentence no longer in the source is a translated line somebody will look
    for on screen and never find — and it hides that the English it belonged to changed."""
    orphans = i18n.stale("tr", strings_tool.wrapped())

    assert orphans == [], (
        f"{len(orphans)} Turkish entr(ies) match nothing in the source: {orphans[:5]}")


def test_the_conversion_does_not_go_backwards():
    """A ratchet, not a target. Every sentence still bare stays English whatever the setting
    says, so a mixed screen is the failure this counts down to zero.

    Lower this number as sentences are wrapped; it must never be raised. `python
    tools/strings.py` prints the current state, and `--todo tr` lists what is left.
    """
    STILL_BARE = 724

    loose = strings_tool.bare()
    assert len(loose) <= STILL_BARE, (
        f"{len(loose)} bare sentences, up from {STILL_BARE} — something on screen was added "
        "without t()")
