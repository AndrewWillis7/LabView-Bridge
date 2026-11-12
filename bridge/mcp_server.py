"""MCP server exposing LabVIEW VI Scripting (via LVBridge Server.vi) to Claude."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pythoncom  # noqa: E402
from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

from client import Bridge, BridgeError, find_property  # noqa: E402

mcp = FastMCP("labview")
_bridge = None


def B():
    global _bridge
    pythoncom.CoInitialize()
    if _bridge is None:
        _bridge = Bridge()
    return _bridge


def _safe(fn):
    try:
        return fn()
    except BridgeError as e:
        return f"ERROR: {e}"
    except Exception as e:  # COM errors etc.
        return f"ERROR: {type(e).__name__}: {e}"


@mcp.tool()
def lv_status() -> str:
    """Check LabVIEW connection and start LVBridge Server.vi if needed."""
    def go():
        b = B()
        ver = b.lv.app.get("Version")
        b.ensure_server()
        return f"LabVIEW {ver} connected; server running: {b.server_path}"
    return _safe(go)


@mcp.tool()
def lv_new_vi() -> str:
    """Create a new blank VI in LabVIEW. Returns handles {vi, diagram, panel}."""
    return _safe(lambda: json.dumps(B().new_vi()))


@mcp.tool()
def lv_open_vi(path: str) -> str:
    """Open an existing VI for editing. Returns handles {vi, diagram, panel}."""
    return _safe(lambda: json.dumps(B().open_vi(path)))


@mcp.tool()
def lv_styles(filter: str = "") -> str:
    """List New VI Object style names (controls, indicators, functions, structures...) matching filter."""
    def go():
        st = B().styles()
        hits = [k for k in st if filter.lower() in k.lower()]
        return json.dumps(hits[:300]) + (f"\n({len(hits)} matches)" if len(hits) > 300 else "")
    return _safe(go)


@mcp.tool()
def lv_add_object(owner: int, style: str, x: int = 100, y: int = 100) -> str:
    """Place a native object. owner = diagram/panel/structure-diagram handle; style = style name or number.
    Returns the new object's handle."""
    return _safe(lambda: json.dumps({"handle": B().add_object(owner, style, x, y)}))


@mcp.tool()
def lv_add_subvi(owner: int, path: str, x: int = 100, y: int = 100) -> str:
    """Place a subVI (any .vi path, incl. vi.lib) on a diagram. Returns the node handle."""
    return _safe(lambda: json.dumps({"handle": B().add_subvi(owner, path, x, y)}))


@mcp.tool()
def lv_terminals(node: int) -> str:
    """List a node's terminals (Terms[] order). Use indices from LabVIEW scripting context help."""
    return _safe(lambda: json.dumps(B().terminals(node)))


@mcp.tool()
def lv_control_terminal(control: int) -> str:
    """Get the block-diagram terminal handle of a front-panel control/indicator."""
    return _safe(lambda: json.dumps({"terminal": B().control_terminal(control)}))


@mcp.tool()
def lv_wire(terminal_a: int, terminal_b: int) -> str:
    """Wire two terminals together."""
    return _safe(lambda: (B().wire(terminal_a, terminal_b), "wired")[1])


@mcp.tool()
def lv_find_property(query: str, class_name: str = "") -> str:
    """Search the VI Server property catalog (name -> ID string) for use with lv_set_properties."""
    return json.dumps(find_property(class_name, query)[:60])


@mcp.tool()
def lv_set_properties(property_node: int, id_strings: list[str]) -> str:
    """Configure a scripted Property Node's property list (Set Properties[] with ID strings)."""
    return _safe(lambda: (B().set_properties(property_node, id_strings), "ok")[1])


@mcp.tool()
def lv_set_constant(constant: int, value: str, kind: str = "string") -> str:
    """Set a diagram constant's value. kind: string | dbl | i32 | bool."""
    return _safe(lambda: (B().set_constant(constant, value, kind), "ok")[1])


@mcp.tool()
def lv_save(vi: int, path: str) -> str:
    """Save a VI (by handle) to an absolute .vi path."""
    return _safe(lambda: (B().save(vi, path), f"saved {path}")[1])


@mcp.tool()
def lv_cleanup(diagram: int) -> str:
    """Run Block Diagram Clean Up (pass the diagram handle)."""
    return _safe(lambda: (B().cleanup(diagram), "ok")[1])


@mcp.tool()
def lv_close(ref: int) -> str:
    """Close a handle (object or VI reference)."""
    return _safe(lambda: (B().close(ref), "ok")[1])


@mcp.tool()
def lv_constant_terminal(constant: int) -> str:
    """Get the wireable terminal handle of a block-diagram constant."""
    return _safe(lambda: json.dumps({"terminal": B().constant_terminal(constant)}))


@mcp.tool()
def lv_set_representation(constant: int, rep: str) -> str:
    """Set a numeric constant's representation: i8 i16 i32 i64 u8 u16 u32 u64 sgl dbl ext."""
    return _safe(lambda: (B().set_representation(constant, rep), "ok")[1])


@mcp.tool()
def lv_set_label(control: int, text: str) -> str:
    """Set a front-panel control/indicator's label text."""
    return _safe(lambda: (B().set_label(control, text), "ok")[1])


@mcp.tool()
def lv_make_indicator(control: int) -> str:
    """Change a front-panel control into an indicator."""
    return _safe(lambda: (B().make_indicator(control), "ok")[1])


@mcp.tool()
def lv_raw(cmd: str, h: list[int] = [], s: list[str] = [], n: list[int] = []) -> str:
    """Send a raw command to LVBridge Server.vi. Returns {h, s, n}."""
    return _safe(lambda: json.dumps(B().cmd(cmd, h, s, n)))


# ---------------- text pipeline (lvapi + generated thunks) ----------------
_lv = None
_ns = None


def LVS():
    global _lv
    pythoncom.CoInitialize()
    if _lv is None:
        import lvapi
        _lv = lvapi.LV(B())
    return _lv


def _namespace():
    global _ns
    if _ns is None:
        import lvapi
        _ns = {"lv": LVS(), "wire": lvapi.wire, "set_constant": lvapi.set_constant,
               "set_node_label": lvapi.set_node_label, "REPS": lvapi.REPS, "lvapi": lvapi}
    return _ns


@mcp.tool()
def lv_exec(code: str) -> str:
    """Run a Python build script against LabVIEW in a persistent session (variables survive between
    calls). Preloaded: lv (session), wire(a, b), set_constant(c, v, rep), REPS.
    API: vi = lv.new_vi() / lv.open_vi(path); vi.control(style, label, x, y); vi.indicator(...);
    vi.ctl(label); vi.controls(); d = vi.diagram; d.add(style, x, y); d.subvi(path); d.const(value);
    d.nodes(); d.find(label=..., cls=...); node["term name"|index] -> Terminal; wire(src, dst)
    (Controls/constants wire directly); any object: o["Property"], o["Property"] = v,
    o.call("Method", Param=value); Structure: s.frames, s.frame(name), s.names, s.add_frame(name);
    vi.save(path). print() output and the value of a variable named `result` are returned."""
    import contextlib
    import io
    import traceback
    ns = _namespace()
    ns.pop("result", None)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<lv_exec>", "exec"), ns)
    except Exception:  # noqa: BLE001
        return buf.getvalue() + "\nERROR:\n" + traceback.format_exc(limit=4)
    out = buf.getvalue()
    if "result" in ns:
        out += ("\n" if out else "") + "result = " + repr(ns["result"])
    return out or "ok"


@mcp.tool()
def lv_props(cls: str, query: str = "") -> str:
    """List properties of a VI Server class (name, r/w), filtered by substring. Use with o["Name"]."""
    return _safe(lambda: json.dumps(LVS().props(cls, query)[:200]))


@mcp.tool()
def lv_methods(cls: str, query: str = "") -> str:
    """List methods of a VI Server class (discovered from LabVIEW, cached)."""
    return _safe(lambda: json.dumps(LVS().methods(cls, query)))


@mcp.tool()
def lv_args(cls: str, method: str) -> str:
    """Show a method's input/output parameter names (generates its thunk)."""
    return _safe(lambda: json.dumps(LVS().args(cls, method)))


@mcp.tool()
def lv_get(handle: int, prop: str, cls: str = "") -> str:
    """Read any property of any object handle (class auto-detected if omitted)."""
    def go():
        lv = LVS()
        return json.dumps(lv.t.get(handle, cls or lv.class_of(handle), prop), default=str)
    return _safe(go)


@mcp.tool()
def lv_set(handle: int, prop: str, value: str, cls: str = "") -> str:
    """Write any property. value is JSON (e.g. 5, "text", [10, 20], true)."""
    def go():
        lv = LVS()
        v = json.loads(value)
        v = tuple(v) if isinstance(v, list) else v
        lv.t.set(handle, cls or lv.class_of(handle), prop, v)
        return "ok"
    return _safe(go)


@mcp.tool()
def lv_call(handle: int, method: str, args: dict = {}, cls: str = "") -> str:
    """Invoke any VI Server method on a handle. args: {param name: value}. Returns outputs."""
    def go():
        lv = LVS()
        return json.dumps(lv.t.call(handle, cls or lv.class_of(handle), method, **args), default=str)
    return _safe(go)


@mcp.tool()
def lv_view(vi_path: str, which: str = "diagram"):
    """Render a SAVED VI and return images. which: diagram | panel | all."""
    def go():
        imgs = B().export_images(vi_path)
        sel = imgs["all"] if which == "all" else imgs.get(which, [])
        if not sel:
            return f"No {which} images produced; files: {imgs['all']}"
        return [Image(path=p) for p in sel[:6]]
    return _safe(go)


@mcp.tool()
def lv_run_vi(vi_path: str, inputs: dict = {}, outputs: list[str] = []) -> str:
    """Run a VI with front-panel inputs {label: value}; return requested output labels."""
    return _safe(lambda: json.dumps(B().run_vi(vi_path, inputs, outputs), default=str))


if __name__ == "__main__":
    mcp.run()
