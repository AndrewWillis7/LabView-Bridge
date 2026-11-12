"""Benchmark: finish the ENGR 3350 'Disturb' practice test entirely through the LabVIEW Bridge.

Starts from the NI Simple State Machine template (Main.vi as saved) plus the State/Data typedefs,
and builds the state machine described in the test:
    Initialize -> Wait for Event -> {Disturb Mass, Read Data (50 ms timeout), Exit};
    Read Data -> Save Data (not settled) | Wait for Event (settled)

Run:  python build_disturb.py        (LabVIEW running, LVBridge Server.vi available)
"""
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bridge"))
from lvapi import LV, wire  # noqa: E402

MAIN = HERE / "Main.vi"
SMD = HERE / "SMD trans.vi"
VMAG = HERE / "Vibration Magnitude.vi"
STATES = ["Initialize", "Wait for Event", "Disturb Mass", "Save Data", "Read Data", "Exit"]  # State.ctl order
S = {name: i for i, name in enumerate(STATES)}

t0 = time.time()
lv = LV()
shutil.copyfile(HERE / "template" / "Main.template.vi", MAIN)  # NI Simple State Machine starter
m = lv.open_vi(MAIN)
m.call("Revert VI")                 # reload from disk in case an older Main.vi is in memory
m = lv.open_vi(MAIN)
D = m.diagram
for n in D.nodes():   # the template drops an unwired "SMD trans.vi" on the root diagram
    if n.cls == "SubVI" and n.label == "SMD trans.vi":
        n.delete()
loop = next(n for n in D.nodes() if n.cls == "WhileLoop")
cs = next(n for n in loop.frames[0].nodes() if n.cls == "CaseStructure")


DEBUG = bool(__import__("os").environ.get("DEBUG"))


def chk(tag):
    if DEBUG:
        print(f"  [{tag}] tunnels:", sorted(("L" if t.pos[0] < cs.pos[0] + 5 else "R", t.pos[1]) for t in cs.tunnels))


def enum_const(term, state):
    c = term.constant()
    lv.b.set_constant(c.h, S[state], "i32")
    return c


def next_state(frame_diag, state):
    """Set the frame's existing 'Next State' enum constant."""
    c = next(o for o in frame_diag.objects() if o.cls == "EnumConstant")
    lv.b.set_constant(c.h, S[state], "i32")
    return c


# ---------------- frames ----------------
cs.names = ["Initialize", "Wait for Event", "Disturb Mass", "Read Data", "Exit"]
cs.add_frame("Save Data", after="Read Data")
F = {n: i for i, n in enumerate(cs.names)}
print("frames:", cs.names); chk("frames")

# case-structure tunnels (positions from the template)
data_in, data_out = cs.tunnel_at("left", 120), cs.tunnel_at("right", 120)
err_in, err_out = cs.tunnel_at("left", 457), cs.tunnel_at("right", 457)
state_out, stop_out = cs.tunnel_at("right", 311), cs.tunnel_at("right", 488)


def frame(name):
    i = F[name]
    return i, cs.frames[i]


# ---------------- Initialize: open file, write header, Settled=T, store refnum ----------------
i, f = frame("Initialize")
bun = next(n for n in f.nodes() if n.cls == "NamedBundler")
bun["Input Count"] = 2
bun["Element Names[]"] = ("Settled", "File")
bun.refresh()
settled0 = next(o for o in f.objects() if o.cls == "BooleanConstant")  # template's Settled constant
lv.b.set_constant(settled0.h, True, "bool")                            # start settled
cvp = f.add("Current VI's Path", 200, 240)
strip = f.add("Strip Path", 300, 240)
bpath = f.add("Build Path", 400, 240)
wire(cvp.out(), strip.inp("path"))
wire(strip.out("stripped path"), bpath.inp("base path"))
wire(f.const("vibration data.txt", 300, 290), bpath.inp("name"))
opn = f.add("Open/Create/Replace File", 500, 300)
wire(bpath.out(), opn.inp("file path"))
op = opn.inp("operation").constant()
lv.b.set_constant(op.h, 4, "i32")  # replace or create
wrt = f.add("Write to Text File", 640, 300)
wire(f.const("Time (s)\tAcceleration (m/s^2)\r\n", 520, 380), wrt.inp("text"))
wire(opn.out("refnum out"), wrt.inp("file"))
err_out.inside(i).unwire()
wire(err_in.inside(i), opn.inp("error in"))
wire(opn.out("error out"), wrt.inp("error in"))
wire(wrt.out("error out"), err_out.inside(i))
wire(wrt.out("refnum out"), bun.inp("File"))
print("Initialize done"); chk("Initialize")

# ---------------- Wait for Event: Disturb / Exit value change, 50 ms timeout -> Read Data -------------
i, f = frame("Wait for Event")
ev = next(n for n in f.nodes() if n.cls == "EventStructure")
ev.call("Set Handled Events", FrameIndex=2, StaticAppVIEvents=((1, -1, False),),
        StaticControlEvents=(), DynamicEvents=())
Terminal = type(err_in.inside(0))
Terminal(lv, ev.get("Timeout Terminal"), "Terminal").constant(50)
ev_data_l = min((t for t in ev.tunnels if t.pos[0] <= ev.pos[0] + 5), key=lambda t: t.pos[1])
ev_data_r = min((t for t in ev.tunnels if t.pos[0] > ev.pos[0] + 5), key=lambda t: t.pos[1])
for k in (1, 2):  # template's "Running User State" bundles reference the removed String element
    ef = ev.frames[k]
    for n in ef.nodes():
        if n.cls == "NamedBundler":
            n.delete()
    lv.t.call(ef.h, "Diagram", "Remove Wire Loose Ends")
    wire(ev_data_l.inside(k), ev_data_r.inside(k))
next_state(ev.frames[2], "Read Data")
next_state(ev.frames[1], "Disturb Mass")
dsm = m.ctl("Do Something <Replace Me>")
dsm.label = "Disturb Mass"
lv.obj(dsm["Boolean Text"], "Text")["Text"] = "Disturb"
m.ctl("Do Something Else <Replace Me>").delete()
print("Wait for Event done"); chk("Wait for Event")

# ---------------- Disturb Mass: SMD(disturb=T), Settled=F, record time & accel ----------------
i, f = frame("Disturb Mass")
f.clear()
smd = f.subvi(SMD, 250, 200)
wire(f.const(True, 180, 200), smd.inp("Disturb"))
now = f.add("Get Date/Time In Seconds", 250, 300)
dbl = f.add("To Double Precision Float", 380, 300)
wire(now.out(), dbl.inp("number"))
bb = f.add("Bundle By Name", 520, 140)
bb["Input Count"] = 3
data_out.inside(i).unwire()
wire(data_in.inside(i), bb.inp("input cluster"))
bb["Element Names[]"] = ("Settled", "Disturb Time (s)", "Acceleration")
bb.refresh()
wire(f.const(False, 440, 160), bb.inp("Settled"))
wire(dbl.out(), bb.inp("Disturb Time (s)"))
wire(smd.out("Acceleration"), bb.inp("Acceleration"))
wire(bb.out("output cluster"), data_out.inside(i))
err_out.inside(i).unwire()
wire(err_in.inside(i), smd.inp("error in"))
wire(smd.out("error"), err_out.inside(i))
next_state(f, "Wait for Event")
print("Disturb Mass done"); chk("Disturb Mass")

# ---------------- Read Data: SMD(F), vm subVI, chart, settled logic, LED, next state ----------------
i, f = frame("Read Data")
f.clear()
data_out.inside(i).unwire()   # unwire() removes the whole net, so do it before branching off data_in
smd = f.subvi(SMD, 220, 260)
wire(f.const(False, 150, 260), smd.inp("Disturb"))
ub = f.add("Unbundle By Name", 220, 150)
wire(data_in.inside(i), ub.inp("input cluster"))
ub["Output Count"] = 2
ub["Element Names[]"] = ("Acceleration", "Settled")
ub.refresh()
vmn = f.subvi(VMAG, 360, 200)
wire(smd.out("Acceleration"), vmn.inp("a_n"))
wire(ub.out("Acceleration"), vmn.inp("a_n-1"))
orr = f.add("Or", 470, 160)
wire(ub.out("Settled"), orr.inp("x"))
wire(vmn.out("Settled"), orr.inp("y"))
bb = f.add("Bundle By Name", 580, 130)
bb["Input Count"] = 2
wire(data_in.inside(i), bb.inp("input cluster"))
bb["Element Names[]"] = ("Acceleration", "Settled")
bb.refresh()
wire(smd.out("Acceleration"), bb.inp("Acceleration"))
wire(orr.out(), bb.inp("Settled"))
wire(bb.out("output cluster"), data_out.inside(i))
# chart + LED terminals live in this frame
chart = m.indicator("Waveform Chart", "Vibration History", 230, 20)
led = m.indicator("Round LED", "Saving Data", 40, 140)
chart.term.call("Move", position=(560, 330), owner=f.h)
led.term.call("Move", position=(640, 250), owner=f.h)
cb = f.add("Bundle", 470, 330)
el = [t for t in cb.terms if t.name == "element"]   # capture before wiring renames them
wire(smd.out("Acceleration"), el[0])
wire(vmn.out("vm"), el[1])
wire(cb.out(), chart.term)
nt = f.add("Not", 560, 250)
wire(orr.out(), nt.inp("x"))
wire(nt.out(), led.term)
sel = f.add("Select", 640, 300)
wire(orr.out(), sel.inp("s"))
ns = next_state(f, "Wait for Event")
type(err_in.inside(0))(lv, lv.b.constant_terminal(ns.h), "Terminal").unwire()
wire(ns, sel.inp("t"))
enum_const(sel.inp("f"), "Save Data")  # Create Constant auto-wires it
wire(sel.out(), state_out.inside(i))
err_out.inside(i).unwire()
wire(err_in.inside(i), smd.inp("error in"))
wire(smd.out("error"), err_out.inside(i))
print("Read Data done"); chk("Read Data")

# ---------------- Save Data: elapsed time + acceleration -> file (low-level IO) ----------------
i, f = frame("Save Data")
# linked tunnels: LabVIEW already auto-wired data & error pass-throughs into the new frame
if not data_out.inside(i).wired:
    wire(data_in.inside(i), data_out.inside(i))
err_out.inside(i).unwire()
ub = f.add("Unbundle By Name", 220, 160)
wire(data_in.inside(i), ub.inp("input cluster"))
ub["Output Count"] = 3
ub["Element Names[]"] = ("File", "Disturb Time (s)", "Acceleration")
ub.refresh()
now = f.add("Get Date/Time In Seconds", 220, 260)
dbl = f.add("To Double Precision Float", 330, 260)
wire(now.out(), dbl.inp("number"))
sub = f.add("Subtract", 430, 230)
wire(dbl.out(), sub.inp("x"))
wire(ub.out("Disturb Time (s)"), sub.inp("y"))
fmt1 = f.add("Format Into String", 520, 230)
wire(f.const("%.3f\t", 440, 190), fmt1.inp("format string"))
wire(sub.out(), fmt1.inp("input 1"))
fmt2 = f.add("Format Into String", 620, 230)
wire(f.const("%.4f\r\n", 560, 190), fmt2.inp("format string"))
wire(fmt1.out("resulting string"), fmt2.inp("initial string"))
wire(ub.out("Acceleration"), fmt2.inp("input 1"))
wr = f.add("Write to Text File", 700, 300)
wire(ub.out("File"), wr.inp("file"))
wire(fmt2.out("resulting string"), wr.inp("text"))
wire(err_in.inside(i), fmt1.inp("error in"))
wire(fmt1.out("error out"), fmt2.inp("error in"))
wire(fmt2.out("error out"), wr.inp("error in"))
wire(wr.out("error out"), err_out.inside(i))
enum_const(state_out.inside(i), "Wait for Event")
stop_out.inside(i).constant(False)
print("Save Data done"); chk("Save Data")

# ---------------- Exit: close file, stop ----------------
i, f = frame("Exit")
ub = f.add("Unbundle By Name", 250, 200)
wire(data_in.inside(i), ub.inp("input cluster"))
ub["Element Names[]"] = ("File",)
ub.refresh()
cl = f.add("Close File", 400, 260)
wire(ub.out("File"), cl.inp("refnum"))
err_out.inside(i).unwire()
wire(err_in.inside(i), cl.inp("error in"))
wire(cl.out("error out"), err_out.inside(i))
print("Exit done"); chk("Exit")

# ---------------- front panel ----------------
for dec in m.panel.get("Decorations[]", "Panel"):   # template's instructional free text
    lv.obj(dec).delete()
dsm.pos = (30, 40)
led.pos = (30, 130)
m.ctl("Exit").pos = (30, 220)
chart.pos = (200, 20)
chart["Legend:Plot Minimum"] = 2
chart["Legend:Number of Rows"] = 2
for idx, (name, style) in enumerate([("Acceleration", 0), ("Vibration Magnitude", 1)]):
    chart["Active Plot"] = idx
    plot = lv.obj(chart["Plot"], "Plot")
    plot["Plot Name"] = name
    plot["Line Style"] = style            # 0 solid, 1 dashed
    print("  plot", idx, plot["Plot Name"], "line style", plot["Line Style"])
ys = lv.obj(chart["Y Scale"], "GraphScale")
ys["Scale Fit"] = 0                       # fixed scale (no autoscale)
ys["Range:Minimum"] = -2.0
ys["Range:Maximum"] = 2.0
lv.obj(ys["Name Label"], "Text")["Text"] = "Acceleration (m/s^2)"
xs = lv.obj(chart["X Scale"], "GraphScale")
lv.obj(xs["Name Label"], "Text")["Text"] = "Sample (50 ms)"
print("front panel done"); chk("front panel")

# ---------------- state descriptions, remove template notes ----------------
DESCR = {
    "Initialize": "Initialize - open the data file (low-level file I/O), write the header, start with Settled = T.",
    "Wait for Event": "Wait for Event - Disturb Mass / Exit buttons, or every 50 ms (timeout) go to Read Data.",
    "Disturb Mass": "Disturb Mass - call Get Acceleration with disturb = T, record the disturb time, Settled = F.",
    "Read Data": "Read Data - read acceleration, compute vibration magnitude (subVI), chart it; "
                 "Save Data while not settled, otherwise back to Wait for Event.",
    "Save Data": "Save Data - write time since disturbance and acceleration to the file (low-level file I/O).",
    "Exit": "Exit - close the data file and stop the state machine.",
}
for name, text in DESCR.items():
    fd = cs.frame(name)
    lv.obj(fd["Subdiagram Label"], "Text")["Text"] = text
    for dec in fd.get("Decorations[]", "Diagram"):
        d = lv.obj(dec)
        if d.cls == "Text" and "#CodeNeeded" in (d.get("Text", "Text") or ""):
            d.delete()
for k in (1, 2):   # leftover "Running User State n" string constants in the event cases
    for o in ev.frames[k].objects():
        if o.cls == "StringConstant":
            o.delete()
for dec in cs.frame("Wait for Event").get("Decorations[]", "Diagram"):
    d = lv.obj(dec)
    if d.cls == "Text" and "#CodeNeeded" in (d.get("Text", "Text") or ""):
        d.delete()
for k in range(len(ev.frames)):
    for dec in ev.frames[k].get("Decorations[]", "Diagram"):
        d = lv.obj(dec)
        if d.cls == "Text" and "#CodeNeeded" in (d.get("Text", "Text") or ""):
            d.delete()
print("descriptions done")

m.call("Block Diagram:Clean Up")
m.save()
print(f"saved {m.path} in {time.time() - t0:.1f} s")
