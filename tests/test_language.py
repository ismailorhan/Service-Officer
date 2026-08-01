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
    # Both halves: a string translated where it is drawn — a page heading, a nav label — has
    # an entry and no `t("…")` call site, and comparing against the wrapped ones alone called
    # eighteen legitimate entries stale.
    # Against every literal in the source, not only the ones that look like sentences: a
    # one-word nav label such as "Clients" is translated where it is drawn and `bare()`
    # deliberately skips anything without a space. An entry matching no literal at all is the
    # only real orphan.
    #
    # This test was toothless until 2026-07-30. `everything()` walked `core/`, which contains
    # the catalogue, so every key matched *itself* and nothing could ever be orphaned. With the
    # catalogue excluded it immediately found the Categories page's description: reworded in
    # English months before, still the old wording here, so the heading was shown in English to
    # anybody reading in Turkish. That is exactly the failure this test is named for.
    orphans = i18n.stale("tr", strings_tool.everything())

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
    # 749 -> 747 on 2026-07-30: two paragraphs on the service detail tabs became notes on an
    # InfoDot, which is one string where there were two labels.
    # 747 -> 739 on 2026-07-30: the navigation, every page heading and the service detail's
    # tabs, translated where they are drawn.
    # 739 -> 703 on 2026-07-30: no sentence was translated for this one. Thirty-six of the
    # thirty-nine were never sentences — pieces of the stylesheet f-string in ui/theme.py,
    # split at every `{colour}`, which the tool counted as prose whenever a piece happened to
    # hold a colon and no brace. Adding one CSS rule pushed the number up by one and failed
    # this test, which is how it was found. `bare()` now judges an f-string whole. So this is a
    # lowering, not a raising: the count was never measuring those.
    # 703 -> 681 on 2026-07-30: the Turkish was being counted as untranslated English. The
    # catalogue lives under `core/`, which `bare()` walked, so every *value* in tr.py was read
    # as a sentence nobody had translated — the keys are filtered out by having entries, the
    # translations are not. Translating four things raised the count by four, which is how it
    # surfaced. So this number had been inflated by every line ever translated, and the honest
    # figure is smaller. Four Hub notes were also wrapped and translated in the same change.
    # 681 -> 685 on 2026-07-30: the hub's update endpoints. Four protocol refusals in
    # hub_server.py — "not now — a stack is running", "there is no newer release to install",
    # "the download failed". They stay English for the reason the entries above give: a hub
    # words its own refusals and a client may be reading in another language. The sentence the
    # *client* composes about a version mismatch is on screen in that person's language, and it
    # was wrapped and translated rather than counted here.
    # 685 -> 691 on 2026-07-30: the hub serving its installer to clients. Six more of the same
    # kind as the entry above — a hub's own refusals, worded by the hub and read by somebody who
    # may be reading in another language ("this hub has no installer for the release it is
    # running", "the download passed 300 MB", "administrator rights were refused"). Every
    # sentence the *panel* composes for this feature was wrapped and translated.
    STILL_BARE = 691

    # What will stay English whatever the setting says: bare *and* with no catalogue entry.
    # `bare()` alone overcounts, because a heading translated where it is drawn is bare at its
    # call site and translated all the same.
    loose = strings_tool.untranslated("tr")
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

    Checked in the build *scripts*, not in the .spec files: those are deleted and regenerated,
    so the first version of this test guarded a build artefact and would have passed for ever
    while the shipped exe stayed English. Proved by reading the built archive's module list.

    And in **every** script that builds the exe, not only the one used by hand. The second
    version of this test read `build.bat` alone — which had been fixed — while
    `.github/workflows/release.yml` named the catalogue nowhere at all. So the local build was
    correct, the test was green, and every release the pipeline published shipped without any
    Turkish. Found by grepping the two files for the same string and getting 2 and 0.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    wanted = [code for code, _name in i18n.LANGUAGES if code != i18n.DEFAULT]
    assert wanted, "no language but English, so nothing to bundle"

    for where in ("build.bat", ".github/workflows/release.yml"):
        script = (root / where).read_text(encoding="utf-8", errors="replace")
        for code in wanted:
            # Twice: the tray application and the hub are two builds.
            assert script.count(f"--hidden-import=core.translations.{code}") == 2, (
                f"{where} does not name core.translations.{code} for both builds; a language "
                "picked in the frozen app would silently read English")


def test_a_catalogue_module_exists_for_every_language():
    """`missing()` and `stale()` answer "" for a language with no module at all, so a
    forgotten file reads as a complete translation."""
    import pathlib

    here = pathlib.Path(__file__).resolve().parent.parent / "core" / "translations"
    for code, name in i18n.LANGUAGES:
        if code == i18n.DEFAULT:
            continue
        assert (here / f"{code}.py").exists(), f"no catalogue module for {name} ({code})"


def test_no_catalogue_entry_is_written_twice():
    """A duplicate key is silent: Python keeps the last one, so the first translation is dead
    code that somebody will edit and wonder why nothing changed. Happened within an hour of the
    catalogue existing — "Dashboard" was in it twice, as "Panosu" and then as "Pano"."""
    import ast
    import pathlib

    here = pathlib.Path(__file__).resolve().parent.parent / "core" / "translations"
    for code, name in i18n.LANGUAGES:
        if code == i18n.DEFAULT:
            continue
        tree = ast.parse((here / f"{code}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [ast.literal_eval(k) for k in node.keys if k is not None]
            twice = sorted({k for k in keys if keys.count(k) > 1})
            assert twice == [], f"{name}: written twice — {twice}"
