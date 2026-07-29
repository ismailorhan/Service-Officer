"""End to end: a client that is *not* on the hub's machine.

The whole point. Everything found today was hidden by hub and client sharing one ProgramData,
so this run gives the client its own empty one — which is what a fresh workstation has — and
then drives the real widgets against a real hub over a real socket.

What it checks, in the order a person would notice it:

 1. the panel lists the hub's services, not this disk's (which has none)
 2. the machine rows say which computer they are, and carry the right chips
 3. the History page shows the hub's timeline
 4. "Add services" is offered the hub's services
 5. a Stop from this panel reaches the service and the busy label clears
 6. the hub's schedule runs, and its outcome arrives here
 7. this computer's own theme survives adopting the hub's landscape
"""
import os
import pathlib
import sys
import tempfile
import time

REPO = r"C:\Users\ismail.orhan\Projects\Service-Officer"
sys.path.insert(0, REPO)

# --- a workstation with nothing of its own --------------------------------
CLIENT_DIR = tempfile.mkdtemp(prefix="so-workstation-")
HUB_DIR = tempfile.mkdtemp(prefix="so-hubmachine-")

from core import config as cfg_mod        # noqa: E402
from core import db, history              # noqa: E402


# A wall-clock guard, so a hang says *where* rather than timing out silently.
import faulthandler, threading
faulthandler.enable()


def _bail():
    print("!! still running after 120 s - stacks below", flush=True)
    faulthandler.dump_traceback()
    os._exit(2)


threading.Timer(120, _bail).start()

failures = []


def check(what: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  - {detail}" if detail else ""),
          flush=True)
    if not ok:
        failures.append(what)


# --- the hub's machine ----------------------------------------------------
from core import engine as engine_mod, hub_auth, hub_server, state as st  # noqa: E402

STATES = {"AppEngine": st.RUNNING, "WMSServer": st.RUNNING, "LabelService": st.RUNNING}
# The same shape control.list_all_services really returns — {name, display, status}. A
# stand-in missing a key is a bug in the stand-in, and the picker crashed on it.
INSTALLED = [{"name": n, "display": f"CompuTec {n}", "status": "Running"} for n in
             ("AppEngine", "WMSServer", "LabelService", "GatewayService")]

engine_mod.control.query_status = lambda name, machine="": STATES.get(name, st.UNKNOWN)
engine_mod.control.start_type = lambda name, machine="": "Automatic"
engine_mod.control.stop_service = lambda name, machine="": STATES.update({name: st.STOPPED})
engine_mod.control.start_service = lambda name, machine="": STATES.update({name: st.RUNNING})
engine_mod.control.restart_service = lambda name, machine="": STATES.update({name: st.RUNNING})
hub_auth.check = lambda token: "workstation7"
hub_auth.note_seen = lambda name, host="": None

from core import control                  # noqa: E402
control.list_all_services = lambda machine="", record=None: list(INSTALLED)
control.host_name = lambda: "CTL052"       # the hub's machine, as the hub sees itself

history.HISTORY_PATH = os.path.join(HUB_DIR, "history.db")

hub_cfg = cfg_mod.Config(
    machines=[cfg_mod.Machine(),
              cfg_mod.Machine(name="sc-sql", address="10.77.3.112", kind="windows")],
    services=[cfg_mod.Service(name="AppEngine", label="CompuTec AppEngine"),
              cfg_mod.Service(name="WMSServer", label="CompuTec WMS Server"),
              cfg_mod.Service(name="LabelService", label="CompuTec Label Service"),
              cfg_mod.Service(name="B1ServerTools64", label="SAP B1 Server Tools",
                              machine="sc-sql")],
    triggers=[cfg_mod.Trigger(name="nightly", action="service", service="WMSServer",
                              service_action="stop")],
    theme="dark", auto_start=False)
holder = {"cfg": hub_cfg}

engine = engine_mod.Engine(lambda: holder["cfg"], store=st.Store(),
                           on_config_saved=lambda c: holder.update(cfg=c))
engine.prime_states()
server = hub_server.HubServer(engine, host="127.0.0.1", port=0, insecure=True)
server.start()
print(f"hub on {server.url}, {len(hub_cfg.services)} services\n")

# --- the workstation -----------------------------------------------------
cfg_mod.APP_DIR = CLIENT_DIR
cfg_mod.CONFIG_PATH = os.path.join(CLIENT_DIR, "services.json")
history.HISTORY_PATH = os.path.join(CLIENT_DIR, "history.db")   # empty, as on a real one

from core import hub_client                # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel   # noqa: E402
from ui import panel as panel_mod, theme    # noqa: E402

from PySide6.QtWidgets import QMessageBox     # noqa: E402

qt = QApplication(sys.argv)
complaints = []
QMessageBox.warning = staticmethod(
    lambda *a, **k: complaints.append(a[2] if len(a) > 2 else a))
QMessageBox.information = staticmethod(lambda *a, **k: None)
theme.set_mode("light")
qt.setStyleSheet(theme.sheet())

client = hub_client.HubClient(server.url, "good")
client.start()
assert client.wait_for(lambda: client.connected, timeout=15), "the client never connected"

# What app.py does: adopt the landscape, keep this computer's taste.
mine = cfg_mod.load()
mine.theme = "light"
landscape, _etag = client.config()
adopted = cfg_mod.merged(landscape, mine)

print("1. the landscape")
check("this workstation's own config is empty", len(mine.services) == 0,
      f"{len(mine.services)} services on its disk")
check("the panel lists the hub's services", len(adopted.services) == 4,
      f"{[s.name for s in adopted.services]}")
check("this computer's theme survived", adopted.theme == "light",
      f"theme={adopted.theme!r} (the hub's is {landscape.theme!r})")
check("the hub's auto-start did not come with it", adopted.auto_start is True)

# The workstation is a different computer from here on.
control.host_name = lambda: "WORKSTATION7"
win = panel_mod.MainPanel(adopted, store=client.store, hub=lambda: client)
win.resize(1010, 700)
win.show()
qt.processEvents()

print("\n2. the machine rows")
win._select(win.machines_page, win._buttons_by_name["machines"])
win.machines_page.refresh()
win.grab()
row = win.machines_page.list.itemWidget(win.machines_page.list.item(0))
title = row.findChildren(QLabel)[0].text()
chips = [lb.text() for lb in row.findChildren(QLabel)
         if lb.text() in ("This PC", "Hub")]
check("the hub's row is named after the hub, not this workstation",
      "CTL052" in title and "WORKSTATION7" not in title, title)
check("it carries the Hub chip and not This PC", chips == ["Hub"], str(chips))

print("\n3. the History page")
win._select(win.history_page, win._buttons_by_name["history"])
engine.act("stop", "LabelService", actor="somebody at the hub")
time.sleep(1.2)
win.history_page.reload()
for _ in range(150):
    if not win.history_page._fetching and win.history_page._rows_key is not None:
        break
    qt.processEvents()
    time.sleep(0.02)
qt.processEvents()
rows = win.history_page._current_rows()
check("the hub's timeline arrives here", len(rows) > 0, f"{len(rows)} rows")
check("and it is about the hub's services",
      any(r.get("service") == "LabelService" for r in rows),
      str(sorted({r.get("service", "") for r in rows})))
# (A "the workstation's own db is empty" check belongs on two real machines: one process
#  has one module-level history path, so the hub's writes land in it here.)

print("\n4. Add services")
from ui.pages.base import ServicePicker      # noqa: E402


picker = ServicePicker(set(), None, machine="", hub=client)
picker._load()
for _ in range(150):
    if picker._all:
        break
    qt.processEvents()
    time.sleep(0.02)
got = sorted(s["name"] for s in picker._all)
check("the picker is offered the hub's services", got == sorted(s["name"] for s in INSTALLED),
      str(got))
check("including one that is installed but not configured", "GatewayService" in got,
      "so the list can only have come from the hub, not from a config")
picker.deleteLater()

print("\n5. an action from here")
before = STATES["AppEngine"]
action_events = []
client._on_event = action_events.append
client.act("stop", "AppEngine", actor="WORKSTATION7\\ismail")
ok = client.wait_for(lambda: any(e.get("kind") == "action" for e in action_events),
                     timeout=15)
check("the stop reached the service", STATES["AppEngine"] == st.STOPPED,
      f"{before} -> {STATES['AppEngine']}")
check("and its completion came back, so a busy label can clear", ok,
      str([e.get("kind") for e in action_events]))

print("\n6. the hub's schedule")
fired = []
client._on_event = fired.append
engine.run_trigger("nightly", actor="the schedule")
got = client.wait_for(lambda: any(e.get("kind") == "trigger" for e in fired), timeout=15)
check("the trigger ran on the hub", STATES["WMSServer"] == st.STOPPED,
      f"WMSServer is {STATES['WMSServer']}")
check("and this panel was told how it went", got,
      str([e.get("outcome") for e in fired if e.get("kind") == "trigger"]))

print("\n7. health")
health = []
client._on_event = health.append
engine._call(engine._on_health, service="AppEngine", machine="", verdict=st.UNHEALTHY,
             detail="the port never answered")
seen = client.wait_for(lambda: client.store.health_of("AppEngine") == st.UNHEALTHY,
                       timeout=10)
check("a health verdict reaches this panel", seen,
      client.store.health_of("AppEngine"))

win.grab().save(os.path.join(
    r"C:\Users\ISMAIL~1.ORH\AppData\Local\Temp\claude\C--Users-ismail-orhan-Projects\512bb2dc-fd1d-4bba-8f91-763e6c2885bd\scratchpad",
    "e2e_client.png"))

check("nothing complained in a dialog", not complaints, "; ".join(map(str, complaints)))

print()
if failures:
    print(f"{len(failures)} FAILURE(S): " + "; ".join(failures))
else:
    print("all checks passed")
win.close()
client.stop()
server.stop()
# _exit, not sys.exit: not all of the hub's request threads are daemons and interpreter
# shutdown waits for them, which turns a finished run into a hang.
sys.stdout.flush()
os._exit(1 if failures else 0)
