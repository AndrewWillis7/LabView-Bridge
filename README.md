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
