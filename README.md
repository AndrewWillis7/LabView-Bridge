# LabVIEW Bridge

Lets Claude Code build real VIs in LabVIEW 2026 (64-bit) from text, through VI Scripting.

```
Claude Code ──MCP (stdio)──► bridge/mcp_server.py ──ActiveX──► LabVIEW 2026
                               │                     ├─ front-panel mailbox ─► labview/LVBridge Server.vi
                               │                     │     (create objects, wire, save, + meta cases)
                               │                     └─ thunk VIs (labview/thunks/, generated on demand)
                               └─ lvapi.py: VI / Diagram / Node / Terminal / Control object model
```

## How it works
`LVBridge Server.vi` is the only hand-built VI. Besides object creation/wiring/saving it has a few
*meta* cases (`pclass`, `pwrite`, `msel`, `mkctl`, `mkind`, `mkconst`) that let the bridge **build its
own accessor VIs**: the first time Python needs, say, `CaseStructure.Frame Names` (write) or
`EventStructure.Set Handled Events`, `thunks.py` scripts a tiny VI containing exactly that Property/Invoke
Node, saves it under `labview/thunks/`, and from then on calls it straight through ActiveX (<1 ms).
So every VI Server property (277 classes, see `bridge/vi_server_catalog.json`) and every method
(discovered from LabVIEW, cached in `bridge/vi_server_methods.json`) is reachable without more G code.
Palette functions can be run the same way (`Thunks.prim`).

## The text pipeline
One Python script (or one `lv_exec` MCP call) builds or edits a whole VI:

```python
vi = lv.open_vi("Main.vi")
cs = vi.diagram.find(cls="CaseStructure")
cs.names = ["Init", "Run", "Exit"]               # any property:  o["Prop"] / o["Prop"] = v
f = cs.add_frame("Save", after="Run")             # any method:    o.call("Method", Param=v)
w = f.add("Write to Text File", 400, 200)
wire(cs.tunnel_at("left", 120).inside(f), w.inp("file"))   # terminals by name
vi.save()
```

More building blocks (tested in `tests/gap_test.py`, which builds and runs a barcode subVI in about 2 s):

```python
t = lv.open_vi("controls/State.ctl"); t.controls()[0].items = ["Init", "Read", "Exit"]; t.save()
cl = lv.open_vi("controls/Data.ctl").controls()[0]
cl.add("Push Button", "Reset"); cl.add("controls/actions.ctl", "Actions")   # typedef inside a cluster
vi.ctl("Sort").mechanical_action = "latch when released"
vi.connector(left=[barcode], right=[actions, delay, value])                # 4x2x2x4, top to bottom
el = frame_diagram.express("Elapsed Time", 50, 50)                          # real Express VI node
c = vi.typedef_const("controls/actions.ctl", where=frame, value=(False, True, False))
vi.todo("draw the icon")        # 'USER TODO:' note in the VI + lv.flush_todos(folder) -> TODO_USER.md
t = lv.new_ctl("controls/Color.ctl", "Enum (modern)", "Color"); t.root.items = [...]; t.save()  # new typedef
loop = vi.diagram.add("while", 160, 40); loop.resize(760, 380)
sr = loop.add_shift_register(80)   # sr.init / sr.read / sr.write / sr.final ; loop.stop_terminal
```

If the bridge can't do a step, the build script calls `vi.todo(...)` instead of failing. The step is
then left as a visible note in the VI and as an entry in a `TODO_USER.md` checklist.

From-scratch example: `Projects/Rainbow/build_rainbow.py` builds a rainbow state machine (two typedefs,
a while loop with shift registers, 4 states, latched Next/Stop buttons) in about 2 s with no template.

Benchmark: `Projects/Disturb Project/build_disturb.py` turns the NI Simple State Machine template into
the full ENGR 3350 "Disturb" practice-test solution (event structure with 50 ms timeout, 6 states,
low-level file I/O, chart with solid/dashed plots, LED, subVI) in about 6 s.

## Layout
- `bridge/lvcom.py` – typed ActiveX wrapper (LabVIEW.Application / VirtualInstrument)
- `bridge/client.py` – mailbox protocol to `LVBridge Server.vi`, image export, run VIs
- `bridge/thunks.py` – generated property/method/primitive accessors
- `bridge/lvapi.py` – object model used by scripts and `lv_exec`
- `bridge/mcp_server.py` – MCP tools: `lv_exec`, `lv_props`, `lv_methods`, `lv_args`, `lv_get`, `lv_set`,
  `lv_call`, `lv_view`, `lv_run_vi`, plus the low-level `lv_*` object/wire tools
- `labview/LVBridge Server.vi` – the hand-built server (see `BUILD_GUIDE.md`)
- `.mcp.json` – registers the `labview` MCP server for Claude Code sessions in this folder

## Setup notes
- `LabVIEW.ini` has `SuperPrivateScriptingFeatureVisible=True` (backup at `scratch/LabVIEW.ini.bak`), and
  Tools » Options » VI Server » *Show VI Scripting functions* is enabled.
- Python 3.13 with `pip install -r requirements.txt`.
- The server VI is started automatically by the bridge; stop it with its `stop` button.
- After changing `bridge/*.py`, reconnect the MCP server (`/mcp`) to pick up new tools.

## Gotchas the API already handles
- VI/Application refnums don't cross ActiveX as ints, so their thunks take a U32 and Type Cast inside.
- Nested Python tuples are sent as arrays of VARIANT arrays (arrays of clusters), not 2-D arrays.
- `Inside Terminals[]` of a case tunnel isn't in frame order after `Add Frame`; `Tunnel.inside(frame)`
  matches by owning diagram. Linked tunnels auto-wire pass-throughs into new frames.
- `Terminal.unwire()` deletes the whole wire net in that diagram, so unwire before branching.
- Refs opened *inside* a thunk close when it finishes, so VIs are opened by the long-running server.
- A `.ctl` can't be dropped on a diagram (error 1060), so `typedef_const` places a panel instance,
  creates a constant from it, deletes the control, then calls `Move` into the target frame.
- LabVIEW links typedefs by name in memory: two `State.ctl` files from different folders resolve to
  whichever one was loaded first.
- Connector `Terminal Bounds[]` entries are (left, top, right, bottom). `connector()` picks the left and
  right columns from these bounds, so it doesn't depend on terminal index order.
- Run-checks work headless: `SetControlValue` on a latched button does press it, so a script can run a VI,
  click buttons and read indicators. Modal dialogs (One Button Dialog) still need a person to close them.
- `lv.new_ctl` copies a small vi.lib `.ctl` as a seed, because saving a new VI with a `.ctl` name gives a VI,
  not a control file. The seed belongs to Actor Framework.lvlib, so `new_ctl` disconnects the copy.
- Typedef names are global in memory. Reusing a name that's already loaded from another folder (for example
  `State.ctl`) fails with 1061, so give each project's typedefs unique names.
- Case outputs: wiring each frame straight to the same indicator can make one tunnel per frame (a broken
  VI). Use `cs.wire_out(indicator, {frame: source, ...})`, which makes one tunnel and feeds it from every
  frame.
- Shift registers: `Add Shift Register` returns only the right half. The left terminals are found through the
  loop's outer terminals. A wire between two shift registers routes around a case structure instead of
  making tunnels, so start a tunnel by wiring to a node inside the frame.
