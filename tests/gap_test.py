"""Gap test: typedef edits, string case subVI with connector pane, Express VI, user TODO notes."""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
from lvapi import LV, wire  # noqa: E402

T0 = time.time()
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scratch", "gap")
SRC = os.path.join(os.path.dirname(__file__), "..", "Projects", "Disturb Project", "controls")
os.makedirs(D, exist_ok=True)
# LabVIEW keeps files in memory by name, so every run uses new names
R = str(int(T0) % 100000)
STATE, DATA, BARCODE = f"GState{R}.ctl", f"GData{R}.ctl", f"GBarcode{R}.vi"
shutil.copy(os.path.join(SRC, "State.ctl"), os.path.join(D, STATE))
shutil.copy(os.path.join(SRC, "Data.ctl"), os.path.join(D, DATA))
lv = LV()

# 1. typedefs
st = lv.open_vi(os.path.join(D, STATE))
st.controls()[0].items = ["Initialize", "Read", "Sort", "Receipt", "Exit"]
st.save()
dt = lv.open_vi(os.path.join(D, DATA))
cl = dt.controls()[0]
for k in cl.children():
    k.delete()
cl.add("Push Button", "Reset")
cl.add("Numeric Control (modern)", "Delay Time (s)")
cl.add("Numeric Control (modern)", "Total ($)")
cl.add(os.path.join(D, STATE), "Actions")  # stand-in for actions.ctl
dt.save()
print("typedefs", [(c.cls, c.label) for c in cl.children()], round(time.time() - T0, 1))

# 2. barcode-style subVI
vi = lv.new_vi()
bc = vi.control("String Control (modern)", "Barcode", 30, 40)
val = vi.indicator("Numeric", "Value", 300, 40)
dly = vi.indicator("Numeric", "Delay Time", 300, 110)
cs = vi.diagram.add("Case Structure", 250, 60)
wire(bc, cs.terms[0])  # case selector
print("frames after string wire", cs.names)
table = [("2L B", 0.10, 2.0), ("1L B", 0.05, 2.0), ("500mL B", 0.05, 2.0), ("16oz C", 0.05, 2.0)]
while len(cs.frames) < len(table) + 1:
    cs.add_frame()
cs.names = [f'"{b}"' for b, _, _ in table] + ["Default"]
print("names", cs.names)
for i, (b, v, d) in enumerate(table + [("", 0.0, 1.0)]):
    f = cs.frames[i]
    cv = f.const(float(v), 30, 20)
    cd = f.const(float(d), 30, 60)
    wire(cv, val)
    wire(cd, dly)
el = vi.diagram.express("Elapsed Time", 100, 300)
print("express", el.cls, el.term_names()[:3])
vi.todo("draw the icon (Edit Icon)", x=20, y=400)
v2 = lv.new_vi()  # side checks on their own VI so the barcode VI stays runnable
cs2 = v2.diagram.add("Case Structure", 200, 100)
btn = v2.control("Push Button", "Sort", 30, 200)
btn.mechanical_action = "latch when released"
assert btn.mechanical_action == 4, btn.mechanical_action
tc = v2.typedef_const(os.path.join(D, DATA), where=cs2.frames[0], value=(True, 2.0, 0.1, 1), x=20, y=20)
assert tc.get("Value", "Constant")[0] is True and tc.owner.h == cs2.frames[0].h
print("latch + typedef const ok")
vi.connector(left=[bc], right=[val, dly])
p = vi.save(os.path.join(D, BARCODE))
print("saved", p, round(time.time() - T0, 1))
print("todo file", lv.flush_todos(D))

# 3. run it
for b in ["2L B", "16oz C", "tin can"]:
    print(b, lv.b.run_vi(p, {"Barcode": b}, ["Value", "Delay Time"]))
print("total", round(time.time() - T0, 1), "s")
