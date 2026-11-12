"""Recycling test, step 2: build the barcode subVI (Barcode.vi) through the LabVIEW Bridge, then run it.

Barcode (string) -> Actions (actions.ctl: Invalid, Bottle, Can lights), Delay Time (s), Value ($).
One string case per barcode; Default = invalid item. Connector: Barcode left; Actions, Delay Time, Value right.

Until the real start files are here, a stand-in controls/actions.ctl is created (cluster of 3 LEDs).
If the real actions.ctl is present, it is used as is.

Run:  python build_barcode.py      (LabVIEW running)
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bridge"))
from lvapi import LV, wire  # noqa: E402

ACTIONS = HERE / "controls" / "actions.ctl"
BARCODE = HERE / "Barcode.vi"
# barcode -> (Invalid, Bottle, Can), value $, delay s      (table from the test)
TABLE = {
    "2L B": ((False, True, False), 0.10, 2.0),
    "1L B": ((False, True, False), 0.05, 2.0),
    "500mL B": ((False, True, False), 0.05, 2.0),
    "16oz C": ((False, False, True), 0.05, 2.0),
}
INVALID = ((True, False, False), 0.00, 1.0)

t0 = time.time()
lv = LV()

# ---- stand-in actions.ctl (only if the start files haven't provided one) ----
if not ACTIONS.exists():
    ACTIONS.parent.mkdir(exist_ok=True)
    a = lv.new_ctl(ACTIONS, "Cluster (modern)", "Actions")
    for name in ("Invalid", "Bottle", "Can"):
        a.root.add("Round LED (modern)", name)
    a.save()
    print("created stand-in", ACTIONS)

# ---- Barcode.vi (rerunnable: an existing/in-memory copy is cleared and rebuilt) ----
try:
    vi = lv.open_vi(BARCODE)
    vi.diagram.clear()
    for c in vi.controls():
        c.delete()
except Exception:  # noqa: BLE001
    vi = lv.new_vi()

bc = vi.control("String Control (modern)", "Barcode", 30, 40)
act = lv.obj(lv.b.add_subvi(vi.panel.h, str(ACTIONS), 260, 30))
act.label = "Actions"
act.indicator = True
dly = vi.indicator("Numeric", "Delay Time (s)", 260, 150)
val = vi.indicator("Numeric", "Value ($)", 260, 210)

D = vi.diagram
cs = D.add("Case Structure", 200, 40)
wire(bc, cs.terms[0])                       # string selector
names = list(TABLE) + ["Default"]
while len(cs.frames) < len(names):
    cs.add_frame()
cs.names = [f'"{n}"' for n in TABLE] + ["Default"]
rows = list(TABLE.items()) + [("Default", INVALID)]
srcs = {"a": {}, "d": {}, "v": {}}
for name, (lights, value, delay) in rows:
    f = cs.frame(name)
    srcs["a"][name] = vi.typedef_const(ACTIONS, where=f, value=lights, x=30, y=20)
    srcs["d"][name] = f.const(float(delay), 30, 90)
    srcs["v"][name] = f.const(float(value), 30, 130)
cs.wire_out(act, srcs["a"])      # one tunnel per output, fed from every case
cs.wire_out(dly, srcs["d"])
cs.wire_out(val, srcs["v"])

vi.connector(left=[bc], right=[act, dly, val])
vi.todo("draw the icon (right-click icon > Edit Icon)", x=20, y=260)
vi.cleanup()
path = vi.save(BARCODE)
lv.flush_todos(HERE)
print(f"built {path} in {time.time() - t0:.1f} s")

# ---- run-check every row of the table ----
ok = True
for code, (lights, value, delay) in list(TABLE.items()) + [("tin can", INVALID)]:
    out = lv.b.run_vi(path, {"Barcode": code}, ["Actions", "Delay Time (s)", "Value ($)"])
    got = (tuple(bool(x) for x in out["Actions"]), round(out["Value ($)"], 2), out["Delay Time (s)"])
    good = got == (lights, value, delay)
    ok &= good
    print(f"{'ok ' if good else 'BAD'} {code!r:10} -> lights {got[0]}  ${got[1]:.2f}  {got[2]} s")
print("all cases pass" if ok else "SOME CASES FAILED")
