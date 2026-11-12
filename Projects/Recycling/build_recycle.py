"""Recycling test: build the main state machine (Recycle.vi) and its file I/O through the LabVIEW Bridge.

No start files yet, so this builds from scratch with its own typedefs (unique names so they can't clash
with the real start files in memory):
    controls/Recycle State.ctl  Initialize, Read, Sort, Receipt, Exit
    controls/Recycle Data.ctl   Reset, Delay Time (s), Total ($), Actions (actions.ctl)
Needs Barcode.vi + controls/actions.ctl  ->  run build_barcode.py first.

File I/O (test step 6): Open/Create/Replace (replace or create) before the loop, refnum and error on
shift registers through every state, header line in Initialize, one line per Receipt, Close File +
Simple Error Handler after the loop.

Run:  python build_recycle.py        (LabVIEW running)
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bridge"))
from lvapi import LV, wire  # noqa: E402

CTL = HERE / "controls"
ACTIONS, BARCODE, MAIN = CTL / "actions.ctl", HERE / "Barcode.vi", HERE / "Recycle.vi"
STATES = ["Initialize", "Read", "Sort", "Receipt", "Exit"]
S = {n: i for i, n in enumerate(STATES)}
BARCODES = ["2L B", "1L B", "500mL B", "16oz C", "tin can"]
DATA_FILE = "recycling data.txt"
for need in (ACTIONS, BARCODE):
    if not need.exists():
        sys.exit(f"missing {need.name}: run build_barcode.py first")

t0 = time.time()
lv = LV()
LVDIR = Path(lv.b.lv.app.get("ApplicationDirectory"))
SIMPLE_ERR = LVDIR / "vi.lib" / "Utility" / "error.llb" / "Simple Error Handler.vi"

# ---------------- typedefs (rerunnable: existing files are edited in place) ----------------
p = CTL / "Recycle State.ctl"
st = lv.open_vi(p) if p.exists() else lv.new_ctl(p, "Enum (modern)", "State")
st.controls()[0].items = STATES
st.save()
STATE_CTL = p

p = CTL / "Recycle Data.ctl"
if not p.exists():
    dt = lv.new_ctl(p, "Cluster (modern)", "Data")
    cl = dt.root
    cl.add("Push Button", "Reset")
    cl.add("Numeric Control (modern)", "Delay Time (s)")
    cl.add("Numeric Control (modern)", "Total ($)")
    cl.add(ACTIONS, "Actions")
    dt.save()
DATA_CTL = p

# ---------------- front panel ----------------
try:  # rebuild in place (also works when the file is gone but LabVIEW still holds it)
    vi = lv.open_vi(MAIN)
    vi.diagram.clear()
    for c in vi.controls():
        c.delete()
except Exception:  # noqa: BLE001
    vi = lv.new_vi()

combo = vi.control("Combo Box (modern)", "Barcode", 30, 30)
combo["Strings []"] = tuple(BARCODES)
btn = {}
for i, name in enumerate(["Sort", "Receipt", "Exit"]):
    b = vi.control("OK Button (modern)", name, 30, 100 + 60 * i)
    b.wrap(b.get("Boolean Text", "Boolean"), "Text").set("Text", name, "Text")
    b.mechanical_action = "latch when released"
    btn[name] = b
total_ind = vi.indicator("Numeric", "Current Total ($)", 250, 30)
lights = lv.obj(lv.b.add_subvi(vi.panel.h, str(ACTIONS), 250, 100))
lights.label = "Actions"
lights.indicator = True

# ---------------- before the loop: open the file ----------------
D = vi.diagram
cvp = D.add("Current VI's Path", 20, 300)
strip = D.add("Strip Path", 120, 300)
bpath = D.add("Build Path", 220, 300)
opn = D.add("Open/Create/Replace File", 340, 300)
wire(cvp.out(), strip.inp("path"))
wire(strip.out("stripped path"), bpath.inp("base path"))
wire(D.const(DATA_FILE, 120, 360), bpath.inp("name"))
wire(bpath.out(), opn.inp("file path"))
lv.b.set_constant(opn.inp("operation").constant().h, 4, "i32")   # 4 = replace or create

# ---------------- loop + shift registers ----------------
loop = D.add("while", 520, 40)
loop.resize(1200, 620)
sr_state = loop.add_shift_register(60)
sr_data = loop.add_shift_register(120)
sr_file = loop.add_shift_register(180)
sr_err = loop.add_shift_register(240)
wire(vi.typedef_const(STATE_CTL, value=S["Initialize"], x=420, y=90), sr_state.init)
wire(vi.typedef_const(DATA_CTL, x=420, y=150), sr_data.init)
wire(opn.out("refnum out"), sr_file.init)
wire(opn.out("error out"), sr_err.init)

# ---------------- after the loop: close the file, report errors ----------------
cls = D.add("Close File", 1780, 300)
wire(sr_file.final, cls.inp("refnum"))
wire(sr_err.final, cls.inp("error in"))
seh = D.subvi(SIMPLE_ERR, 1900, 300)
wire(cls.out("error out"), seh.inp("error in"))

# ---------------- case structure ----------------
L = loop.frames[0]
cs = L.add("Case Structure", 150, 40)
wire(sr_state.read, cs.terms[0])
while len(cs.frames) < len(STATES):
    cs.add_frame()
cs.names = [f'"{n}"' for n in STATES]


def bundle(f, src, names, x, y, tunnel=False):
    """Bundle By Name on `src` with the given element names; returns (node, tunnel or None)."""
    bb = f.add("Bundle By Name", x, y)
    t = cs.new_tunnel(lambda: wire(src, bb.inp("input cluster"))) if tunnel else wire(src, bb.inp("input cluster"))
    bb["Input Count"] = len(names)
    bb["Element Names[]"] = tuple(names)
    bb.refresh()
    return bb, t


def unbundle(f, src, names, x, y):
    ub = f.add("Unbundle By Name", x, y)
    wire(src, ub.inp("input cluster"))
    ub["Output Count"] = len(names)
    ub["Element Names[]"] = tuple(names)
    ub.refresh()
    return ub


def state_const(f, name, x, y):
    return vi.typedef_const(STATE_CTL, where=f, value=S[name], x=x, y=y)


# ---- Initialize: header line; creates every tunnel the other states reuse ----
f = cs.frame("Initialize")
wt = f.add("Write to Text File", 200, 120)
f_in = cs.new_tunnel(lambda: wire(sr_file.read, wt.inp("file")))
e_in = cs.new_tunnel(lambda: wire(sr_err.read, wt.inp("error in")))
wire(f.const("Date\tTime\tTotal ($)\r\n", 60, 160), wt.inp("text"))
f_out = cs.new_tunnel(lambda: wire(wt.out("refnum out"), sr_file.write))
e_out = cs.new_tunnel(lambda: wire(wt.out("error out"), sr_err.write))
bb, d_in = bundle(f, sr_data.read, ["Total ($)"], 200, 40, tunnel=True)
wire(f.const(0.0, 100, 50), bb.inp("Total ($)"))
d_out = cs.new_tunnel(lambda: wire(bb.out("output cluster"), sr_data.write))
st_out = cs.new_tunnel(lambda: wire(state_const(f, "Read", 360, 200), sr_state.write))
stop_out = cs.new_tunnel(lambda: wire(f.const(False, 360, 260), loop.stop_terminal))


def io(name):
    f = cs.frame(name)
    return f, d_in.inside(f), d_out.inside(f), f_in.inside(f), f_out.inside(f), e_in.inside(f), e_out.inside(f)


def pass_file(f, fi, fo, ei, eo):
    wire(fi, fo)
    wire(ei, eo)


# ---- Read: barcode subVI, buttons (Exit > Receipt > Sort), update Data on Sort ----
f, di, do, fi, fo, ei, eo = io("Read")
pass_file(f, fi, fo, ei, eo)
sub = f.subvi(BARCODE, 140, 40)
combo.term.call("Move", position=(30, 40), owner=f.h)
wire(combo.term, sub.inp("Barcode"))
ub = unbundle(f, di, ["Total ($)"], 140, 140)
add = f.add("Add", 280, 140)
wire(ub.out("Total ($)"), add.inp("x"))
wire(sub.out("Value"), add.inp("y"))
total_ind.term.call("Move", position=(300, 220), owner=f.h)
wire(ub.out("Total ($)"), total_ind.term)
bb, _ = bundle(f, di, ["Total ($)", "Actions", "Delay Time (s)", "Reset"], 400, 60)
wire(add.out(), bb.inp("Total ($)"))
wire(sub.out("Actions"), bb.inp("Actions"))
wire(sub.out("Delay Time"), bb.inp("Delay Time (s)"))
wire(f.const(True, 330, 110), bb.inp("Reset"))
for i, name in enumerate(["Sort", "Receipt", "Exit"]):
    btn[name].term.call("Move", position=(30, 260 + 50 * i), owner=f.h)
sel_data = f.add("Select", 560, 80)
wire(btn["Sort"].term, sel_data.inp("s"))
wire(bb.out("output cluster"), sel_data.inp("t"))
wire(di, sel_data.inp("f"))
wire(sel_data.out(), do)
prev = state_const(f, "Read", 300, 300)
for i, name in enumerate(["Sort", "Receipt", "Exit"]):  # innermost first -> Exit has priority
    sel = f.add("Select", 450 + 90 * i, 280 + 30 * i)
    wire(btn[name].term, sel.inp("s"))
    wire(state_const(f, name, 360 + 90 * i, 250 + 30 * i), sel.inp("t"))
    wire(prev, sel.inp("f"))
    prev = sel.out()
wire(prev, st_out.inside(f))
wt = f.add("Wait (ms)", 140, 380)
wire(f.const(100, 60, 380), wt.inp("milliseconds to wait"))
wire(f.const(False, 600, 400), stop_out.inside(f))

# ---- Sort: Elapsed Time on Delay Time, Reset only on the first pass, lights until expired ----
f, di, do, fi, fo, ei, eo = io("Sort")
pass_file(f, fi, fo, ei, eo)
ub = unbundle(f, di, ["Delay Time (s)", "Reset", "Actions"], 100, 60)
el = f.express("Elapsed Time", 260, 40)
wire(ub.out("Delay Time (s)"), el.inp("Time Target (s)"))
wire(ub.out("Reset"), el.inp("Reset"))
bb, _ = bundle(f, di, ["Reset"], 420, 160)
wire(f.const(False, 340, 170), bb.inp("Reset"))
wire(bb.out("output cluster"), do)
off = vi.typedef_const(ACTIONS, where=f, value=(False, False, False), x=400, y=260)
sel_l = f.add("Select", 520, 240)
wire(el.out("Time has Elapsed"), sel_l.inp("s"))
wire(off, sel_l.inp("t"))
wire(ub.out("Actions"), sel_l.inp("f"))
lights.term.call("Move", position=(620, 240), owner=f.h)
wire(sel_l.out(), lights.term)
sel_s = f.add("Select", 520, 330)
wire(el.out("Time has Elapsed"), sel_s.inp("s"))
wire(state_const(f, "Read", 420, 320), sel_s.inp("t"))
wire(state_const(f, "Sort", 420, 360), sel_s.inp("f"))
wire(sel_s.out(), st_out.inside(f))
wt = f.add("Wait (ms)", 140, 380)
wire(f.const(10, 60, 380), wt.inp("milliseconds to wait"))
wire(f.const(False, 600, 400), stop_out.inside(f))

# ---- Receipt: popup with the total, date<TAB>time<TAB>total<EOL> to the file, Total = 0 ----
f, di, do, fi, fo, ei, eo = io("Receipt")
ub = unbundle(f, di, ["Total ($)"], 60, 40)
msg = f.add("Format Into String", 200, 30)
wire(f.const("Your total is $%.2f", 120, 10), msg.inp("format string"))
wire(ub.out("Total ($)"), msg.inp("input 1"))
wire(ei, msg.inp("error in"))
dlg = f.add("One Button Dialog", 360, 30)
wire(msg.out("resulting string"), dlg.inp("message"))
tot = f.add("Format Into String", 200, 150)
wire(f.const("%.2f", 120, 140), tot.inp("format string"))
wire(ub.out("Total ($)"), tot.inp("input 1"))
wire(msg.out("error out"), tot.inp("error in"))
dts = f.add("Get Date/Time String", 200, 240)
cat = f.add("Concatenate Strings", 400, 180)
cat["Input Count"] = 6
cat.refresh()
cins = [t for t in cat.terms if not t.is_source]
pieces = [dts.out("date string"), f.add("Tab Constant", 330, 200), dts.out("time string"),
          f.add("Tab Constant", 330, 230), tot.out("resulting string"), f.add("End of Line Constant", 330, 260)]
for src, dst in zip(pieces, cins):
    wire(src, dst)
wt = f.add("Write to Text File", 540, 180)
wire(fi, wt.inp("file"))
wire(cat.out(), wt.inp("text"))
wire(tot.out("error out"), wt.inp("error in"))
wire(wt.out("refnum out"), fo)
wire(wt.out("error out"), eo)
bb, _ = bundle(f, di, ["Total ($)"], 540, 60)
wire(f.const(0.0, 460, 70), bb.inp("Total ($)"))
wire(bb.out("output cluster"), do)
wire(state_const(f, "Read", 600, 300), st_out.inside(f))
wire(f.const(False, 600, 360), stop_out.inside(f))

# ---- Exit: only state that stops the loop (file is closed after the loop) ----
f, di, do, fi, fo, ei, eo = io("Exit")
pass_file(f, fi, fo, ei, eo)
wire(di, do)
wire(state_const(f, "Exit", 300, 200), st_out.inside(f))
wire(f.const(True, 300, 260), stop_out.inside(f))

# ---------------- hand-off notes, layout, save ----------------
vi.todo("draw an icon for Barcode.vi (and Recycle.vi if you like)", x=20, y=700)
vi.cleanup()
for c, xy in ((combo, (30, 30)), (btn["Sort"], (30, 100)), (btn["Receipt"], (30, 160)),
              (btn["Exit"], (30, 220)), (total_ind, (250, 30)), (lights, (250, 100))):
    c.pos = xy
path = vi.save(MAIN)
lv.flush_todos(HERE)
print(f"built {path} in {time.time() - t0:.1f} s; Execution:State =", vi.get("Execution:State", "VI"))
