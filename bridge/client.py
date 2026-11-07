"""Client for 'LVBridge Server.vi' — a mailbox loop running inside LabVIEW.

Protocol (all through ActiveX front-panel access on the running server VI):
  Python writes inputs  h (U32[]), s (String[]), n (I32[]), cmd (String), then seq (I32) last.
  The VI notices seq changed, executes cmd, writes out_h / out_s / out_n / err, then ack = seq.
Handles are VI Server refnums type-cast to U32; they stay valid while the server VI keeps running.
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path

from lvcom import LabVIEW

ROOT = Path(__file__).resolve().parent.parent
SERVER_VI = ROOT / "labview" / "LVBridge Server.vi"
CATALOG = ROOT / "bridge" / "vi_server_catalog.json"


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, server_vi=SERVER_VI, timeout=20.0):
        self.lv = LabVIEW()
        self.server_path = str(server_vi)
        self.timeout = timeout
        self.server = None
        self.seq = int(time.time()) % 1_000_000
        self._styles = None

    # ---------- server lifecycle ----------
    def ensure_server(self):
        if not os.path.exists(self.server_path):
            raise BridgeError(f"Server VI not found: {self.server_path}. Build it per BUILD_GUIDE.md.")
        if self.server is None:
            self.server = self.lv.vi(self.server_path)
        state = self.server.get("ExecState")
        if state not in (2, 3):  # 2 = Run top level, 3 = Running (subVI)
            self.server.set_control("stop", False)
            self.server.call("Run", True)  # async
            time.sleep(0.3)
        return self.server

    def stop_server(self):
        if self.server is not None:
            self.server.set_control("stop", True)

    # ---------- raw command ----------
    def cmd(self, cmd, h=(), s=(), n=()):
        srv = self.ensure_server()
        self.seq += 1
        srv.set_control("h", [int(x) for x in h] or [0])
        srv.set_control("s", [str(x) for x in s] or [""])
        srv.set_control("n", [int(x) for x in n] or [0])
        srv.set_control("cmd", cmd)
        srv.set_control("seq", self.seq)
        deadline = time.time() + self.timeout
        while srv.get_control("ack") != self.seq:
            if time.time() > deadline:
                raise BridgeError(f"Timeout waiting for '{cmd}' (is LVBridge Server.vi running?)")
            time.sleep(0.01)
        err = srv.get_control("err") or ""
        out = {
            "h": [int(x) for x in (srv.get_control("out_h") or ())],
            "s": list(srv.get_control("out_s") or ()),
            "n": [int(x) for x in (srv.get_control("out_n") or ())],
        }
        if err.strip():
            raise BridgeError(f"{cmd}: {err.strip()}")
        return out

    # ---------- high level ----------
    def new_vi(self):
        h = self.cmd("new_vi")["h"]
        return {"vi": h[0], "diagram": h[1], "panel": h[2]}

    def open_vi(self, path):
        h = self.cmd("open_vi", s=[path])["h"]
        return {"vi": h[0], "diagram": h[1], "panel": h[2]}

    def styles(self, refresh=False):
        if self._styles is None or refresh:
            out = self.cmd("styles")
            names = out["s"]
            vals = out["n"] if len(out["n"]) == len(names) else list(range(len(names)))
            self._styles = dict(zip(names, vals))
        return self._styles

    def resolve_style(self, style):
        if isinstance(style, int) or (isinstance(style, str) and style.isdigit()):
            return int(style)
        st = self.styles()
        if style in st:
            return st[style]
        low = {k.lower(): v for k, v in st.items()}
        if style.lower() in low:
            return low[style.lower()]
        cands = [k for k in st if style.lower() in k.lower()]
        if len(cands) == 1:
            return st[cands[0]]
        raise BridgeError(f"Unknown style '{style}'. Close matches: {cands[:15]}")

    def add_object(self, owner, style, x=100, y=100):
        return self.cmd("new_obj", h=[owner], n=[self.resolve_style(style), x, y])["h"][0]

    def add_subvi(self, owner, path, x=100, y=100):
        return self.cmd("new_subvi", h=[owner], s=[path], n=[0, x, y])["h"][0]

    def terminals(self, node):
        out = self.cmd("terms", h=[node])
        res = []
        for i, t in enumerate(out["h"]):
            d = {"index": i, "terminal": t}
            if i < len(out["n"]) and len(out["n"]) == len(out["h"]):
                d["is_input"] = bool(out["n"][i])
            res.append(d)
        return res

    def wire(self, term_a, term_b):
        self.cmd("wire", h=[term_a, term_b])

    def control_terminal(self, control):
        return self.cmd("ctl_term", h=[control])["h"][0]

    def set_properties(self, prop_node, id_strings):
        self.cmd("set_props", h=[prop_node], s=list(id_strings))

    CONST_MODES = {"string": 0, "dbl": 1, "double": 1, "i32": 2, "int": 2, "bool": 3, "boolean": 3}

    def set_constant(self, const, value, kind="string"):
        mode = self.CONST_MODES[kind.lower()]
        if mode == 3:
            value = 1 if str(value).lower() in ("1", "true", "t", "yes") else 0
        n1 = int(value) if mode in (2, 3) else 0
        self.cmd("set_const", h=[const], s=[str(value)], n=[mode, n1])

    def save(self, vi, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.cmd("save", h=[vi], s=[path])

    def cleanup(self, diagram):
        self.cmd("cleanup", h=[diagram])

    def close(self, ref):
        self.cmd("close", h=[ref])

    # ---------- ActiveX-only helpers (no server needed) ----------
    def export_images(self, vi_path, out_dir=None):
        """Render a saved VI's panel + diagram to image files. Returns {'panel': [...], 'diagram': [...]}."""
        out_dir = out_dir or tempfile.mkdtemp(prefix="lvimg_")
        vi = self.lv.vi(vi_path)
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", Path(vi_path).stem)
        html = os.path.join(out_dir, stem + ".html")
        # format 1 = complete documentation, imageFormat 0 = PNG, depth 24
        vi.call("PrintVIToHTML", html, False, 1, 0, 24, out_dir)
        files = sorted(Path(out_dir).glob(stem + "*"))
        imgs = [str(f) for f in files if f.suffix.lower() in (".png", ".jpg", ".gif")]
        return {
            "panel": [f for f in imgs if Path(f).stem.endswith("p")],
            "diagram": [f for f in imgs if re.search(r"d\d*$", Path(f).stem)],
            "all": imgs,
            "html": html,
        }

    def run_vi(self, vi_path, inputs=None, outputs=None):
        """Run a VI via ActiveX Call with named inputs; return named outputs."""
        vi = self.lv.vi(vi_path)
        inputs = inputs or {}
        for k, v in inputs.items():
            vi.set_control(k, v)
        vi.call("Run", False)
        return {k: vi.get_control(k) for k in (outputs or [])}


# ---------- property catalog (for set_properties) ----------
_catalog = None


def find_property(class_name, query):
    global _catalog
    if _catalog is None:
        _catalog = json.load(open(CATALOG, encoding="utf-8"))
    q = query.lower()
    hits = []
    for cls, entry in _catalog.items():
        if class_name and cls.lower() != class_name.lower():
            continue
        for p in entry.get("props", []):
            if q in str(p.get("long", "")).lower() or q in str(p.get("short", "")).lower():
                hits.append({"class": cls, "name": p["long"], "short": p.get("short"),
                             "id": p.get("uid"), "dir": {1: "read", 2: "write", 3: "read/write"}.get(p.get("dir"), p.get("dir"))})
    return hits
