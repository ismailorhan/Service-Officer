"""Asking a machine or a service how it is — from wherever the watching happens.

Two buttons went to the wrong computer. **Test connection** and **Check now** both ran on
whatever machine the panel happened to be open on, and with a hub that is a workstation: it
proved that *this* desk can reach a server, which is a different question from the one the hub
answers every five seconds and the only one that matters. The Machines page even said "(from this
computer)" in the result — honest about the wrong answer rather than giving the right one.

So the work lives here, called either by the panel when it *is* the engine or by the hub over its
API, and the two cannot drift apart because there is one copy.

**Facts, not sentences.** Everything here returns numbers and booleans and, where a diagnosis can
only be made where the probe ran, the diagnosis. The wording is the panel's, in the panel's
language — see core/i18n.py. Moving "Services can be started and stopped." in here would move a
screenful of English into a module a Turkish reader never reaches.
"""

from __future__ import annotations

from . import applog

log = applog.get("probe")


def test_machine(record) -> dict:
    """Reach one machine and report what was found. Never raises.

    The connector is forgotten first: a held connection that went stale would answer from
    memory, and "test" has to mean *now*. Signing in is explicit for the same reason
    `reachable()` is not enough on its own — it answers yes or no for the poller, and "the
    password is wrong" and "the machine is off" need different things done about them.
    """
    from . import connectors

    found = {"reachable": False, "why": "", "signed_in": False,
             "control": False, "kill": False, "logs": False, "push": False,
             "abilities_why": "", "winrm": None}
    connectors.forget(record.name)
    try:
        conn = connectors.for_machine(record.name, record=record)
        sign_in = getattr(conn, "_sign_in", None)
        if callable(sign_in):
            sign_in()
            found["signed_in"] = True
        if not conn.reachable():
            found["why"] = _why_unreachable(record)
            return found
        can = conn.abilities()
    except RuntimeError as exc:
        # Ours, and already a sentence — the transport's own words. Passed through rather than
        # rewritten: "the host key does not match" is not something this layer can improve on.
        found["why"] = str(exc)
        return found
    except Exception as exc:
        found["why"] = f"{type(exc).__name__}: {exc}"
        return found

    found.update(reachable=True, control=bool(can.control), kill=bool(can.kill),
                 logs=bool(can.logs), push=bool(can.push),
                 abilities_why=getattr(can, "why", "") or "")
    found["winrm"] = _probe_winrm(record)
    return found


def _why_unreachable(record) -> str:
    """What to open, rather than only that it did not answer.

    Only answerable where the probe ran — a firewall diagnosis is about the path between two
    machines — which is why this sentence is a fact here and not wording the panel could invent.
    """
    if record.is_linux:
        return (f"{record.where()} did not answer over SSH. Check that the machine is on, "
                f"that port {record.port or 22} is open, and that the account, key or "
                f"password and the host key all match.")
    from . import scm_windows
    told = scm_windows.diagnose(record.address or record.name)
    return told or f"{record.where()} did not answer."


def _probe_winrm(record) -> dict:
    """Whether WinRM is usable, or None when the question does not apply.

    None rather than False for this computer and for Linux: "switched off" and "not a thing
    here" read the same in a boolean and differently to a person.
    """
    if record.is_local or record.is_linux:
        return None
    from . import secrets, winrm_windows

    user = record.username if record.auth == "password" else ""
    password = secrets.get(secrets.ref_for_machine(record.name)) if user else ""
    where = record.address or record.name
    winrm_windows.forget(where)
    answer = winrm_windows.probe(where, user, password)
    return {"ok": bool(answer.get("ok")), "why": answer.get("why", "") or ""}


def run_health(service) -> dict:
    """Run one service's health checks now and report each line.

    As the *engine* would run them, which is the point: a check that says "something answers on
    CTL052:54001" is asking whether the machine watching it can open that port, and a workstation
    is not that machine.
    """
    from . import control, health as health_mod

    ok, results = health_mod.run_all(service, control)
    return {"ok": bool(ok), "lines": [r.line() for r in results]}
