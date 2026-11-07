# LabVIEW Bridge

Lets Claude Code build real VIs in LabVIEW 2026 (64-bit) through VI Scripting.

```
Claude Code ──MCP (stdio)──► bridge/mcp_server.py ──ActiveX──► LabVIEW 2026
                                                     ├─ front-panel mailbox ─► labview/LVBridge Server.vi (VI Scripting)
                                                     └─ direct: open/run VIs, render panel/diagram images
```

## Layout
- `bridge/lvcom.py` – typed ActiveX wrapper (LabVIEW.Application / VirtualInstrument)
- `bridge/client.py` – mailbox protocol to `LVBridge Server.vi`, image export, run VIs, property catalog lookup
- `bridge/mcp_server.py` – MCP tools (`lv_new_vi`, `lv_add_object`, `lv_wire`, `lv_save`, `lv_view`, …)
- `bridge/vi_server_catalog.json` – VI Server classes/properties dumped from this LabVIEW install
- `labview/LVBridge Server.vi` – the one hand-built VI (see `BUILD_GUIDE.md`)
- `.mcp.json` – registers the `labview` MCP server for Claude Code sessions in this folder

## Setup notes
- `LabVIEW.ini` has `SuperPrivateScriptingFeatureVisible=True` (backup at `scratch/LabVIEW.ini.bak`).
- Python 3.13 with `pip install -r requirements.txt`.
- The server VI is started automatically by the bridge; stop it with its `stop` button.
