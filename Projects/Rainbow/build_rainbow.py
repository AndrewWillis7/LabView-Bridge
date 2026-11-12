"""Rainbow state machine, built from scratch through the LabVIEW Bridge (no template, no GUI).

Front panel: 'Next' button, 'Stop' button, 'Current Color' string indicator.
Typedefs:    controls/Rainbow Color.ctl (Red ... Violet)    controls/Rainbow State.ctl (Show Color, Wait, Next Color, Stop)
States:
    Show Color -> write the color name to the indicator              -> Wait
    Wait       -> 50 ms; Stop? -> Stop, Next? -> Next Color, else Wait
    Next Color -> Increment the Color enum (wraps Violet -> Red)      -> Show Color
    Stop       -> stop the loop

Run:  python build_rainbow.py [out_dir]     (LabVIEW running; default out_dir = this folder)
"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "bridge"))
from lvapi import LV, Obj, wire  # noqa: E402,F401

OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE
CTL = OUT / "controls"
COLORS = ["Red", "Orange", "Yellow", "Green", "Blue", "Indigo", "Violet"]
STATES = ["Show Color", "Wait", "Next Color", "Stop"]
S = {n: i for i, n in enumerate(STATES)}

t0 = time.time()
CTL.mkdir(parents=True, exist_ok=True)
lv = LV()


# Rerunnable: LabVIEW keeps files in memory by name, so existing files are opened and rebuilt in place.
def typedef(name, label, items):
    path = CTL / name
    t = lv.open_vi(path) if path.exists() else lv.new_ctl(path, "Enum (modern)", label)
    t.controls()[0].items = items
    t.save()
    return path


COLOR_CTL = typedef("Rainbow Color.ctl", "Color", COLORS)
STATE_CTL = typedef("Rainbow State.ctl", "State", STATES)

# ---- front panel ----
MAIN = OUT / "Rainbow.vi"
try:  # also succeeds when the file was deleted but LabVIEW still holds it in memory
    vi = lv.open_vi(MAIN)
    vi.diagram.clear()
    for c in vi.controls():
        c.delete()
except Exception:  # noqa: BLE001
    vi = lv.new_vi()
nxt = vi.control("OK Button (modern)", "Next", 40, 40)
stop = vi.control("Rectangular Stop Button", "Stop", 40, 110)
for b in (nxt, stop):
    b.mechanical_action = "latch when released"
show = vi.indicator("String Indicator (modern)", "Current Color", 200, 50)
nxt.wrap(nxt.get("Boolean Text", "Boolean"), "Text").set("Text", "Next", "Text")  # button caption

# ---- loop + shift registers ----
D = vi.diagram
loop = D.add("while", 160, 40)
loop.resize(760, 380)
sr_state = loop.add_shift_register(80)
sr_color = loop.add_shift_register(140)
wire(vi.typedef_const(STATE_CTL, value=S["Show Color"], x=60, y=110), sr_state.init)
wire(vi.typedef_const(COLOR_CTL, value=0, x=60, y=170), sr_color.init)

cs = loop.frames[0].add("Case Structure", 240, 70)
wire(sr_state.read, cs.terms[0])                     # enum -> case selector
while len(cs.frames) < len(STATES):
    cs.add_frame()
cs.names = [f'"{n}"' for n in STATES]


def new_tunnel(fn):
    """Run fn (which wires across the case border) and return the one tunnel it created."""
    before = {t.h for t in cs.tunnels}
    fn()
    made = [t for t in cs.tunnels if t.h not in before]
    assert len(made) == 1, f"expected 1 new tunnel, got {len(made)}"
    return made[0]


# Show Color: creates every tunnel (color in/out, state out, stop out); other frames fill them in
f = cs.frame("Show Color")
fmt = f.add("Format Into String", 120, 40)
c_in = new_tunnel(lambda: wire(sr_color.read, fmt.inp("input 1")))
c_out = new_tunnel(lambda: wire(c_in.inside(f), sr_color.write))
show.term.call("Move", position=(300, 40), owner=f.h)
wire(fmt.out("resulting string"), show.term)
ns = vi.typedef_const(STATE_CTL, where=f, value=S["Wait"], x=300, y=150)
st_out = new_tunnel(lambda: wire(ns, sr_state.write))
sf = f.const(False, 300, 220)
stop_out = new_tunnel(lambda: wire(sf, loop.stop_terminal))


def frame_io(name):
    f = cs.frame(name)
    return f, c_in.inside(f), c_out.inside(f)


# Wait
f, cin, cout = frame_io("Wait")
wire(cin, cout)
wt = f.add("Wait (ms)", 60, 30)
wire(f.const(50, 20, 30), wt.inp("milliseconds to wait"))
nxt.term.call("Move", position=(40, 120), owner=f.h)
stop.term.call("Move", position=(40, 200), owner=f.h)
sel_next = f.add("Select", 220, 110)
wire(nxt.term, sel_next.inp("s"))
wire(vi.typedef_const(STATE_CTL, where=f, value=S["Next Color"], x=120, y=100), sel_next.inp("t"))
wire(vi.typedef_const(STATE_CTL, where=f, value=S["Wait"], x=120, y=150), sel_next.inp("f"))
sel_stop = f.add("Select", 330, 160)
wire(stop.term, sel_stop.inp("s"))
wire(vi.typedef_const(STATE_CTL, where=f, value=S["Stop"], x=230, y=200), sel_stop.inp("t"))
wire(sel_next.out(), sel_stop.inp("f"))
wire(sel_stop.out(), st_out.inside(f))
wire(f.const(False, 380, 250), stop_out.inside(f))

# Next Color
f, cin, cout = frame_io("Next Color")
inc = f.add("Increment", 150, 60)
wire(cin, inc.inp("x"))
wire(inc.out(), cout)
wire(vi.typedef_const(STATE_CTL, where=f, value=S["Show Color"], x=300, y=150), st_out.inside(f))
wire(f.const(False, 380, 250), stop_out.inside(f))

# Stop
f, cin, cout = frame_io("Stop")
wire(cin, cout)
wire(vi.typedef_const(STATE_CTL, where=f, value=S["Stop"], x=300, y=150), st_out.inside(f))
wire(f.const(True, 380, 250), stop_out.inside(f))

cs.frame("Show Color")  # leave the first state visible
vi.cleanup()
path = vi.save(MAIN)
print(f"built {path} in {time.time() - t0:.1f} s; runnable state:", vi.get("Execution:State", "VI"))
