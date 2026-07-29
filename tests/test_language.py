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
    # 724 when the count was first taken, then raised twice on 2026-07-29 by the hub's own
    # port becoming changeable — protocol-level refusals in hub_server.py, which stay English
    # on purpose because a hub words them in *its* language and a client reads them in
    # another (see core/i18n.py). The eight sentences that landed on screen were wrapped and
    # translated instead, which is what this test is for: it failed, and the failing is the
    # mechanism working. Raising it must always be deliberate, and the reason belongs here.
    # 742 → 745 on 2026-07-29: three more sentences in app.py's startup guard and the
    # flyout's not-connected state. Two of the three landed on screen and were wrapped and
    # translated; the rest are log lines. Raising this is deliberate, and the reason belongs
    # here — see the two entries above.
    # 745 → 749 on 2026-07-29: the guards for a click while the hub is down.
    STILL_BARE = 749

    loose = strings_tool.bare()
    assert len(loose) <= STILL_BARE, (
        f"{len(loose)} bare sentences, up from {STILL_BARE} — something on screen was added "
        "without t()")


# ---------------------------------------------------------------------------
# where a person's own choices are kept
# ---------------------------------------------------------------------------
def test_a_display_choice_survives_a_restart_on_a_client(tmp_path, monkeypatch):
    """It did not. Theme, language and auto-start were read from services.json and, on a
    client, saved to the *hub* — so the client's own disk never recorded the choice and the
    next launch reverted it. Proved on 2026-07-29: picked Turkish and dark, both went to the
    hub, next launch read English and System.

    And services.json is the landscape — one hub's, read by every client of it — so a client
    saving its theme there wrote one person's eyesight into a shared file.
    """
    from core import local as local_mod
    from core import secrets

    monkeypatch.setattr(local_mod, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local_mod, "MACHINE_PATH", str(tmp_path / "machine.json"))
    monkeypatch.setattr(secrets, "USER_SECRETS_PATH", str(tmp_path / "user.dat"))

    fresh = local_mod.load()
    assert (fresh.language, fresh.theme) == ("en", "system")

    chosen = local_mod.load()
    chosen.language, chosen.theme, chosen.auto_start = "tr", "dark", False
    assert local_mod.save(chosen)

    again = local_mod.load()
    assert (again.language, again.theme, again.auto_start) == ("tr", "dark", False),         "the choice was not stored on this computer"


def test_an_older_config_hands_its_display_settings_over(tmp_path, monkeypatch):
    """A services.json written before the move carries a theme and an auto-start. Dropping
    them would silently reset a setting somebody had chosen."""
    from core import local as local_mod

    monkeypatch.setattr(local_mod, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local_mod, "MACHINE_PATH", str(tmp_path / "machine.json"))

    older = cfg_mod.Config(theme="dark", auto_start=False, language="tr")
    mine = local_mod.taste(older)

    assert (mine.theme, mine.auto_start, mine.language) == ("dark", False, "tr")
    # And it stuck, so the next launch does not have to ask the config again.
    assert local_mod.load().theme == "dark"


def test_a_choice_already_made_here_is_not_overwritten_by_the_config(tmp_path, monkeypatch):
    """The migration is once and one-way. A value in this file can only have got there by
    being set after the move, so it is the newer of the two."""
    from core import local as local_mod

    monkeypatch.setattr(local_mod, "PATH", str(tmp_path / "client.json"))
    monkeypatch.setattr(local_mod, "MACHINE_PATH", str(tmp_path / "machine.json"))

    mine = local_mod.load()
    mine.theme = "light"
    local_mod.save(mine)

    after = local_mod.taste(cfg_mod.Config(theme="dark"))
    assert after.theme == "light", "the config overwrote a choice made here"


def test_every_catalogue_is_named_in_the_build():
    """`core/i18n.py` loads a catalogue by name at run time, from the code in the settings.
    A dynamic import is invisible to PyInstaller: the build succeeded without it and the frozen
    app fell back to English with nothing but a line in its log — picking Türkçe did nothing at
    all, which is the worst kind of nothing.

    Checked in `build.bat`, not in the .spec files: those are deleted and regenerated by it, so
    the first version of this test guarded a build artefact and would have passed for ever
    while the shipped exe stayed English. Proved by reading the built archive's module list.
    """
    import pathlib

    script = (pathlib.Path(__file__).resolve().parent.parent / "build.bat")         .read_text(encoding="utf-8", errors="replace")
    wanted = [code for code, _name in i18n.LANGUAGES if code != i18n.DEFAULT]
    assert wanted, "no language but English, so nothing to bundle"

    for code in wanted:
        # Twice: the tray application and the hub are two builds.
        assert script.count(f"--hidden-import=core.translations.{code}") == 2, (
            f"core.translations.{code} is not named for both builds; a language picked in "
            "the frozen app would silently read English")


def test_a_catalogue_module_exists_for_every_language():
    """`missing()` and `stale()` answer "" for a language with no module at all, so a
    forgotten file reads as a complete translation."""
    import pathlib

    here = pathlib.Path(__file__).resolve().parent.parent / "core" / "translations"
    for code, name in i18n.LANGUAGES:
        if code == i18n.DEFAULT:
            continue
        assert (here / f"{code}.py").exists(), f"no catalogue module for {name} ({code})"
